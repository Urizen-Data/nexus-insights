#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — оркестратор ELT-пайплайна для League of Legends (Nexus Insights).
================================================================================

ELT-процесс (Extract → Load → Transform) в правильной последовательности:

  1. EXTRACT      (extract.py)      — сбор сырых данных из Riot API
                                       → raw_data/*.csv (участники, баны, игроки)
  2. DATA DRAGON  (data_dragon.py)  — статические справочники (чемпионы, предметы,
                                       заклинания, иконки) → data_dragon/*.csv
  3. LOAD         (load.py)         — CSV → Parquet → DuckDB (схема main)
                                       очистка, нормализация, загрузка таблиц
  4. TRANSFORM    (transform.py)    — main.* → витрины дашборда (3 схемы):
                                       lol_meta, lol_match_overview, lol_combat

Почему именно такой порядок:
  • EXTRACT первым — собирает динамические данные матчей (самое долгое — API).
  • DATA DRAGON вторым — справочники нужны для LOAD (конвертируются в Parquet/DuckDB).
  • LOAD третьим — создаёт схему main в DuckDB, без неё TRANSFORM упадёт.
  • TRANSFORM последним — читает main.* и строит аналитические витрины.

Запуск:
    python pipeline.py                        # полный прогон (E → DD → L → T)
    python pipeline.py --skip-extract         # пропустить сбор данных из API
    python pipeline.py --skip-data-dragon     # пропустить справочники
    python pipeline.py --force-data-dragon    # принудительно перекачать справочники
    python pipeline.py --skip-load            # пропустить load (если main уже есть)
    python pipeline.py --skip-transform       # пропустить построение витрин
    python pipeline.py --only transform       # запустить только один этап
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Имена скриптов-этапов (лежат в той же папке что и pipeline.py)
SCRIPT_EXTRACT     = "extract.py"       # 1. Extract  — Riot API → raw CSV
SCRIPT_DATA_DRAGON = "data_dragon.py"   # 2. Data Dragon — справочники → CSV
SCRIPT_LOAD        = "load.py"          # 3. Load     — CSV → Parquet → DuckDB.main
SCRIPT_TRANSFORM   = "transform.py"     # 4. Transform — main → витрины дашборда

# Папка где лежит pipeline.py (она же корень проекта ELT/)
BASE_DIR = Path(__file__).resolve().parent

# Lock-файл — защита от одновременного запуска двух пайплайнов
LOCK_FILE = BASE_DIR / ".pipeline.lock"

# Файл с логами оркестратора
LOG_FILE = BASE_DIR / "pipeline.log"


# ============================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================

def setup_logging() -> None:
    """Логирование одновременно в консоль (stdout) и в файл pipeline.log."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )


# ============================================================
# ПРОВЕРКИ ПЕРЕД ЗАПУСКОМ
# ============================================================

def check_prerequisites(skip_extract: bool, skip_data_dragon: bool,
                        skip_load: bool, skip_transform: bool) -> bool:
    """
    Проверяет наличие всех необходимых файлов перед запуском.

    Проверяет:
        — наличие скриптов-этапов (только тех, что НЕ пропущены),
        — config.yaml (нужен всем этапам),
        — .env с RIOT_API_KEY (нужен только для extract),
        — активацию виртуального окружения (предупреждение).

    Возвращает True если всё в порядке, иначе False.
    """
    all_ok = True

    # ── Собираем список скриптов для проверки (с учётом флагов --skip-*) ──
    scripts_to_check: list[str] = []
    if not skip_extract:
        scripts_to_check.append(SCRIPT_EXTRACT)
    if not skip_data_dragon:
        scripts_to_check.append(SCRIPT_DATA_DRAGON)
    if not skip_load:
        scripts_to_check.append(SCRIPT_LOAD)
    if not skip_transform:
        scripts_to_check.append(SCRIPT_TRANSFORM)

    for script in scripts_to_check:
        if not (BASE_DIR / script).exists():
            logging.error("❌ Скрипт не найден: %s", BASE_DIR / script)
            all_ok = False

    # ── config.yaml обязателен для всех этапов ──
    if not (BASE_DIR / "config.yaml").exists():
        logging.error("❌ config.yaml не найден: %s", BASE_DIR / "config.yaml")
        all_ok = False

    # ── .env нужен только для extract (Riot API key) ──
    if not skip_extract:
        if not (BASE_DIR / ".env").exists():
            logging.error("❌ Файл .env не найден: %s", BASE_DIR / ".env")
            logging.error("   Создайте .env со строкой RIOT_API_KEY=<ваш_ключ>")
            all_ok = False

    # ── Предупреждение если venv не активирован (не критично) ──
    if sys.base_prefix == sys.prefix:
        logging.warning("⚠️  Похоже, виртуальное окружение не активировано (.venv)")

    return all_ok


# ============================================================
# БЛОКИРОВКА ПОВТОРНОГО ЗАПУСКА (lock-файл)
# ============================================================

def acquire_lock() -> bool:
    """
    Создаёт lock-файл. Возвращает False если пайплайн уже запущен
    (lock-файл существует) — защита от параллельных запусков.
    """
    if LOCK_FILE.exists():
        lock_time = datetime.fromtimestamp(LOCK_FILE.stat().st_mtime)
        logging.error("❌ Пайплайн уже запущен! Lock-файл: %s (создан %s)",
                      LOCK_FILE, lock_time.strftime("%Y-%m-%d %H:%M:%S"))
        logging.error("   Если это старая блокировка — удалите файл вручную: %s", LOCK_FILE)
        return False
    LOCK_FILE.write_text(str(datetime.now()), encoding="utf-8")
    return True


def release_lock() -> None:
    """Удаляет lock-файл (вызывается в finally, даже при ошибке)."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


# ============================================================
# ЗАПУСК ОДНОГО ЭТАПА
# ============================================================

def run_step(step_name: str, script: str, cwd: Path,
             extra_args: list[str] | None = None) -> tuple[int, float]:
    """
    Запускает один скрипт-этап как отдельный процесс.

    Параметры:
        step_name  — человекочитаемое имя этапа (для логов)
        script     — имя .py файла
        cwd        — рабочая папка (BASE_DIR)
        extra_args — доп. аргументы командной строки (например ["--force"])

    Возвращает:
        (код_возврата, затраченное_время_в_секундах)
    """
    cmd = [sys.executable, script]
    if extra_args:
        cmd.extend(extra_args)

    logging.info("▶  %s: %s", step_name, " ".join(cmd))
    t0 = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(cwd))
    elapsed = time.perf_counter() - t0

    if result.returncode == 0:
        logging.info("✅ %s — успешно (%.1f сек)", step_name, elapsed)
    else:
        logging.error("❌ %s — ошибка (код %d, %.1f сек)",
                      step_name, result.returncode, elapsed)

    return result.returncode, elapsed


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def main() -> int:
    setup_logging()

    # ── Парсинг аргументов командной строки ──
    parser = argparse.ArgumentParser(
        description="Оркестратор ELT-пайплайна League of Legends (Nexus Insights)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Последовательность ELT:
    EXTRACT → DATA DRAGON → LOAD → TRANSFORM

Примеры:
    python pipeline.py                      # полный прогон
    python pipeline.py --skip-extract       # без сбора данных (использовать готовые CSV)
    python pipeline.py --skip-data-dragon   # без справочников
    python pipeline.py --force-data-dragon  # принудительно обновить справочники
    python pipeline.py --skip-load          # без load (если DuckDB.main уже готов)
    python pipeline.py --skip-transform     # без построения витрин
    python pipeline.py --only load          # запустить ТОЛЬКО этап load
        """,
    )
    parser.add_argument("--skip-extract", action="store_true",
                        help="Пропустить этап extract (сбор данных из Riot API)")
    parser.add_argument("--skip-data-dragon", action="store_true",
                        help="Пропустить загрузку справочников Data Dragon")
    parser.add_argument("--force-data-dragon", action="store_true",
                        help="Принудительно перекачать Data Dragon (--force)")
    parser.add_argument("--skip-load", action="store_true",
                        help="Пропустить этап load (CSV → Parquet → DuckDB)")
    parser.add_argument("--skip-transform", action="store_true",
                        help="Пропустить этап transform (построение витрин)")
    parser.add_argument("--only", choices=["extract", "data-dragon", "load", "transform"],
                        help="Запустить ТОЛЬКО указанный этап (остальные пропустить)")
    args = parser.parse_args()

    # ── Режим --only: пропускаем всё, кроме одного этапа ──
    if args.only:
        args.skip_extract     = args.only != "extract"
        args.skip_data_dragon = args.only != "data-dragon"
        args.skip_load        = args.only != "load"
        args.skip_transform   = args.only != "transform"

    # ── Блокировка повторного запуска ──
    if not acquire_lock():
        return 1

    try:
        logging.info("=" * 64)
        logging.info("🚀 ELT ПАЙПЛАЙН — ЗАПУСК (Extract → Data Dragon → Load → Transform)")
        logging.info(f"📂 Рабочая папка: {BASE_DIR}")
        logging.info(f"📝 Лог-файл:      {LOG_FILE}")
        logging.info("=" * 64)

        # ── Проверки перед запуском ──
        if not check_prerequisites(args.skip_extract, args.skip_data_dragon,
                                   args.skip_load, args.skip_transform):
            logging.error("❌ Проверки не пройдены — прерывание.")
            return 1

        errors: list[str] = []
        total_t0 = time.perf_counter()

        # ════════════════════════════════════════════════════
        # ЭТАП 1: EXTRACT — сбор сырых данных из Riot API
        #         → raw_data/matches_participants.csv, bans.csv, players.csv
        # ════════════════════════════════════════════════════
        if not args.skip_extract:
            rc, _ = run_step("1️⃣  EXTRACT", SCRIPT_EXTRACT, BASE_DIR)
            if rc != 0:
                errors.append("Extract")
                # Extract — критичный этап: без сырых данных нет смысла продолжать
                logging.error("⛔ Extract завершился с ошибкой — прерываем пайплайн.")
                _finish(errors, total_t0)
                return 1
        else:
            logging.info("⏭️  EXTRACT — пропущен (--skip-extract)")

        # ════════════════════════════════════════════════════
        # ЭТАП 2: DATA DRAGON — статические справочники
        #         → data_dragon/champions.csv, items.csv, spells.csv, icons.csv
        # ════════════════════════════════════════════════════
        if not args.skip_data_dragon:
            extra = ["--force"] if args.force_data_dragon else None
            rc, _ = run_step("2️⃣  DATA DRAGON", SCRIPT_DATA_DRAGON, BASE_DIR, extra_args=extra)
            if rc != 0:
                errors.append("Data Dragon")
                # Не критично: load может работать без справочников (просто без обогащения)
                logging.warning("⚠️  Data Dragon завершился с ошибкой — продолжаем без справочников.")
        else:
            logging.info("⏭️  DATA DRAGON — пропущен (--skip-data-dragon)")

        # ════════════════════════════════════════════════════
        # ЭТАП 3: LOAD — очистка, нормализация, загрузка в DuckDB
        #         CSV → Parquet → DuckDB (схема main)
        #         ⚠️ КРИТИЧНЫЙ: создаёт main.* — без него transform упадёт!
        # ════════════════════════════════════════════════════
        if not args.skip_load:
            rc, _ = run_step("3️⃣  LOAD", SCRIPT_LOAD, BASE_DIR)
            if rc != 0:
                errors.append("Load")
                # Load — критичный этап: без схемы main transform не сможет работать
                logging.error("⛔ Load завершился с ошибкой — прерываем пайплайн "
                              "(transform не сможет прочитать main.*).")
                _finish(errors, total_t0)
                return 1
        else:
            logging.info("⏭️  LOAD — пропущен (--skip-load)")

        # ════════════════════════════════════════════════════
        # ЭТАП 4: TRANSFORM — построение аналитических витрин
        #         main.* → lol_meta, lol_match_overview, lol_combat
        # ════════════════════════════════════════════════════
        if not args.skip_transform:
            rc, _ = run_step("4️⃣  TRANSFORM", SCRIPT_TRANSFORM, BASE_DIR)
            if rc != 0:
                errors.append("Transform")
        else:
            logging.info("⏭️  TRANSFORM — пропущен (--skip-transform)")

        # ── Итог ──
        return _finish(errors, total_t0)

    finally:
        release_lock()


def _finish(errors: list[str], total_t0: float) -> int:
    """
    Печатает итоговую сводку и возвращает код выхода.
    Вынесено отдельно, чтобы вызывать при раннем прерывании.
    """
    total_elapsed = time.perf_counter() - total_t0

    if errors:
        logging.error("=" * 64)
        logging.error("❌ ПАЙПЛАЙН ЗАВЕРШЁН С ОШИБКАМИ: %s", ", ".join(errors))
        logging.error("⏱️  Общее время: %.1f сек (%.1f мин)",
                      total_elapsed, total_elapsed / 60)
        logging.error("=" * 64)
        return 1

    logging.info("=" * 64)
    logging.info("✅ ELT ПАЙПЛАЙН УСПЕШНО ЗАВЕРШЁН")
    logging.info("⏱️  Общее время: %.1f сек (%.1f мин)",
                 total_elapsed, total_elapsed / 60)
    logging.info("💾 Результат: parquet_folder/lol.duckdb (схемы main + витрины)")
    logging.info("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())