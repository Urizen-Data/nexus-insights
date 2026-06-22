#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load.py — объединённый TRANSFORM + LOAD для ETL-пайплайна LoL.
================================================================

Назначение:
    Принимает сырые CSV-файлы, созданные на этапе extract.py,
    очищает их, нормализует, конвертирует в Parquet и загружает в DuckDB.

Этапы работы:
    1. Читает сырые CSV из raw_data/
       — matches_participants_*.csv (участники матчей + метаданные)
       — bans_*.csv (баны чемпионов)
       — players.csv (топ-игроки лиг)
    
    2. Очищает и нормализует (transform)
       — Приводит типы колонок (строки, числа, булевы значения)
       — Разделяет объединённую таблицу на match_info и participants
       — Конвертирует timestamp в дату матча
    
    3. Сохраняет Parquet в parquet_folder/
       — match_info.parquet, participants.parquet, players.parquet, bans.parquet
       — Все справочники Data Dragon (champions, items, spells, icons)
    
    4. Загружает ВСЕ Parquet в DuckDB (lol.duckdb)
       — Каждый .parquet файл становится таблицей
       — Таблицы создаются в схеме main

Входные данные:
    raw_data/
    ├── matches_participants.csv    — сырые данные участников (от extract.py)
    ├── bans.csv                    — сырые данные банов (от extract.py)
    └── players.csv                 — данные игроков (от extract.py)
    
    data/                           — справочники Data Dragon
    ├── champions.csv
    ├── items.csv
    ├── spells.csv
    ├── icons.csv
    └── version.txt

Выходные данные:
    parquet_folder/
    ├── match_info.parquet          — уникальная информация о матчах
    ├── participants.parquet        — данные участников (очищенные)
    ├── players.parquet             — данные игроков
    ├── bans.parquet                — баны (очищенные)
    ├── champions.parquet           — справочник чемпионов
    ├── items.parquet               — справочник предметов
    ├── spells.parquet              — справочник заклинаний
    ├── icons.parquet               — справочник иконок
    ├── version.txt                 — версия Data Dragon
    └── lol.duckdb                  — база данных со всеми таблицами

Запуск:
    python load.py
