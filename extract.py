#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract.py — Extract-стадия ETL для League of Legends.
========================================================

Назначение:
    Собирает сырые данные из официального Riot Games API:
    — список топ-игроков из лиг Challenger, Grandmaster, Master
    — ID последних рейтинговых матчей для каждого игрока
    — полную информацию по каждому матчу (участники, статистика, предметы, баны)

Конфигурация:
    • .env        — ТОЛЬКО секрет (RIOT_API_KEY). Без него скрипт упадёт.
    • config.yaml — все параметры пайплайна (лимиты, пути, числа игроков и т.п.).

Устойчивость к сбоям:
    • Все запросы к API идут с автоматическими ретраями (tenacity).
    • При обрыве соединения прогресс сохраняется в .txt файлы —
      повторный запуск продолжит с того же места, не перекачивая уже обработанное.
    • Данные пишутся инкрементально (дозапись в CSV каждые N матчей).

Rate Limits:
    • Riot API: 20 запросов/сек, 100 запросов/120 сек.
    • Скрипт делает паузу 1.2 сек между запросами (настраивается в config.yaml).
    • При получении 429 (Rate Limit Exceeded) — ждёт указанное время и повторяет.

Структура сбора:
    ЭТАП 1 — построение пула игроков:
        Запрашивает топ-200 (build_players_per_tier) игроков из каждой лиги
        (Challenger, Grandmaster, Master) для каждого региона (EUW, NA).
        Сохраняет в players.csv как буфер.
    
    ЭТАП 2 — выбор топ-N из пула:
        Из players.csv берёт топ-50 (top_players_per_tier) по League Points
        для каждой комбинации регион/лига. Эти игроки пойдут в загрузку матчей.
    
    ЭТАП 3 — загрузка матчей:
        Для каждого выбранного игрока запрашивает последние 100 матчей.
        По каждому матчу загружает полный JSON и извлекает:
        — участников (30+ полей статистики)
        — баны чемпионов

Выходные файлы:
    raw_data/
    ├── matches_participants.csv   — все участники всех матчей
    ├── bans.csv                   — все баны
    └── processed_players.txt      — список обработанных PUUID (для resume)
    └── processed_matches.txt      — список обработанных match_id (для resume)

Запуск:
    python extract.py
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# ИМПОРТЫ
# ═══════════════════════════════════════════════════════════
import logging                                          # логирование процесса
import os                                               # чтение переменных окружения (.env)
import sys                                              # stdout для логов
import time                                             # паузы между запросами (rate limit)
from datetime import datetime                           # форматирование дат матчей
from pathlib import Path                                # кроссплатформенная работа с путями
from typing import Dict, Iterable, List, Optional, Set   # аннотации типов

import pandas as pd                                     # DataFrame для CSV
import requests                                         # HTTP-запросы к Riot API
import yaml                                             # чтение config.yaml
from dotenv import load_dotenv                          # загрузка .env файла
from tenacity import (                                   # автоматические ретраи при сбоях
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# ═══════════════════════════════════════════════════════════
# ПУТИ И КОНСТАНТЫ
# ═══════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent             # папка где лежит этот скрипт (ELT/)
ENV_PATH = SCRIPT_DIR / ".env"                           # путь к файлу с API-ключом
CONFIG_PATH = SCRIPT_DIR / "config.yaml"                 # путь к файлу конфигурации


# ═══════════════════════════════════════════════════════════
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ═══════════════════════════════════════════════════════════
def setup_logging(level: str = "INFO") -> None:
    """
    Настраивает единый формат логов для всего скрипта.
    Пишет в stdout с временной меткой, уровнем и сообщением.
    Уровень по умолчанию INFO, можно переопределить в config.yaml.
    """
    level_name = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=level_name,
        format="%(asctime)s %(levelname)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ═══════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ API-КЛЮЧОМ
# ═══════════════════════════════════════════════════════════
def resolve_api_key() -> str:
    """
    Читает RIOT_API_KEY ИСКЛЮЧИТЕЛЬНО из файла .env.
    
    Безопасность:
        Ключ НЕ хранится в коде и НЕ попадает в репозиторий.
        Файл .env добавлен в .gitignore.
    
    Возвращает:
        Строку API-ключа.
    
    Выбрасывает:
        RuntimeError — если .env не найден или ключ пуст.
    """
    # Загружаем переменные окружения из .env (если файл существует)
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH)

    # Читаем ключ из переменной окружения
    env_key = os.getenv("RIOT_API_KEY", "").strip()
    if not env_key:
        raise RuntimeError(
            f"RIOT_API_KEY не найден в {ENV_PATH.name}. "
            "Создайте файл .env со строкой RIOT_API_KEY=<ваш_ключ>."
        )

    logging.info("API key loaded from .env")
    return env_key


