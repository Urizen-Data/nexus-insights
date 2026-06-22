#!/usr/bin/env python3
"""
Загрузчик статических справочных данных League of Legends из Data Dragon.

Скачивает справочники (чемпионы, предметы, заклинания призывателя, иконки профиля)
и сохраняет их в виде CSV-файлов в каталог, указанный в config.yaml (data_dragon_dir).

Также записывается version.txt, чтобы не качать данные повторно без необходимости.

Запуск:
    python data_dragon.py           # скачать только если версия изменилась
    python data_dragon.py --force   # принудительно перекачать всё заново
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import requests
import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Конфигурация из config.yaml
# ---------------------------------------------------------------------------

def load_output_dir() -> Path:
    """Читает data_dragon_dir из config.yaml, иначе возвращает 'data' рядом со скриптом."""
    script_dir = Path(__file__).resolve().parent
    config_path = script_dir / "config.yaml"

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            data_dir = cfg.get("data_dragon_dir", "data_dragon")
            # Превращаем в абсолютный путь относительно папки скрипта
            return (script_dir / data_dir).resolve()
        except Exception:
            pass

    return script_dir / "data_dragon"


DATA_DIR = load_output_dir()
VERSION_FILE = DATA_DIR / "version.txt"

# Базовый адрес Data Dragon.
DDRAGON_BASE = "https://ddragon.leagueoflegends.com"

# Локаль по умолчанию (язык текстов: названия, описания, lore).
DEFAULT_LOCALE = "en_US"

# Сетевые настройки.
REQUEST_TIMEOUT = 60       # секунд на один запрос
REQUEST_RETRIES = 5        # количество попыток на один URL
REQUEST_RETRY_DELAY = 2.0  # пауза между попытками в секундах

# Единая HTTP-сессия (переиспользует TCP-соединение -> быстрее и стабильнее).
SESSION = requests.Session()


# ---------------------------------------------------------------------------
# Базовая функция загрузки JSON с повторными попытками
# ---------------------------------------------------------------------------

def get_json(url: str) -> dict | list:
    """
    Загружает JSON по указанному URL с повторными попытками.
    """
    last_exc: Exception | None = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            logging.warning(
                "Запрос не удался (попытка %s/%s) %s: %s",
                attempt, REQUEST_RETRIES, url, exc,
            )
            if attempt < REQUEST_RETRIES:
                time.sleep(REQUEST_RETRY_DELAY)

    raise RuntimeError(
        f"Не удалось загрузить URL после {REQUEST_RETRIES} попыток: {url}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Версия Data Dragon
# ---------------------------------------------------------------------------

def get_latest_version() -> str:
    """Возвращает последнюю доступную версию Data Dragon."""
    url = f"{DDRAGON_BASE}/api/versions.json"
    versions = get_json(url)
    return versions[0]


# ---------------------------------------------------------------------------
# Чемпионы
# ---------------------------------------------------------------------------

def load_champion_lore(version: str, champion_key: str, locale: str = DEFAULT_LOCALE) -> str:
    """Загружает lore-описание чемпиона."""
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{locale}/champion/{champion_key}.json"
    data = get_json(url)["data"][champion_key]
    return data.get("lore", "")


def load_champions(version: str, locale: str = DEFAULT_LOCALE) -> pd.DataFrame:
    """Загружает список чемпионов с полными описаниями."""
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{locale}/champion.json"
    data = get_json(url)["data"]

    total = len(data)
    rows = []

    for index, (champion_key, info) in enumerate(data.items(), start=1):
        name = info.get("name", champion_key)
        logging.info("Загрузка lore чемпиона [%s/%s]: %s", index, total, name)
        champion_lore = load_champion_lore(version, champion_key, locale)

        rows.append({
            "champion_id": int(info["key"]),
            "champion_name": info["name"],
            "champion_title": info.get("title", ""),
            "champion_lore": champion_lore,
            "champion_tags": ",".join(info.get("tags", [])),
            "champion_image": f"{version}/img/champion/{info['image']['full']}",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Предметы
# ---------------------------------------------------------------------------

def load_items(version: str, locale: str = DEFAULT_LOCALE) -> pd.DataFrame:
    """Загружает справочник предметов."""
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{locale}/item.json"
    data = get_json(url)["data"]

    rows = []
    for item_id, info in data.items():
        gold = info.get("gold", {})
        image = info.get("image")
        rows.append({
            "item_id": int(item_id),
            "item_name": info.get("name", ""),
            "item_gold_total": gold.get("total", 0),
            "item_gold_sell": gold.get("sell", 0),
            "item_tags": ",".join(info.get("tags", [])),
            "item_image": f"{version}/img/item/{image['full']}" if image else "",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Заклинания призывателя
# ---------------------------------------------------------------------------

def load_summoner_spells(version: str, locale: str = DEFAULT_LOCALE) -> pd.DataFrame:
    """Загружает справочник заклинаний."""
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{locale}/summoner.json"
    data = get_json(url)["data"]

    rows = []
    for _name, info in data.items():
        cooldown = info.get("cooldown") or [0]
        image = info.get("image")
        rows.append({
            "spell_id": int(info.get("key", 0)),
            "spell_name": info.get("name", ""),
            "spell_description": info.get("description", ""),
            "spell_cooldown": cooldown[0],
            "spell_image": f"{version}/img/spell/{image['full']}" if image else "",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Иконки профиля
# ---------------------------------------------------------------------------

def load_profile_icons(version: str, locale: str = DEFAULT_LOCALE) -> pd.DataFrame:
    """Загружает иконки профиля."""
    url = f"{DDRAGON_BASE}/cdn/{version}/data/{locale}/profileicon.json"
    data = get_json(url)["data"]

    rows = []
    for icon_id, info in data.items():
        image = info.get("image")
        rows.append({
            "icon_id": int(icon_id),
            "icon_image": f"{version}/img/profileicon/{image['full']}" if image else "",
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Сохранение всех данных
# ---------------------------------------------------------------------------

def save_all(version: str, out_dir: Path, locale: str = DEFAULT_LOCALE) -> None:
    """Скачивает все справочники и сохраняет их в CSV."""
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.info("Скачивание статических данных Data Dragon, версия %s", version)

    champions = load_champions(version, locale=locale)
    items = load_items(version, locale=locale)
    spells = load_summoner_spells(version, locale=locale)
    icons = load_profile_icons(version, locale=locale)

    champions.to_csv(out_dir / "champions.csv", index=False, encoding="utf-8")
    items.to_csv(out_dir / "items.csv", index=False, encoding="utf-8")
    spells.to_csv(out_dir / "spells.csv", index=False, encoding="utf-8")
    icons.to_csv(out_dir / "icons.csv", index=False, encoding="utf-8")

    logging.info(
        "Сохранено: чемпионов=%s, предметов=%s, заклинаний=%s, иконок=%s",
        len(champions), len(items), len(spells), len(icons),
    )

    VERSION_FILE.write_text(version, encoding="utf-8")
    logging.info("CSV-файлы Data Dragon сохранены в %s", out_dir)


def read_saved_version(out_dir: Path = DATA_DIR) -> str | None:
    """Читает сохранённую версию."""
    version_file = out_dir / "version.txt"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Загрузчик статических справочных данных LoL из Data Dragon."
    )
    parser.add_argument("--force", action="store_true",
                        help="Принудительно перекачать все файлы.")
    parser.add_argument("--locale", default=DEFAULT_LOCALE,
                        help=f"Локаль данных (по умолчанию {DEFAULT_LOCALE}).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")

    try:
        latest = get_latest_version()
    except Exception as exc:
        logging.error("Не удалось получить последнюю версию Data Dragon: %s", exc)
        return 2

    saved = read_saved_version(DATA_DIR)

    if saved == latest and not args.force:
        logging.info("Данные Data Dragon актуальны (%s). Используйте --force для перекачивания.", latest)
        return 0

    try:
        save_all(latest, out_dir=DATA_DIR, locale=args.locale)
    except Exception as exc:
        logging.exception("Не удалось скачать или сохранить данные Data Dragon: %s", exc)
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())