"""
from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# ИМПОРТЫ
# ═══════════════════════════════════════════════════════════
import logging                                          # логирование процесса
import shutil                                           # копирование файлов (version.txt)
import sys                                              # stdout для логов
from pathlib import Path                                # кроссплатформенная работа с путями
from typing import Optional                             # аннотации типов

import pandas as pd                                     # DataFrame для обработки данных
import yaml                                             # чтение config.yaml
import duckdb                                           # DuckDB для создания базы данных


# ============================================================
# КОНФИГУРАЦИЯ — загрузка параметров из config.yaml
# ============================================================

def load_config() -> dict:
    """
    Загружает все пути и параметры из config.yaml.
    
    Возвращает словарь с ключами:
        raw_data_dir       — папка с сырыми CSV (от extract.py)
        parquet_output_dir — папка для готовых Parquet-файлов
        processed_csv_dir  — папка для очищенных CSV (опционально, для отладки)
        data_dragon_dir    — папка со справочниками Data Dragon
        players_csv        — путь к файлу players.csv
        duckdb_path        — путь к файлу базы данных DuckDB
        log_level          — уровень логирования
    
    Все пути резолвятся относительно папки скрипта.
    """
    script_dir = Path(__file__).resolve().parent           # папка где лежит load.py (ELT/)
    config_path = script_dir / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # Читаем YAML-конфиг
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

    # Резолвим все пути из конфига
    raw_data_dir = resolve_path(cfg.get("raw_data_dir", "raw_data"))
    parquet_output_dir = resolve_path(cfg.get("parquet_output_dir", "parquet_folder"))
    processed_csv_dir = resolve_path(cfg.get("processed_csv_dir", "processed_csv"))
    data_dragon_dir = resolve_path(cfg.get("data_dragon_dir", "data_dragon"))
    players_csv = resolve_path(cfg.get("players_csv", "players.csv"))
    duckdb_path = resolve_path(cfg.get("duckdb_path", "parquet_folder/lol.duckdb"))

    # Создаём все нужные папки заранее
    raw_data_dir.mkdir(parents=True, exist_ok=True)
    parquet_output_dir.mkdir(parents=True, exist_ok=True)
    processed_csv_dir.mkdir(parents=True, exist_ok=True)
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    return {
        "raw_data_dir": raw_data_dir,
        "parquet_output_dir": parquet_output_dir,
        "processed_csv_dir": processed_csv_dir,
        "data_dragon_dir": data_dragon_dir,
        "players_csv": players_csv,
        "duckdb_path": duckdb_path,
        "log_level": cfg.get("log_level", "INFO"),
    }


def setup_logging(level: str = "INFO") -> None:
    """
    Настраивает единый формат логов для всего скрипта.
    Пишет в stdout с временной меткой, уровнем и сообщением.
    """
    level_name = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=level_name,
        format="%(asctime)s %(levelname)s — %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def _latest_file(directory: Path, pattern: str) -> Optional[Path]:
    """
    Находит последний (по алфавиту) файл, matching pattern.
    Используется для поиска matches_participants_YYYYMMDD.csv —
    берётся самый свежий по дате в имени файла.
    
    Параметры:
        directory — папка для поиска
        pattern   — glob-паттерн (например "matches_participants_*.csv")
    
    Возвращает:
        Path к последнему файлу или None если ничего не найдено.
    """
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def _to_bool(series: pd.Series) -> pd.Series:
    """
    Приводит колонку к булеву типу (bool).
    Обрабатывает разные форматы: True/False, true/false, 1/0, строки.
    """
    return (
        series.astype(str).str.strip().str.lower()         # все значения → строки в нижнем регистре
        .map({"true": True, "false": False, "1": True, "0": False})  # маппинг строк в bool
        .fillna(False)                                      # NaN → False
        .astype(bool)                                       # финальный тип
    )


def _to_int(series: pd.Series) -> pd.Series:
    """
    Приводит колонку к целочисленному типу (int64).
    Некорректные значения → 0.
    """
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


def _to_str(series: pd.Series) -> pd.Series:
    """
    Приводит колонку к строковому типу.
    NaN и 'nan' → пустая строка.
    """
    return series.astype(str).where(series.notna(), "").replace({"nan": ""})


def _write_parquet(df: pd.DataFrame, out_path: Path) -> None:
    """
    Сохраняет DataFrame в формат Parquet.
    
    При ошибке (например несовместимость типов) делает fallback:
    сохраняет во временный CSV и конвертирует через DuckDB.
    
    Параметры:
        df       — DataFrame для сохранения
        out_path — путь к выходному .parquet файлу
    """
    try:
        # Основной путь: pandas → parquet (сохраняет типы колонок)
        df.to_parquet(out_path, index=False)
        logging.info("Записан parquet: %s (%d строк, %d колонок)",
                     out_path.name, len(df), df.shape[1])
    except Exception as exc:
        # Fallback: через DuckDB (более tolerant к типам)
        logging.warning("to_parquet не сработал (%s) для %s — пробуем DuckDB", exc, out_path)
        tmp_csv = out_path.with_suffix(".tmp.csv")
        df.to_csv(tmp_csv, index=False, encoding="utf-8")
        con = duckdb.connect(database=":memory:")
        con.execute(
            f"COPY (SELECT * FROM read_csv_auto('{tmp_csv.as_posix()}')) "
            f"TO '{out_path.as_posix()}' (FORMAT PARQUET)"
        )
        con.close()
        if tmp_csv.exists():
            tmp_csv.unlink()                               # удаляем временный CSV
        logging.info("Записан parquet через DuckDB: %s", out_path)


# ============================================================
# КОНТРАКТ КОЛОНОК — какие типы у каких полей
# ============================================================

# ── Строковые колонки уровня матча ──
MATCH_STRING_COLS = ["match_id", "region", "game_version", "game_mode", "match_date"]

# ── Числовые колонки уровня матча ──
MATCH_NUMERIC_COLS = ["game_duration", "game_start_timestamp", "queue_id"]

# ── Все колонки таблицы match_info (в правильном порядке) ──
MATCH_COLS = [
    "match_id", "region", "game_duration", "game_version",
    "game_start_timestamp", "game_mode", "queue_id", "match_date",
]

# ── Строковые колонки участника ──
PARTICIPANT_STRING_COLS = [
    "puuid", "riot_game_name", "riot_tagline", "champion_name",
    "team_position", "individual_position",
]

# ── Булевы колонки участника ──
PARTICIPANT_BOOL_COLS = ["win", "first_blood_kill", "game_ended_in_surrender"]

# ── Числовые колонки участника (30+ полей) ──
PARTICIPANT_NUMERIC_COLS = [
    "team_id", "kills", "deaths", "assists", "gold_earned",
    "minions_killed", "champ_level", "summoner_level",
    "damage_to_champions", "damage_taken", "baron_kills",
    "dragon_kills", "turret_kills", "inhibitor_kills",
    "vision_score", "wards_placed", "wards_killed", "total_heal",
    "item0", "item1", "item2", "item3", "item4", "item5", "item6",
    "summoner_spell1", "summoner_spell2",
    "spell1_casts", "spell2_casts", "spell3_casts", "spell4_casts",
]

# ── Числовые колонки банов ──
BANS_NUMERIC_COLS = ["team_id", "champion_id", "pick_turn"]


# ============================================================
# ОЧИСТКА И НОРМАЛИЗАЦИЯ ДАННЫХ
# ============================================================

def clean_participants(df: pd.DataFrame) -> pd.DataFrame:
    """
    Очищает объединённую таблицу участников (participants + поля матча).
    
    Что делает:
        — Приводит строковые колонки к str (пустые вместо NaN)
        — Приводит булевы колонки к bool
        — Приводит числовые колонки к int64
        — Конвертирует game_start_timestamp (мс) → match_date (datetime)
    
    Параметры:
        df — сырой DataFrame из matches_participants.csv
    
    Возвращает:
        Очищенный DataFrame с правильными типами колонок.
    """
    df = df.copy()

    # 1) Строковые колонки: NaN → "", все значения → str
    for c in PARTICIPANT_STRING_COLS + MATCH_STRING_COLS:
        if c in df.columns and c != "match_date":        # match_date формируется отдельно
            df[c] = _to_str(df[c])

    # 2) Булевы колонки: True/False/1/0 → bool
    for c in PARTICIPANT_BOOL_COLS:
        if c in df.columns:
            df[c] = _to_bool(df[c])

    # 3) Числовые колонки участника: некорректные → 0, тип int64
    for c in PARTICIPANT_NUMERIC_COLS:
        if c in df.columns:
            df[c] = _to_int(df[c])

    # 4) Числовые колонки матча: некорректные → 0, тип int64
    for c in MATCH_NUMERIC_COLS:
        if c in df.columns:
            df[c] = _to_int(df[c])

    # 5) Конвертация timestamp → дата
    # game_start_timestamp в миллисекундах → делим на 1000 → datetime → только дата
    if "game_start_timestamp" in df.columns:
        ts = pd.to_numeric(df["game_start_timestamp"], errors="coerce")
        df["match_date"] = pd.to_datetime(ts / 1000, unit="s", errors="coerce").dt.normalize()

    return df


def clean_bans(df: pd.DataFrame) -> pd.DataFrame:
    """
    Очищает таблицу банов.
    
    Что делает:
        — match_id и match_region → строки
        — team_id, champion_id, pick_turn → целые числа
    
    Параметры:
        df — сырой DataFrame из bans.csv
    
    Возвращает:
        Очищенный DataFrame.
    """
    df = df.copy()
    for c in ["match_id", "match_region"]:
        if c in df.columns:
            df[c] = _to_str(df[c])
    for c in BANS_NUMERIC_COLS:
        if c in df.columns:
            df[c] = _to_int(df[c])
    return df


def split_match_and_participants(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Разделяет объединённую таблицу на две нормализованные:
    
    1. match_info    — уникальная информация о матчах (1 строка = 1 матч)
       Колонки: match_id, region, game_duration, game_version,
                game_start_timestamp, game_mode, queue_id, match_date
    
    2. participants  — данные участников (10 строк на матч)
       Все колонки участника + match_id для связи
    
    Это убирает дублирование данных матча (раньше они повторялись
    для каждого из 10 участников).
    
    Параметры:
        df — очищенный DataFrame из clean_participants()
    
    Возвращает:
        Кортеж (match_info, participants).
    """
    # Берём только колонки матча, удаляем дубликаты по match_id
    match_cols = [c for c in MATCH_COLS if c in df.columns]
    match_info = (
        df[match_cols]
        .drop_duplicates(subset="match_id")
        .reset_index(drop=True)
    )

    # Убираем колонки матча из participants (кроме match_id для связи)
    drop_cols = [c for c in MATCH_COLS if c in df.columns and c != "match_id"]
    participants = df.drop(columns=drop_cols).reset_index(drop=True)

    logging.info(
        "Нормализация -> match_info: %d строк (%d кол.) | participants: %d строк (%d кол.)",
        len(match_info), match_info.shape[1], len(participants), participants.shape[1],
    )
    return match_info, participants