# ═══════════════════════════════════════════════════════════
# ЗАГРУЗКА КОНФИГУРАЦИИ
# ═══════════════════════════════════════════════════════════
def load_config() -> dict:
    """
    Загружает все параметры из config.yaml и API-ключ из .env.
    
    config.yaml содержит:
        — rate_limit_pause: пауза между запросами (сек)
        — matches_per_player: сколько матчей запрашивать на игрока
        — build_players_per_tier: размер пула игроков на этапе 1
        — top_players_per_tier: сколько выбирать на этапе 2
        — queue_id: 420 = Ranked Solo/Duo
        — raw_data_dir: папка для выходных CSV
        — players_csv: путь к файлу с игроками
        — processed_players_file: путь к файлу прогресса по игрокам
        — processed_matches_file: путь к файлу прогресса по матчам
        — save_every: как часто сбрасывать буфер на диск (в матчах)
        — log_level: уровень логирования
    
    Все пути резолвятся относительно папки скрипта.
    """
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Читаем YAML
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    def resolve_path(path_str: str, default: str = "") -> Path:
        """
        Превращает строку пути из конфига в абсолютный путь.
        Если путь не указан — использует default относительно папки скрипта.
        """
        if not path_str:
            return script_dir / default
        return (script_dir / path_str).resolve()

    # Ключ читаем отдельно (из .env, не из конфига)
    api_key = resolve_api_key()

    return {
        "api_key": api_key,
        "rate_limit_pause": cfg.get("rate_limit_pause", 1.2),
        "matches_per_player": cfg.get("matches_per_player", 100),
        "build_players_per_tier": cfg.get("build_players_per_tier", 200),
        "top_players_per_tier": cfg.get("top_players_per_tier", 50),
        "queue_id": cfg.get("queue_id", 420),
        "raw_data_dir": resolve_path(cfg.get("raw_data_dir", "raw_data")),
        "players_csv": resolve_path(cfg.get("players_csv", "players.csv")),
        "processed_players_file": resolve_path(cfg.get("processed_players_file", "processed_players.txt")),
        "processed_matches_file": resolve_path(cfg.get("processed_matches_file", "processed_matches.txt")),
        "save_every": cfg.get("save_every", 10),
        "log_level": cfg.get("log_level", "INFO"),
    }


# ═══════════════════════════════════════════════════════════
# HTTP-ЗАПРОСЫ С АВТОМАТИЧЕСКИМИ ПОВТОРАМИ
# ═══════════════════════════════════════════════════════════
@retry(
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    wait=wait_exponential(multiplier=1, min=2, max=60),   # ждём 2, 4, 8, 16, 32 сек
    stop=stop_after_attempt(5),                            # максимум 5 попыток
    reraise=True,                                          # пробрасываем ошибку если все попытки исчерпаны
)
def safe_get(url: str, headers: Dict[str, str], timeout: int = 30) -> requests.Response:
    """
    Выполняет GET-запрос с автоматическими ретраями при ошибках.
    
    Особые случаи:
        — 429 (Rate Limit Exceeded): ждёт время из заголовка Retry-After,
          затем вызывает исключение для ретрая через tenacity.
        — Остальные ошибки (5xx, таймауты): обрабатываются tenacity автоматически.
    
    Параметры:
        url     — полный URL эндпоинта Riot API
        headers — заголовки запроса (включая X-Riot-Token)
        timeout — таймаут запроса в секундах (по умолчанию 30)
    
    Возвращает:
        Объект requests.Response с JSON-данными.
    """
    resp = requests.get(url, headers=headers, timeout=timeout)

    # Обработка Rate Limit (слишком много запросов)
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = int(retry_after) if retry_after else 5
        except ValueError:
            wait = 5
        logging.warning("Rate limit (429). Respecting Retry-After=%s s", wait)
        time.sleep(wait)
        # Вызываем исключение — tenacity перехватит и сделает ретрай
        raise requests.exceptions.RequestException("429 rate limit")

    # Для остальных ошибок (4xx, 5xx) — стандартный raise
    resp.raise_for_status()
    return resp