# ============================================================
# ЗАГРУЗКА PARQUET В DUCKDB
# ============================================================

def load_parquet_to_duckdb(conn: duckdb.DuckDBPyConnection, parquet_path: Path, table_name: str) -> None:
    """
    Загружает один Parquet-файл в DuckDB как таблицу.
    
    Если таблица с таким именем уже существует — удаляет и создаёт заново.
    Имя таблицы = имя файла без расширения .parquet.
    
    Параметры:
        conn       — соединение с DuckDB
        parquet_path — путь к .parquet файлу
        table_name — имя создаваемой таблицы
    """
    # Удаляем старую таблицу если есть
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    # Создаём новую из Parquet
    conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_parquet('{parquet_path}')")
    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    logging.info("  ✓ DuckDB таблица %s: %d строк", table_name, count)


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def main() -> None:
    """
    Главная функция: оркестрирует весь процесс transform + load.
    
    Порядок работы:
    1. Загружает конфигурацию
    2. Ищет сырые CSV в raw_data/
    3. Очищает и нормализует participants, bans, players
    4. Сохраняет всё в Parquet
    5. Копирует справочники Data Dragon
    6. Загружает все Parquet в DuckDB
    """
    cfg = load_config()
    setup_logging(cfg["log_level"])

    logging.info("=" * 60)
    logging.info("TRANSFORM + LOAD: CSV → Parquet → DuckDB")
    logging.info("=" * 60)

    raw_data_dir = cfg["raw_data_dir"]
    parquet_dir = cfg["parquet_output_dir"]
    processed_csv_dir = cfg["processed_csv_dir"]
    data_dragon_dir = cfg["data_dragon_dir"]
    players_csv = cfg["players_csv"]
    duckdb_path = cfg["duckdb_path"]

    logging.info(f"📂 raw_data:     {raw_data_dir}")
    logging.info(f"📁 parquet:      {parquet_dir}")
    logging.info(f"💾 duckdb:       {duckdb_path}")

    # ── Ищем входные CSV-файлы ──
    # participants: матчит и "matches_participants.csv", и "matches_participants_YYYYMMDD.csv"
    participants_src = _latest_file(raw_data_dir, "matches_participants*.csv")
    # bans: матчит и "bans.csv", и "bans_YYYYMMDD.csv"
    bans_src = _latest_file(raw_data_dir, "bans*.csv")

    if not participants_src:
        logging.error(f"❌ Не найден CSV участников в {raw_data_dir}")
        return

    # ═══════════════════════════════════════════════════════
    # ШАГ 1: Обработка участников
    # ═══════════════════════════════════════════════════════
    logging.info(f"📄 Загрузка: {participants_src.name}")
    parts = pd.read_csv(participants_src)                  # читаем сырой CSV
    # Дедупликация: extract.py дозаписывает в CSV (mode="a"), при перезапуске возможны дубли.
    # Уникальность строки участника = (match_id, puuid).
    before = len(parts)
    parts = parts.drop_duplicates(subset=["match_id", "puuid"]).reset_index(drop=True)
    if before != len(parts):
        logging.info("🧹 Удалено дублей участников: %d (было %d, стало %d)",
                     before - len(parts), before, len(parts))
    parts_clean = clean_participants(parts)                 # очищаем и приводим типы
    # Сохраняем очищенную версию для отладки
    parts_clean.to_csv(processed_csv_dir / "participants_cleaned.csv", index=False)

    # Разделяем на две нормализованные таблицы
    match_info, participants = split_match_and_participants(parts_clean)
    _write_parquet(match_info, parquet_dir / "match_info.parquet")
    _write_parquet(participants, parquet_dir / "participants.parquet")

    # ═══════════════════════════════════════════════════════
    # ШАГ 2: Обработка игроков
    # ═══════════════════════════════════════════════════════
    if players_csv.exists():
        logging.info(f"📄 Загрузка игроков: {players_csv}")
        players = pd.read_csv(players_csv, keep_default_na=False)
        players.to_csv(processed_csv_dir / "players_cleaned.csv", index=False)
        _write_parquet(players, parquet_dir / "players.parquet")
    else:
        logging.warning(f"⚠️ Не найден {players_csv}")

    # ═══════════════════════════════════════════════════════
    # ШАГ 3: Обработка банов
    # ═══════════════════════════════════════════════════════
    if bans_src:
        logging.info(f"📄 Загрузка банов: {bans_src.name}")
        bans = pd.read_csv(bans_src)
        bans_clean = clean_bans(bans)
        bans_clean.to_csv(processed_csv_dir / "bans_cleaned.csv", index=False)
        _write_parquet(bans_clean, parquet_dir / "bans.parquet")
    else:
        logging.warning("⚠️ Не найден CSV банов")

    # ═══════════════════════════════════════════════════════
    # ШАГ 4: Копирование справочников Data Dragon
    # ═══════════════════════════════════════════════════════
    if data_dragon_dir.exists():
        logging.info("📚 Обработка справочников Data Dragon из %s", data_dragon_dir)
        # Конвертируем ВСЕ CSV из папки Data Dragon в Parquet
        for csv_file in data_dragon_dir.glob("*.csv"):
            parquet_name = csv_file.stem + ".parquet"
            try:
                logging.info(f"📄 Конвертация: {csv_file.name}")
                df_dd = pd.read_csv(csv_file, keep_default_na=False)
                _write_parquet(df_dd, parquet_dir / parquet_name)
            except Exception as exc:
                logging.warning(f"Не удалось сконвертировать {csv_file.name}: {exc}")

        # Копируем version.txt — чтобы знать версию справочников
        version_file = data_dragon_dir / "version.txt"
        if version_file.exists():
            shutil.copy2(version_file, parquet_dir / "version.txt")
            logging.info("📄 version.txt скопирован в %s", parquet_dir)
    else:
        logging.warning(f"⚠️ Папка Data Dragon не найдена: {data_dragon_dir}")

    # ═══════════════════════════════════════════════════════
    # ШАГ 5: Загрузка ВСЕХ Parquet в DuckDB
    # ═══════════════════════════════════════════════════════
    logging.info("-" * 40)
    logging.info("💾 Загрузка Parquet в DuckDB...")
    conn = duckdb.connect(str(duckdb_path))

    # Все .parquet файлы в выходной папке → таблицы в DuckDB
    # Имя таблицы = имя файла без расширения
    for parquet_file in sorted(parquet_dir.glob("*.parquet")):
        table_name = parquet_file.stem                     # "match_info", "participants", ...
        load_parquet_to_duckdb(conn, parquet_file, table_name)

    conn.close()

    # ═══════════════════════════════════════════════════════
    # ИТОГИ
    # ═══════════════════════════════════════════════════════
    logging.info("=" * 60)
    logging.info("✅ TRANSFORM + LOAD завершён")
    logging.info(f"📁 Parquet: {parquet_dir}")
    for f in sorted(parquet_dir.glob("*")):
        if f.is_file():
            size_kb = f.stat().st_size / 1024
            logging.info(f"   • {f.name} ({size_kb:.0f} KB)")
    logging.info(f"💾 DuckDB:  {duckdb_path}")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()