# ═══════════════════════════════════════════════════════════
# СОХРАНЕНИЕ ПРОГРЕССА (resume после обрыва)
# ═══════════════════════════════════════════════════════════
def load_set(path: Path) -> Set[str]:
    """
    Загружает множество строк из текстового файла.
    Каждая строка — один элемент (PUUID игрока или match_id).
    Используется для возобновления после остановки.
    """
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_set(path: Path, data: Iterable[str]) -> None:
    """
    Сохраняет множество строк в текстовый файл.
    По одной строке на элемент, в алфавитном порядке.
    Автоматически создаёт родительскую папку если нужно.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in sorted(set(data)):
            f.write(f"{item}\n")


# ═══════════════════════════════════════════════════════════
# МАППИНГ ПОЛЕЙ JSON → CSV
# ═══════════════════════════════════════════════════════════

# Какие поля извлекаем из participant JSON-объекта
# Ключ = имя колонки в CSV, Значение = путь в JSON
PARTICIPANT_FIELDS = {
    # Идентификаторы
    "puuid": "puuid",                                   # уникальный ID игрока (Player Universally Unique ID)
    "riot_game_name": "riotIdGameName",                 # имя игрока (Riot ID)
    "riot_tagline": "riotIdTagline",                    # тег игрока (#EUW и т.д.)
    "champion_name": "championName",                    # имя чемпиона (из JSON, не из справочника)
    "team_position": "teamPosition",                    # роль (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY)
    "individual_position": "individualPosition",        # уточнённая позиция
    "team_id": "teamId",                                # 100 = синяя команда, 200 = красная
    # Боевые метрики
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "win": "win",                                       # True/False — победа/поражение
    "gold_earned": "goldEarned",                        # всего золота заработано
    "minions_killed": "totalMinionsKilled",             # фарм (CS)
    "champ_level": "champLevel",                        # уровень чемпиона на конец игры
    "summoner_level": "summonerLevel",                  # уровень аккаунта игрока
    # Урон
    "damage_to_champions": "totalDamageDealtToChampions",
    "damage_taken": "totalDamageTaken",
    # Объекты
    "baron_kills": "baronKills",
    "dragon_kills": "dragonKills",
    "turret_kills": "turretKills",
    "inhibitor_kills": "inhibitorKills",
    # Обзор (vision)
    "vision_score": "visionScore",
    "wards_placed": "wardsPlaced",
    "wards_killed": "wardsKilled",
    # Особые события
    "first_blood_kill": "firstBloodKill",               # первая кровь
    "total_heal": "totalHeal",                          # всего исцелено
    "game_ended_in_surrender": "gameEndedInSurrender",   # матч закончился сдачей
    # Предметы (7 слотов: 0-6)
    "item0": "item0",
    "item1": "item1",
    "item2": "item2",
    "item3": "item3",
    "item4": "item4",
    "item5": "item5",
    "item6": "item6",
    # Заклинания призывателя
    "summoner_spell1": "summoner1Id",
    "summoner_spell2": "summoner2Id",
    # Использование способностей (Q, W, E, R)
    "spell1_casts": "spell1Casts",
    "spell2_casts": "spell2Casts",
    "spell3_casts": "spell3Casts",
    "spell4_casts": "spell4Casts",
}

# Поля уровня матча (берутся из info, не из participant)
MATCH_FIELDS = {
    "game_duration": "gameDuration",                    # длительность в секундах
    "game_version": "gameVersion",                      # версия игры (патч)
    "game_start_timestamp": "gameStartTimestamp",        # Unix timestamp начала матча (мс)
    "game_mode": "gameMode",                            # режим игры (CLASSIC, ARAM и т.д.)
    "queue_id": "queueId",                              # ID очереди (420 = Ranked Solo)
}


# ═══════════════════════════════════════════════════════════
# КЛАСС ЭКСТРАКТОРА
# ═══════════════════════════════════════════════════════════
class APIExtractor:
    """
    Основной класс экстрактора данных из Riot API.
    
    Инкапсулирует:
        — подключение к API (ключ, заголовки)
        — загрузку списков игроков (League v4)
        — загрузку матчей (Match v5)
        — инкрементальное сохранение в CSV
        — возобновление после остановки (resume)
    
    Регионы:
        EU → платформа euw1, матчи в europe
        US → платформа na1, матчи в americas
    
    Лиги:
        challenger, grandmaster, master (RANKED_SOLO_5x5)
    """

    # Маппинг регионов для разных эндпоинтов
    # Match v5 использует широкие регионы (europe, americas)
    REGION_TO_MATCH = {"EU": "europe", "US": "americas"}
    # League v4 использует платформы (euw1, na1)
    REGION_TO_PLATFORM = {"EU": "euw1", "US": "na1"}
    # Три топ-лиги для сбора
    TIERS = ["challenger", "grandmaster", "master"]

    def __init__(self, cfg: Dict[str, object]):
        """
        Инициализирует экстрактор с параметрами из конфига.
        
        Параметры:
            cfg — словарь конфигурации из load_config()
        """
        self.cfg = cfg
        self.headers = {"X-Riot-Token": cfg["api_key"]}   # заголовок авторизации
        self.rate_pause = float(cfg.get("rate_limit_pause", 1.2))
        self.matches_per_player = int(cfg.get("matches_per_player", 100))

        # Двухэтапный отбор игроков
        self.build_players_per_tier = int(cfg.get("build_players_per_tier", 200))
        self.top_players_per_tier = int(cfg.get("top_players_per_tier", 50))
        self.queue_id = int(cfg.get("queue_id", 420))

        # Пути к файлам
        self.raw_dir = Path(cfg["raw_data_dir"]).resolve()
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.players_csv = Path(cfg["players_csv"]).resolve()
        self.processed_players_path = Path(cfg["processed_players_file"]).resolve()
        self.processed_matches_path = Path(cfg["processed_matches_file"]).resolve()
        self.save_every = int(cfg.get("save_every", 10))

        # Загружаем прогресс (для resume после остановки)
        self.processed_players = load_set(self.processed_players_path)
        self.processed_matches = load_set(self.processed_matches_path)

        # Выходные CSV (перезаписываются при каждом запуске)
        self.participants_csv = self.raw_dir / "matches_participants.csv"
        self.bans_csv = self.raw_dir / "bans.csv"

        # Буферы в памяти (сбрасываются на диск каждые save_every матчей)
        self._participants_rows: List[Dict] = []
        self._bans_rows: List[Dict] = []

        logging.info("Extractor initialized. raw dir=%s | queue_id=%s",
                     str(self.raw_dir), self.queue_id)

    # ═══════════════════════════════════════════════════════
    # ЭТАП 2: ЗАГРУЗКА И ВЫБОР ТОП-N ИГРОКОВ
    # ═══════════════════════════════════════════════════════
    def load_players(self) -> pd.DataFrame:
        """
        Загружает players.csv и выбирает топ-N игроков по League Points
        для каждой комбинации (region, tier).
        
        Если файл players.csv не существует — вызывает ensure_players_csv()
        для его создания через API.
        
        Возвращает:
            DataFrame с отобранными игроками.
        """
        if not self.players_csv.exists():
            logging.info("players CSV not found at %s — fetching via League v4 API", self.players_csv)
            try:
                self.ensure_players_csv()
            except Exception as exc:
                raise FileNotFoundError(
                    f"players file not found and failed to fetch: {self.players_csv} — {exc}"
                )

        df = pd.read_csv(self.players_csv, keep_default_na=False)
        logging.info("Loaded %d players from %s", len(df), self.players_csv.name)

        # Группируем по региону и лиге, берём топ-N по LP
        selected = []
        for (region, tier), group in df.groupby(["region", "tier"]):
            top = group.sort_values("leaguePoints", ascending=False).head(self.top_players_per_tier)
            selected.append(top)
            logging.info("Selected %d players for %s/%s (from pool of %d)",
                         len(top), region, tier, len(group))

        if not selected:
            return df
        result = pd.concat(selected, ignore_index=True)
        logging.info("Total selected players: %d", len(result))
        return result

    # ═══════════════════════════════════════════════════════
    # ЭТАП 1: ПОСТРОЕНИЕ ПУЛА ИГРОКОВ
    # ═══════════════════════════════════════════════════════
    def fetch_league_entries(self, platform: str, tier: str) -> list:
        """
        Запрашивает список игроков лиги через League v4 API.
        
        Эндпоинт:
            GET /lol/league/v4/{tier}leagues/by-queue/RANKED_SOLO_5x5
        
        Параметры:
            platform — код платформы (euw1, na1)
            tier     — название лиги (challenger, grandmaster, master)
        
        Возвращает:
            Список словарей с данными игроков (summonerName, puuid, leaguePoints, и т.д.)
        """
        url = (f"https://{platform}.api.riotgames.com/lol/league/v4/"
               f"{tier}leagues/by-queue/RANKED_SOLO_5x5")
        try:
            resp = safe_get(url, self.headers)
            time.sleep(self.rate_pause)                  # соблюдаем rate limit
            return resp.json().get("entries", [])
        except Exception as exc:
            logging.warning("Failed to fetch league %s on %s: %s", tier, platform, exc)
            return []

    def ensure_players_csv(self) -> None:
        """
        Строит players.csv через API если файла нет.
        
        Собирает большой пул игроков (build_players_per_tier) из каждой лиги
        каждого региона. Это делается ОДИН раз — при последующих запусках
        extract.py читает готовый CSV и выбирает топ-N.
        
        Такой двухэтапный подход решает проблему перекоса:
        если сразу брать топ-50 из API, можно получить только игроков
        одного региона (где больше LP). А так мы сначала собираем
        широкий пул, а потом выбираем топ из него — пропорционально
        по каждому региону.
        """
        rows = []
        build_n = int(self.build_players_per_tier)

        for region_name, platform in self.REGION_TO_PLATFORM.items():
            for tier in self.TIERS:
                logging.info("Fetching %s/%s (platform=%s)", region_name, tier, platform)
                entries = self.fetch_league_entries(platform, tier)
                if not entries:
                    continue

                # Сортируем по LP (от высшего к низшему)
                entries_sorted = sorted(entries, key=lambda x: x.get("leaguePoints", 0), reverse=True)

                kept = 0
                for e in entries_sorted[:build_n]:
                    puuid = e.get("puuid", "")
                    if not puuid:
                        logging.debug("Entry without puuid skipped (%s/%s)", region_name, tier)
                        continue
                    rows.append({
                        "puuid": puuid,
                        "riot_game_name": e.get("summonerName", "") or "",
                        "riot_tagline": "",
                        "region": region_name,
                        "tier": tier.upper(),
                        "leaguePoints": e.get("leaguePoints", 0),
                        "wins": e.get("wins", 0),
                        "losses": e.get("losses", 0),
                    })
                    kept += 1
                logging.info("Kept %d players for %s/%s (pool target=%d)",
                             kept, region_name, tier, build_n)

        if not rows:
            raise RuntimeError(
                "No players fetched from Riot APIs. Проверьте: "
                "1) валидность RIOT_API_KEY, 2) поддержку League v4 ключом."
            )

        df = pd.DataFrame(rows)
        self.players_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(self.players_csv, index=False, encoding="utf-8")
        logging.info("Wrote players CSV to %s (rows=%d)", self.players_csv, len(df))

    # ═══════════════════════════════════════════════════════
    # ЭТАП 3: ЗАГРУЗКА МАТЧЕЙ
    # ═══════════════════════════════════════════════════════
    def fetch_match_ids(self, puuid: str, region: str) -> List[str]:
        """
        Запрашивает ID последних рейтинговых матчей игрока.
        
        Эндпоинт:
            GET /lol/match/v5/matches/by-puuid/{puuid}/ids
        
        Параметры:
            puuid  — уникальный ID игрока
            region — регион игрока (EU, US)
            queue  — 420 (Ranked Solo/Duo)
            type   — ranked
            count  — сколько матчей запрашивать (из конфига)
        
        Возвращает:
            Список match_id (строки вида "EUW1_1234567890").
        """
        match_region = self.REGION_TO_MATCH.get(region)
        if not match_region:
            logging.warning("Region %s is not supported", region)
            return []

        url = (f"https://{match_region}.api.riotgames.com/lol/match/v5/matches/"
               f"by-puuid/{puuid}/ids"
               f"?queue={self.queue_id}&type=ranked&count={self.matches_per_player}")
        try:
            resp = safe_get(url, self.headers)
            time.sleep(self.rate_pause)
            ids = resp.json()
            if not isinstance(ids, list):
                logging.warning("Unexpected match ids response for %s: %s", puuid, type(ids))
                return []
            return ids
        except Exception as exc:
            logging.error("Failed to fetch match ids for %s: %s", puuid, exc)
            return []

    def fetch_match_json(self, match_region: str, match_id: str) -> Optional[Dict]:
        """
        Загружает полный JSON матча по его ID.
        
        Эндпоинт:
            GET /lol/match/v5/matches/{matchId}
        
        Параметры:
            match_region — широкий регион (europe, americas)
            match_id     — ID матча (например "EUW1_1234567890")
        
        Возвращает:
            Полный JSON матча как словарь, или None при ошибке.
        """
        url = f"https://{match_region}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        try:
            resp = safe_get(url, self.headers)
            time.sleep(self.rate_pause)
            return resp.json()
        except Exception as exc:
            logging.error("Failed to fetch match %s: %s", match_id, exc)
            return None

    def _process_match_for_csv(self, match_id: str, region: str, data: Dict) -> bool:
        """
        Извлекает данные участников и банов из JSON матча в буферы.
        
        Возвращает:
            True — матч обработан (очередь совпадает),
            False — матч пропущен (другая очередь).
        """
        info = data.get("info", {})

        # Проверяем что матч из нужной очереди (Ranked Solo/Duo)
        if info.get("queueId") != self.queue_id:
            logging.debug("Skip match %s: queueId=%s != %s",
                          match_id, info.get("queueId"), self.queue_id)
            return False

        teams = info.get("teams", [])
        participants = info.get("participants", [])

        # Дата матча из timestamp (мс → datetime → строка YYYY-MM-DD)
        ts = info.get("gameStartTimestamp")
        match_date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""

        # ── Извлекаем баны ──
        for team in teams:
            team_id = team.get("teamId")
            for ban in team.get("bans", []):
                self._bans_rows.append({
                    "match_id": match_id,
                    "match_region": region,
                    "team_id": team_id,
                    "champion_id": ban.get("championId"),
                    "pick_turn": ban.get("pickTurn"),
                })

        # ── Извлекаем участников ──
        for p in participants:
            # Копируем поля участника по маппингу
            row = {k: p.get(v) for k, v in PARTICIPANT_FIELDS.items()}
            # Добавляем поля уровня матча
            for our, src in MATCH_FIELDS.items():
                row[our] = info.get(src)
            row["match_id"] = match_id
            row["region"] = region
            row["match_date"] = match_date
            self._participants_rows.append(row)

        return True

    def _flush_progress(self) -> None:
        """
        Сбрасывает накопленные в памяти буферы на диск.
        
        Использует инкрементальную дозапись:
        — Первый раз создаёт CSV с заголовками.
        — Последующие разы дописывает строки без заголовков.
        
        Вызывается:
        — Каждые save_every матчей (из конфига).
        — В конце работы (финальный сброс).
        """
        if self._participants_rows:
            df = pd.DataFrame(self._participants_rows)
            if not self.participants_csv.exists():
                # Первая запись — с заголовками
                df.to_csv(self.participants_csv, index=False, mode="w", encoding="utf-8")
            else:
                # Дозапись — без заголовков
                df.to_csv(self.participants_csv, index=False, mode="a", header=False, encoding="utf-8")
            self._participants_rows = []

        if self._bans_rows:
            dfb = pd.DataFrame(self._bans_rows)
            if not self.bans_csv.exists():
                dfb.to_csv(self.bans_csv, index=False, mode="w", encoding="utf-8")
            else:
                dfb.to_csv(self.bans_csv, index=False, mode="a", header=False, encoding="utf-8")
            self._bans_rows = []

    # ═══════════════════════════════════════════════════════
    # ГЛАВНЫЙ ЦИКЛ ЭКСТРАКЦИИ
    # ═══════════════════════════════════════════════════════
    def run(self) -> None:
        """
        Главный цикл: для каждого отобранного игрока загружает его матчи.
        
        Порядок обработки:
        1. Загружает список игроков (load_players)
        2. Для каждого игрока:
           a. Проверяет не обработан ли он уже (resume)
           b. Запрашивает ID его матчей
           c. Для каждого нового матча загружает JSON и извлекает данные
           d. Каждые N матчей сбрасывает буфер на диск
        3. В конце — финальный сброс и статистика
        """
        players_df = self.load_players()
        start_ts = time.time()
        total_new_matches = 0
        skipped_non_420 = 0                           # счётчик матчей не из Ranked Solo

        for idx, player in players_df.iterrows():
            puuid = player.get("puuid")
            region = player.get("region")
            name = player.get("riot_game_name") or (puuid[:16] if puuid else "unknown")

            # Пропускаем игроков без PUUID
            if not puuid:
                logging.warning("Skipping player with empty puuid: %s", player.to_dict())
                continue

            # Проверяем resume — не обработан ли уже этот игрок
            if puuid in self.processed_players:
                logging.debug("Skipping already processed player %s", puuid)
                continue

            logging.info("Processing player %d/%d: %s (%s)",
                         idx + 1, len(players_df), name, region)

            # Определяем широкий регион для Match v5 API
            match_region = self.REGION_TO_MATCH.get(region)
            if not match_region:
                logging.warning("Unsupported region %s for player %s", region, puuid)
                self.processed_players.add(puuid)
                save_set(self.processed_players_path, self.processed_players)
                continue

            # Получаем ID матчей игрока
            match_ids = self.fetch_match_ids(puuid, region)
            if not match_ids:
                logging.info("No matches found for %s", name)
                self.processed_players.add(puuid)
                save_set(self.processed_players_path, self.processed_players)
                continue

            processed_for_player = 0

            # Обрабатываем каждый матч
            for match_id in match_ids:
                # Проверяем resume — не обработан ли уже этот матч
                if match_id in self.processed_matches:
                    logging.debug("Skip already processed match %s", match_id)
                    continue

                # Загружаем JSON матча
                data = self.fetch_match_json(match_region, match_id)
                if not data:
                    continue

                # Извлекаем данные в буферы
                ok = self._process_match_for_csv(match_id, region, data)
                if not ok:
                    skipped_non_420 += 1

                self.processed_matches.add(match_id)
                processed_for_player += 1
                total_new_matches += 1

                # Периодический сброс на диск (каждые save_every матчей)
                if total_new_matches % self.save_every == 0:
                    self._flush_progress()
                    save_set(self.processed_matches_path, self.processed_matches)
                    logging.info("Progress flushed. Total new matches: %d", total_new_matches)

            # Игрок обработан — сохраняем прогресс
            self.processed_players.add(puuid)
            save_set(self.processed_players_path, self.processed_players)
            logging.info("Player %s done: %d new matches", name, processed_for_player)

        # Финальный сброс буферов
        self._flush_progress()
        save_set(self.processed_matches_path, self.processed_matches)
        save_set(self.processed_players_path, self.processed_players)

        # Итоговая статистика
        elapsed = time.time() - start_ts
        logging.info("=" * 60)
        logging.info("Extraction finished.")
        logging.info("New matches processed : %d", total_new_matches)
        logging.info("Skipped (wrong queue) : %d", skipped_non_420)
        logging.info("Participants CSV      : %s", self.participants_csv)
        logging.info("Bans CSV              : %s", self.bans_csv)
        logging.info("Elapsed time          : %.1f s", elapsed)
        logging.info("=" * 60)


# ═══════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ═══════════════════════════════════════════════════════════
def main() -> None:
    """
    Главная функция запуска extract-стадии.
    
    Порядок:
        1. Настраивает логирование (уровень из config.yaml).
        2. Загружает конфигурацию и API-ключ (load_config).
        3. Создаёт экземпляр APIExtractor и запускает сбор данных.
    """
    # 1) Определяем уровень логирования из config.yaml (если файл существует)
    pre_level = "INFO"
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            pre_level = (yaml.safe_load(f) or {}).get("log_level", "INFO")
    setup_logging(pre_level)

    logging.info("Starting extract stage")

    # 2) Загружаем конфиг (включая чтение API-ключа из .env)
    cfg = load_config()
    logging.info(
        "Config: build_pool=%d/tier, select=%d/tier, queue_id=%d, matches/player=%d",
        cfg["build_players_per_tier"], cfg["top_players_per_tier"],
        cfg["queue_id"], cfg["matches_per_player"],
    )

    # 3) Создаём экстрактор и запускаем
    extractor = APIExtractor(cfg)
    extractor.run()


if __name__ == "__main__":
    main()