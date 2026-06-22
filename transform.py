#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transform.py — создание аналитических витрин для дашборда LoL.
================================================================

Назначение:
    Читает сырые таблицы из DuckDB (main.match_info, main.participants, 
    main.players, main.bans, main.champions и др.), агрегирует данные 
    и создаёт оптимизированные витрины для трёх страниц дашборда.

Создаёт витрины для трёх страниц:
    • lol_meta            — первая страница (Meta Dashboard)
      df_all, df_by_role, df_items, SPELLS_DF, EXTRA_DF, RADAR_BASE
    
    • lol_match_overview  — вторая страница (Match Overview)
      match_overview_matches, match_overview_sides,
      match_overview_cancel, match_overview_players
    
    • lol_combat          — третья страница (Blood & Objectives)
      combat_match, combat_team, combat_player_agg,
      combat_hist_dpm, combat_hist_vpm,
      combat_density_2d, combat_violin_kda

Принцип работы:
    1. Подключается к DuckDB (main schema — сырые данные от load.py)
    2. Для каждой схемы выполняет SQL-запросы агрегации
    3. Сохраняет результаты в Parquet (parquet_folder/{schema}/)
    4. Загружает витрины обратно в DuckDB в соответствующие схемы

Особенности:
    • Все агрегации выполняются в SQL (не в pandas) — максимальная скорость
    • Бины гистограмм сериализуются в JSON для компактного хранения
    • Семплы для 2D-плотности и KDA-violin используют PERCENT-выборку
    • Параметры семплирования и даты задаются в config.yaml

Запуск:
    python transform.py
"""
from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# ИМПОРТЫ
# ═══════════════════════════════════════════════════════════
import json                                # сериализация бинов гистограмм в JSON
import logging                             # логирование процесса
import sys                                 # stdout для логов
from pathlib import Path                   # кроссплатформенная работа с путями

import pandas as pd                        # DataFrame для результатов агрегации
import yaml                                # чтение config.yaml
import duckdb                              # DuckDB для выполнения SQL-запросов


# ============================================================
# КОНФИГУРАЦИЯ — загрузка параметров из config.yaml
# ============================================================

def load_config() -> dict:
    """
    Загружает все параметры из config.yaml.
    
    Возвращает словарь с ключами:
        duckdb_path              — путь к базе DuckDB
        parquet_dir              — папка для выходных Parquet-витрин
        queue_id                 — ID очереди (420 = Ranked Solo/Duo)
        min_games                — минимальное число игр для включения в витрину
        log_level                — уровень логирования
        combat_density_match_pct — % матчей для семплирования 2D-плотности
        combat_violin_row_pct    — % строк для семплирования KDA-violin
        combat_remake_sec        — порог ремейка (матчи короче = ремейк)
        combat_start_date        — дата начала периода анализа
    
    Все пути резолвятся относительно папки скрипта.
    """
    script_dir = Path(__file__).resolve().parent           # папка где лежит transform.py (ELT/)
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

    duckdb_path = resolve_path(cfg.get("duckdb_path", "parquet_folder/lol.duckdb"))
    parquet_dir = resolve_path(cfg.get("parquet_output_dir", "parquet_folder"))

    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    # ★ Чистим дату от лишних кавычек (может прийти как '2026-05-01' или 2026-05-01)
    raw_date = cfg.get("combat_start_date", "2026-05-01")
    combat_start_date = str(raw_date).strip().strip("'").strip('"')

    return {
        "duckdb_path": duckdb_path,
        "parquet_dir": parquet_dir,
        "queue_id": cfg.get("queue_id", 420),
        "min_games": cfg.get("min_games", 1),
        "log_level": cfg.get("log_level", "INFO"),
        # ── Параметры для combat-витрин ──
        "combat_density_match_pct": cfg.get("combat_density_match_pct", 20),
        "combat_violin_row_pct": cfg.get("combat_violin_row_pct", 15),
        "combat_remake_sec": cfg.get("combat_remake_sec", 300),
        "combat_start_date": combat_start_date,
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

def _add_meta_cols(df):
    """
    Добавляет динамические колонки к витрине чемпионов:
    
    presence   — Pick% + Ban% (насколько чемпион присутствует в мете)
    css_score  — Champion Strength Score (WR×0.5 + Pick×0.3 + Ban×0.2, нормировано 0-100)
    pbi        — Power Ban Index: (WR − avg_WR) × Pick% / (100 − Ban%)
    tier       — S+, S, A, B, C на основе квантилей css_score
    
    ★ Эта функция используется ТОЛЬКО в витринах lol_meta.
      В дашборде эти колонки пересчитываются динамически через _add_meta_cols
      в зависимости от фильтров (регион, роль, min_games).
    """
    df = df.copy()
    # Presence = сумма pick rate и ban rate, capped на 100%
    df['presence'] = (df['pickrate'] + df['banrate']).clip(upper=100).round(1)

    # Средневзвешенный винрейт (для PBI)
    if not df.empty and df['games'].sum() > 0:
        avg_wr = (df['winrate'] * df['games']).sum() / df['games'].sum()
    else:
        avg_wr = 50.0

    def _norm(col):
        """Нормировка колонки в диапазон [0, 1]."""
        lo, hi = df[col].min(), df[col].max()
        return (df[col] - lo) / (hi - lo) if hi > lo else df[col] * 0

    # Strength Score = нормированные WR, Pick, Ban с весами
    if not df.empty and len(df) > 1:
        df['css_score'] = ((_norm('winrate') * 0.5 + _norm('pickrate') * 0.3
                            + _norm('banrate') * 0.2) * 100).round(2)
    else:
        df['css_score'] = df['winrate'].round(2) if not df.empty else pd.Series(dtype=float)

    # Power Ban Index
    denom = (100 - df['banrate']).replace(0, 1)
    df['pbi'] = ((df['winrate'] - avg_wr) * df['pickrate'] / denom).round(3)

    # Tier — квантили css_score
    if not df.empty:
        q = df['css_score'].quantile([0.20, 0.50, 0.80, 0.95]).tolist()
        bins, eps = [-float('inf')] + q + [float('inf')], 1e-9
        for i in range(1, len(bins)):
            if bins[i] <= bins[i - 1]:
                bins[i] = bins[i - 1] + eps              # гарантируем строгое возрастание
        df['tier'] = pd.cut(df['css_score'], bins=bins,
                            labels=['C', 'B', 'A', 'S', 'S+'],
                            include_lowest=True).astype(str)
    else:
        df['tier'] = 'C'
    return df


def _write_parquet(df: pd.DataFrame, path: Path):
    """
    Сохраняет DataFrame в формат Parquet.
    Использует snappy-сжатие (встроено в pandas).
    
    Параметры:
        df   — DataFrame для сохранения
        path — путь к выходному .parquet файлу
    """
    df.to_parquet(path, index=False)
    logging.info("  ✓ parquet: %s/%s (%d строк)", path.parent.name, path.name, len(df))


def _to_db(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, table_name: str, schema: str):
    """
    Загружает DataFrame в DuckDB как таблицу в указанной схеме.
    
    Если таблица с таким именем уже существует — удаляет и создаёт заново.
    
    Параметры:
        conn       — соединение с DuckDB
        df         — DataFrame для загрузки
        table_name — имя создаваемой таблицы
        schema     — имя схемы (lol_meta, lol_match_overview, lol_combat)
    """
    full_name = f"{schema}.{table_name}"
    conn.execute(f"DROP TABLE IF EXISTS {full_name}")
    conn.execute(f"CREATE TABLE {full_name} AS SELECT * FROM df")
    count = conn.execute(f"SELECT COUNT(*) FROM {full_name}").fetchone()[0]
    logging.info("  ✓ DuckDB %s: %d строк", full_name, count)


def _compute_hist_bins(conn, queue_id, rem_sec, start_date,
                       *, col_expr, clip_pct, nbins,
                       schema, table_name, marts_dir):
    """
    Вычисляет бины гистограммы В SQL и сохраняет в витрину.
    
    Для каждого региона (ALL, EU, US):
    1. Находит верхнюю границу по перцентилю (clip_pct)
    2. Считает min, max, median, avg метрики
    3. Разбивает диапазон на nbins бинов
    4. Считает количество значений в каждом бине
    5. Сохраняет всё в JSON-строку (counts_json)
    
    Параметры:
        conn        — соединение с DuckDB
        queue_id    — ID очереди (420)
        rem_sec     — порог ремейка в секундах
        start_date  — дата начала периода
        col_expr    — SQL-выражение для метрики (например "COALESCE(p.damage_to_champions,0) / ...")
        clip_pct    — перцентиль для отсечения выбросов (0.99)
        nbins       — количество бинов (60)
        schema      — схема для сохранения
        table_name  — имя таблицы
        marts_dir   — папка для Parquet
    
    Структура выходной таблицы:
        region_key  — ALL, EU, US
        lo          — нижняя граница
        hi          — верхняя граница
        width       — ширина одного бина
        nbins       — количество бинов
        median      — медиана распределения
        avg         — среднее значение
        counts_json — JSON-строка с массивом counts по бинам
    """
    rows = []
    for rk in ['ALL', 'EU', 'US']:
        # Фильтр по региону (для ALL — без фильтра)
        region_filter = "" if rk == 'ALL' else \
            f"AND UPPER(TRIM(CAST(m.region AS VARCHAR)))='{rk}'"

        # ── Шаг 1: верхняя граница по перцентилю (clip) ──
        hi_val = conn.execute(f"""
            SELECT quantile_cont({col_expr}, {clip_pct})
            FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
            WHERE m.queue_id = {queue_id} AND m.game_duration >= {rem_sec}
              AND m.match_date >= DATE '{start_date}' {region_filter}
        """).fetchone()[0]

        if hi_val is None:
            continue

        # Обрезаем выбросы: всё что выше clip_pct — заменяем на hi_val
        clip_expr = f"LEAST({col_expr}, {hi_val})"

        # ── Шаг 2: статистики (min, max, median, avg) ──
        stats = conn.execute(f"""
            SELECT MIN({clip_expr}) AS lo, MAX({clip_expr}) AS hi,
                   MEDIAN({clip_expr}) AS med, AVG({clip_expr}) AS avg
            FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
            WHERE m.queue_id = {queue_id} AND m.game_duration >= {rem_sec}
              AND m.match_date >= DATE '{start_date}' {region_filter}
        """).df().iloc[0]

        lo, hi = float(stats['lo']), float(stats['hi'])
        if hi <= lo:
            hi = lo + 1                                 # защита от вырожденного случая
        width = (hi - lo) / nbins                        # ширина одного бина

        # ── Шаг 3: биннинг в SQL ──
        # Функция: FLOOR((value - lo) / width) → номер бина
        # LEAST(..., nbins-1) — чтобы крайние значения не выпадали за границы
        bins_df = conn.execute(f"""
            SELECT LEAST(CAST(FLOOR(({clip_expr} - {lo}) / {width}) AS INT), {nbins - 1}) AS b,
                   COUNT(*) AS c
            FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
            WHERE m.queue_id = {queue_id} AND m.game_duration >= {rem_sec}
              AND m.match_date >= DATE '{start_date}' {region_filter}
            GROUP BY b ORDER BY b
        """).df()

        # ── Шаг 4: собираем counts в массив и сериализуем в JSON ──
        counts = [0] * nbins
        for _, row in bins_df.iterrows():
            bi = int(row['b'])
            if 0 <= bi < nbins:
                counts[bi] = int(row['c'])

        rows.append({
            'region_key': rk,
            'lo': round(lo, 4),
            'hi': round(hi, 4),
            'width': round(width, 4),
            'nbins': nbins,
            'median': round(float(stats['med']), 4),
            'avg': round(float(stats['avg']), 4),
            'counts_json': json.dumps(counts),          # массив counts → JSON-строка
        })

    df_bins = pd.DataFrame(rows)
    _write_parquet(df_bins, marts_dir / f"{table_name}.parquet")
    _to_db(conn, df_bins, table_name, schema)
    logging.info("  ✓ %s: %d регионов", table_name, len(df_bins))


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def main():
    """
    Главная функция: создаёт витрины для всех трёх страниц дашборда.
    
    Порядок работы:
    1. Загружает конфигурацию
    2. Подключается к DuckDB
    3. Создаёт схему lol_meta (6 витрин)
    4. Создаёт схему lol_match_overview (4 витрины)
    5. Создаёт схему lol_combat (7 витрин)
    6. Выводит итоговую статистику
    """
    cfg = load_config()
    setup_logging(cfg["log_level"])

    duckdb_path = str(cfg["duckdb_path"])
    parquet_dir = cfg["parquet_dir"]
    queue_id = cfg["queue_id"]
    min_games = cfg["min_games"]

    # ── Параметры для combat-витрин из конфига ──
    combat_density_pct = cfg["combat_density_match_pct"]     # % матчей для 2D-плотности
    combat_violin_pct = cfg["combat_violin_row_pct"]         # % строк для KDA-violin
    combat_remake_sec = cfg["combat_remake_sec"]             # порог ремейка
    combat_start_date = cfg["combat_start_date"]             # дата начала периода

    logging.info("=" * 60)
    logging.info("TRANSFORM: Создание витрин для дашбордов")
    logging.info(f"  duckdb:            {duckdb_path}")
    logging.info(f"  parquet:           {parquet_dir}")
    logging.info(f"  queue:             {queue_id}")
    logging.info(f"  density_match_pct: {combat_density_pct}%")
    logging.info(f"  violin_row_pct:    {combat_violin_pct}%")
    logging.info(f"  start_date:        {combat_start_date}")
    logging.info("=" * 60)

    conn = duckdb.connect(duckdb_path)

    # ============================================================
    # СХЕМА 1: lol_meta — первая страница дашборда (Meta Dashboard)
    # ============================================================
    META_SCHEMA = "lol_meta"
    lol_meta_dir = parquet_dir / "lol_meta"
    lol_meta_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {META_SCHEMA}")

    # Общее число матчей (используется для расчёта pickrate)
    TOTAL_MATCHES = conn.execute(
        f"SELECT COUNT(DISTINCT match_id) FROM main.match_info WHERE queue_id={queue_id}"
    ).fetchone()[0]
    logging.info(f"  Всего матчей: {TOTAL_MATCHES}")

    # ── Общий SQL-фрагмент для статистик чемпионов ──
    _STATS_SELECT = f"""
        COUNT(*) as games,
        ROUND(AVG(p.kills),   2) as avg_kills,
        ROUND(AVG(p.deaths),  2) as avg_deaths,
        ROUND(AVG(p.assists), 2) as avg_assists,
        ROUND(100.0 * SUM(CAST(p.win AS INT)) / COUNT(*), 2) as winrate,
        ROUND(AVG(CAST(p.kills + p.assists AS FLOAT))
              / NULLIF(AVG(CAST(p.deaths AS FLOAT)), 0), 2) as kda,
        ROUND(100.0 * COUNT(*) / {TOTAL_MATCHES}, 2) as pickrate,
        ROUND(AVG(p.gold_earned), 0) as avg_gold,
        ROUND(AVG(p.minions_killed)
              / NULLIF(AVG(mi.game_duration) / 60, 0), 1) as cs_per_min,
        ROUND(AVG(p.damage_to_champions), 0) as avg_damage,
        ROUND(AVG(p.damage_taken),        0) as avg_damage_taken,
        ROUND(AVG(p.dragon_kills), 2) as avg_dragons,
        ROUND(AVG(p.baron_kills),  2) as avg_barons
    """

    # --- df_all: базовая статистика чемпионов по регионам ---
    logging.info("--- lol_meta: df_all ---")
    df_all = conn.execute(f"""
        SELECT p.champion_name, mi.region, {_STATS_SELECT}
        FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
        WHERE mi.queue_id = {queue_id}
        GROUP BY p.champion_name, mi.region HAVING games >= {min_games}
    """).df()

    # Ban rate: % УНИКАЛЬНЫХ матчей где чемпион был забанен
    # COUNT(DISTINCT b.match_id) — чтобы бан в обеих командах одного матча не считался дважды
    ban_df = conn.execute(f"""
        SELECT c.champion_name, mi.region,
               ROUND(100.0 * COUNT(DISTINCT b.match_id) / {TOTAL_MATCHES}, 2) as banrate
        FROM main.bans b
        JOIN main.champions c   ON b.champion_id = c.champion_id
        JOIN main.match_info mi ON b.match_id    = mi.match_id
        WHERE mi.queue_id = {queue_id}
        GROUP BY c.champion_name, mi.region
    """).df()

    df_all = df_all.merge(ban_df, on=['champion_name', 'region'], how='left')
    df_all['banrate'] = df_all['banrate'].fillna(0)
    df_all = _add_meta_cols(df_all)                      # добавляем presence, css_score, tier

    _write_parquet(df_all, lol_meta_dir / "df_all.parquet")
    _to_db(conn, df_all, "df_all", META_SCHEMA)

    # --- df_by_role: статистика чемпионов по ролям ---
    logging.info("--- lol_meta: df_by_role ---")
    df_by_role = conn.execute(f"""
        SELECT p.champion_name, mi.region, p.team_position, {_STATS_SELECT}
        FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
        WHERE mi.queue_id = {queue_id}
        GROUP BY p.champion_name, mi.region, p.team_position HAVING games >= {min_games}
    """).df()

    df_by_role = df_by_role.merge(ban_df, on=['champion_name', 'region'], how='left')
    df_by_role['banrate'] = df_by_role['banrate'].fillna(0)
    df_by_role = _add_meta_cols(df_by_role)

    _write_parquet(df_by_role, lol_meta_dir / "df_by_role.parquet")
    _to_db(conn, df_by_role, "df_by_role", META_SCHEMA)

    # --- df_items: топ предметов по чемпионам ---
    # UNPIVOT превращает 7 колонок предметов (item0-item6) в строки
    logging.info("--- lol_meta: df_items ---")
    df_items = conn.execute(f"""
        SELECT champion_name, item_id, COUNT(*) AS count FROM (
            UNPIVOT (
                SELECT p.champion_name, p.item0, p.item1, p.item2,
                       p.item3, p.item4, p.item5, p.item6
                FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
                WHERE mi.queue_id = {queue_id}
            ) ON item0,item1,item2,item3,item4,item5,item6 INTO NAME col VALUE item_id
        ) WHERE item_id > 0 GROUP BY champion_name, item_id
    """).df()

    _write_parquet(df_items, lol_meta_dir / "df_items.parquet")
    _to_db(conn, df_items, "df_items", META_SCHEMA)

    # --- SPELLS_DF: топ заклинаний призывателя по чемпионам ---
    # UNION ALL объединяет два слота заклинаний в один столбец
    logging.info("--- lol_meta: SPELLS_DF ---")
    SPELLS_DF = conn.execute(f"""
        SELECT champion_name, spell_id, COUNT(*) AS count FROM (
            SELECT p.champion_name, p.summoner_spell1 AS spell_id
            FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
            WHERE mi.queue_id = {queue_id} AND p.summoner_spell1 > 0
            UNION ALL
            SELECT p.champion_name, p.summoner_spell2
            FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
            WHERE mi.queue_id = {queue_id} AND p.summoner_spell2 > 0
        ) GROUP BY champion_name, spell_id
    """).df()

    _write_parquet(SPELLS_DF, lol_meta_dir / "SPELLS_DF.parquet")
    _to_db(conn, SPELLS_DF, "SPELLS_DF", META_SCHEMA)

    # --- EXTRA_DF: дополнительные метрики чемпионов ---
    # Vision, healing, spell casts — для детальной карточки чемпиона
    logging.info("--- lol_meta: EXTRA_DF ---")
    EXTRA_DF = conn.execute(f"""
        SELECT p.champion_name,
               ROUND(AVG(p.vision_score),    1)                       as avg_vision,
               ROUND(AVG(p.turret_kills),    2)                       as avg_turrets,
               ROUND(AVG(p.inhibitor_kills), 2)                       as avg_inhibitors,
               ROUND(AVG(p.wards_placed),    1)                       as avg_wards_placed,
               ROUND(AVG(p.wards_killed),    1)                       as avg_wards_killed,
               ROUND(AVG(p.total_heal),      0)                       as avg_heal,
               ROUND(AVG(p.champ_level),     1)                       as avg_level,
               ROUND(AVG(p.minions_killed),  0)                       as avg_cs_total,
               ROUND(100.0 * AVG(CAST(p.first_blood_kill AS INT)), 1) as firstblood_rate,
               ROUND(AVG(p.spell1_casts),    1)                       as avg_q,
               ROUND(AVG(p.spell2_casts),    1)                       as avg_w,
               ROUND(AVG(p.spell3_casts),    1)                       as avg_e,
               ROUND(AVG(p.spell4_casts),    1)                       as avg_r
        FROM main.participants p JOIN main.match_info mi ON p.match_id = mi.match_id
        WHERE mi.queue_id = {queue_id} GROUP BY p.champion_name
    """).df()

    _write_parquet(EXTRA_DF, lol_meta_dir / "EXTRA_DF.parquet")
    _to_db(conn, EXTRA_DF, "EXTRA_DF", META_SCHEMA)

    # --- RADAR_BASE: база для радар-диаграммы (8 осей) ---
    # Комбо-метрики + перцентили для Playstyle-радара
    logging.info("--- lol_meta: RADAR_BASE ---")
    RADAR_BASE = conn.execute(f"""
        SELECT 
            p.champion_name,
            ROUND(AVG(p.kills), 2) as avg_kills,
            ROUND(AVG(p.deaths), 2) as avg_deaths,
            ROUND(AVG(p.assists), 2) as avg_assists,
            ROUND(AVG(p.gold_earned), 0) as avg_gold,
            ROUND(AVG(p.minions_killed)
                  / NULLIF(AVG(mi.game_duration) / 60, 0), 1) as cs_per_min,
            ROUND(AVG(p.damage_to_champions), 0) as avg_damage,
            ROUND(AVG(p.damage_taken), 0) as avg_damage_taken,
            ROUND(AVG(p.dragon_kills), 2) as avg_dragons,
            ROUND(AVG(p.baron_kills), 2) as avg_barons,
            ROUND(AVG(p.turret_kills), 2) as avg_turrets,
            ROUND(AVG(p.vision_score), 1) as avg_vision,
            ROUND(AVG(p.total_heal), 0) as avg_heal,
            ROUND(AVG(p.wards_placed), 1) as avg_wards_placed,
            ROUND(100.0 * AVG(CAST(p.first_blood_kill AS INT)), 1) as firstblood_rate
        FROM main.participants p 
        JOIN main.match_info mi ON p.match_id = mi.match_id
        WHERE mi.queue_id = {queue_id}
        GROUP BY p.champion_name
    """).df()

    # Комбо-метрики для осей радара
    RADAR_BASE['m_damage']     = RADAR_BASE['avg_damage']                                    # урон
    RADAR_BASE['m_tank']       = RADAR_BASE['avg_damage_taken']                              # танковость
    RADAR_BASE['m_sustain']    = RADAR_BASE['avg_heal']                                      # лечение
    RADAR_BASE['m_aggression'] = RADAR_BASE['avg_kills'] + RADAR_BASE['firstblood_rate'] / 10.0  # агрессия
    RADAR_BASE['m_teamplay']   = RADAR_BASE['avg_assists']                                   # командная игра
    RADAR_BASE['m_economy']    = RADAR_BASE['avg_gold'] / 100.0 + RADAR_BASE['cs_per_min']   # экономика
    RADAR_BASE['m_vision']     = RADAR_BASE['avg_vision'] + RADAR_BASE['avg_wards_placed']   # обзор
    RADAR_BASE['m_objectives'] = (RADAR_BASE['avg_dragons'] + RADAR_BASE['avg_barons']       # объекты
                                  + RADAR_BASE['avg_turrets'])

    # Перцентили (rank 0-1 → 0-100)
    for col in ['m_damage', 'm_tank', 'm_sustain', 'm_aggression',
                'm_teamplay', 'm_economy', 'm_vision', 'm_objectives']:
        RADAR_BASE[f'{col}_pct'] = (RADAR_BASE[col].rank(pct=True) * 100).round(1)

    _write_parquet(RADAR_BASE, lol_meta_dir / "RADAR_BASE.parquet")
    _to_db(conn, RADAR_BASE, "RADAR_BASE", META_SCHEMA)

    logging.info(f"✅ lol_meta: {len(df_all)} строк в df_all")

    # ============================================================
    # СХЕМА 2: lol_match_overview — вторая страница дашборда
    # ============================================================
    MATCH_SCHEMA = "lol_match_overview"
    match_marts_dir = parquet_dir / "match_overview"
    match_marts_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {MATCH_SCHEMA}")

    REMAKE_SEC = combat_remake_sec     # порог ремейка из config.yaml
    START_DATE = combat_start_date     # дата начала периода из config.yaml

    # --- match_overview_matches: базовая информация о матчах ---
    logging.info("--- lol_match_overview: match_overview_matches ---")
    df_ov_matches = conn.execute(f"""
        SELECT match_id, region, match_date, game_duration,
               game_duration / 60.0 AS duration_min, 
               game_start_timestamp, game_version
        FROM main.match_info
        WHERE queue_id = {queue_id} AND match_date >= DATE '{START_DATE}'
    """).df()
    _write_parquet(df_ov_matches, match_marts_dir / "match_overview_matches.parquet")
    _to_db(conn, df_ov_matches, "match_overview_matches", MATCH_SCHEMA)
    logging.info(f"  ✓ {len(df_ov_matches)} строк")

    # --- match_overview_sides: баланс синей/красной стороны ---
    logging.info("--- lol_match_overview: match_overview_sides ---")
    df_ov_sides = conn.execute(f"""
        SELECT p.match_id, m.region, m.match_date, m.game_version,
               MAX(CASE WHEN p.team_id = 100 AND p.win THEN 1 ELSE 0 END) AS blue_win
        FROM main.participants p 
        JOIN main.match_info m ON p.match_id = m.match_id
        WHERE m.queue_id = {queue_id} 
          AND m.game_duration >= {REMAKE_SEC}
          AND m.match_date >= DATE '{START_DATE}'
        GROUP BY p.match_id, m.region, m.match_date, m.game_version
    """).df()
    _write_parquet(df_ov_sides, match_marts_dir / "match_overview_sides.parquet")
    _to_db(conn, df_ov_sides, "match_overview_sides", MATCH_SCHEMA)
    logging.info(f"  ✓ {len(df_ov_sides)} строк")

    # --- match_overview_cancel: данные о сдачах и ремейках ---
    logging.info("--- lol_match_overview: match_overview_cancel ---")
    df_ov_cancel = conn.execute(f"""
        SELECT m.match_id, m.region, m.match_date, m.game_duration,
               m.game_duration / 60.0 AS duration_min,
               MAX(CASE WHEN p.game_ended_in_surrender THEN 1 ELSE 0 END) AS surrendered
        FROM main.participants p 
        JOIN main.match_info m ON p.match_id = m.match_id
        WHERE m.queue_id = {queue_id} 
          AND m.match_date >= DATE '{START_DATE}'
        GROUP BY m.match_id, m.region, m.match_date, m.game_duration
    """).df()
    _write_parquet(df_ov_cancel, match_marts_dir / "match_overview_cancel.parquet")
    _to_db(conn, df_ov_cancel, "match_overview_cancel", MATCH_SCHEMA)
    logging.info(f"  ✓ {len(df_ov_cancel)} строк")

    # --- match_overview_players: данные игроков (LP, тир, винрейт) ---
    logging.info("--- lol_match_overview: match_overview_players ---")
    df_ov_players = conn.execute("""
        SELECT puuid, riot_game_name, riot_tagline, region, tier,
               leaguePoints, wins, losses
        FROM main.players WHERE leaguePoints IS NOT NULL
    """).df()
    _write_parquet(df_ov_players, match_marts_dir / "match_overview_players.parquet")
    _to_db(conn, df_ov_players, "match_overview_players", MATCH_SCHEMA)
    logging.info(f"  ✓ {len(df_ov_players)} строк")

    logging.info(f"✅ lol_match_overview: 4 витрины созданы")

    # ============================================================
    # СХЕМА 3: lol_combat — третья страница (Blood & Objectives)
    # ============================================================
    COMBAT_SCHEMA = "lol_combat"
    combat_marts_dir = parquet_dir / "combat"
    combat_marts_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {COMBAT_SCHEMA}")

    # --- combat_match: агрегаты уровня матча (kills, objectives) ---
    logging.info("--- lol_combat: combat_match ---")
    df_combat_match = conn.execute(f"""
        SELECT p.match_id, m.match_date, m.game_version,
               UPPER(TRIM(CAST(m.region AS VARCHAR))) AS region,
               m.game_duration/60.0 AS dur,
               SUM(p.kills) AS kills, SUM(p.deaths) AS deaths, SUM(p.assists) AS assists,
               SUM(p.dragon_kills) AS dragons, SUM(p.baron_kills) AS barons,
               SUM(p.turret_kills) AS towers, SUM(p.inhibitor_kills) AS inhibitors
        FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
        WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
          AND m.match_date >= DATE '{combat_start_date}'
        GROUP BY p.match_id, m.match_date, m.game_version, m.region, m.game_duration
    """).df()
    _write_parquet(df_combat_match, combat_marts_dir / "combat_match.parquet")
    _to_db(conn, df_combat_match, "combat_match", COMBAT_SCHEMA)
    logging.info(f"  ✓ {len(df_combat_match)} строк")

    # --- combat_team: агрегаты уровня команды (first_blood, objectives по team_id) ---
    logging.info("--- lol_combat: combat_team ---")
    df_combat_team = conn.execute(f"""
        SELECT p.match_id, 
               UPPER(TRIM(CAST(m.region AS VARCHAR))) AS region,
               CAST(p.win AS INT)=1 AS win,
               MAX(CASE WHEN p.first_blood_kill THEN 1 ELSE 0 END) AS first_blood,
               m.game_duration/60.0 AS dur,
               SUM(p.dragon_kills) AS dragons, SUM(p.baron_kills) AS barons,
               SUM(p.turret_kills) AS towers, SUM(p.inhibitor_kills) AS inhibitors
        FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
        WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
          AND m.match_date >= DATE '{combat_start_date}'
        GROUP BY p.match_id, m.region, CAST(p.win AS INT), m.game_duration
    """).df()
    _write_parquet(df_combat_team, combat_marts_dir / "combat_team.parquet")
    _to_db(conn, df_combat_team, "combat_team", COMBAT_SCHEMA)
    logging.info(f"  ✓ {len(df_combat_team)} строк")

    # --- combat_player_agg: средние метрики игрока по region × win ---
    # kpm = kills/min, dpm = damage/min, kda, kp = kill participation %, vpm = vision/min
    logging.info("--- lol_combat: combat_player_agg ---")
    df_combat_player_agg = conn.execute(f"""
        WITH base AS (
            SELECT
                UPPER(TRIM(CAST(m.region AS VARCHAR))) AS region,
                CAST(p.win AS INT)=1 AS win,
                p.kills, p.assists,
                GREATEST(p.deaths, 1) AS d_safe,
                COALESCE(p.vision_score,0) AS vision,
                COALESCE(p.damage_to_champions,0) AS dmg,
                GREATEST(m.game_duration/60.0, 1) AS dur_safe,
                SUM(p.kills) OVER (PARTITION BY p.match_id, p.team_id) AS team_kills
            FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
            WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
              AND m.match_date >= DATE '{combat_start_date}'
        )
        SELECT region, win,
               AVG(kills / dur_safe)                       AS kpm,
               AVG(dmg / dur_safe)                         AS dpm,
               AVG((kills + assists) / d_safe)             AS kda,
               AVG(LEAST((kills + assists)
                    / GREATEST(team_kills, 1), 1.0)) * 100 AS kp,
               AVG(vision / dur_safe)                      AS vpm
        FROM base
        GROUP BY region, win
    """).df()
    _write_parquet(df_combat_player_agg, combat_marts_dir / "combat_player_agg.parquet")
    _to_db(conn, df_combat_player_agg, "combat_player_agg", COMBAT_SCHEMA)
    logging.info(f"  ✓ {len(df_combat_player_agg)} строк")

    # --- combat_hist_dpm: бины гистограммы Damage/min ---
    logging.info("--- lol_combat: combat_hist_dpm ---")
    _compute_hist_bins(conn, queue_id, combat_remake_sec, combat_start_date,
        col_expr="COALESCE(p.damage_to_champions,0) / GREATEST(m.game_duration/60.0,1)",
        clip_pct=0.99, nbins=60,
        schema=COMBAT_SCHEMA, table_name="combat_hist_dpm",
        marts_dir=combat_marts_dir)

    # --- combat_hist_vpm: бины гистограммы Vision/min ---
    logging.info("--- lol_combat: combat_hist_vpm ---")
    _compute_hist_bins(conn, queue_id, combat_remake_sec, combat_start_date,
        col_expr="COALESCE(p.vision_score,0) / GREATEST(m.game_duration/60.0,1)",
        clip_pct=0.99, nbins=60,
        schema=COMBAT_SCHEMA, table_name="combat_hist_vpm",
        marts_dir=combat_marts_dir)

    # --- combat_density_2d: семпл {combat_density_pct}% матчей для 2D-плотности (dpm vs kp) ---
    # Сначала семплируем матчи, потом берём всех участников этих матчей
    logging.info(f"--- lol_combat: combat_density_2d ({combat_density_pct}% матчей) ---")
    df_combat_density = conn.execute(f"""
        WITH sampled_matches AS (
            SELECT DISTINCT p.match_id
            FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
            WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
              AND m.match_date >= DATE '{combat_start_date}'
            USING SAMPLE {combat_density_pct} PERCENT
        )
        SELECT
            p.match_id, p.team_id,
            UPPER(TRIM(CAST(m.region AS VARCHAR))) AS region,
            p.kills, p.assists,
            COALESCE(p.damage_to_champions,0) AS dmg,
            GREATEST(m.game_duration/60.0, 1) AS dur
        FROM main.participants p
        JOIN main.match_info m ON p.match_id = m.match_id
        JOIN sampled_matches s ON p.match_id = s.match_id
        WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
          AND m.match_date >= DATE '{combat_start_date}'
    """).df()
    _write_parquet(df_combat_density, combat_marts_dir / "combat_density_2d.parquet")
    _to_db(conn, df_combat_density, "combat_density_2d", COMBAT_SCHEMA)
    logging.info(f"  ✓ {len(df_combat_density)} строк")

    # --- combat_violin_kda: семпл {combat_violin_pct}% строк для KDA-violin ---
    logging.info(f"--- lol_combat: combat_violin_kda ({combat_violin_pct}% строк) ---")
    df_combat_violin = conn.execute(f"""
        SELECT UPPER(TRIM(CAST(m.region AS VARCHAR))) AS region,
               CAST(p.win AS INT)=1 AS win,
               (p.kills + p.assists) / GREATEST(p.deaths, 1) AS kda
        FROM main.participants p JOIN main.match_info m ON p.match_id = m.match_id
        WHERE m.queue_id = {queue_id} AND m.game_duration >= {combat_remake_sec}
          AND m.match_date >= DATE '{combat_start_date}'
        USING SAMPLE {combat_violin_pct} PERCENT
    """).df()
    _write_parquet(df_combat_violin, combat_marts_dir / "combat_violin_kda.parquet")
    _to_db(conn, df_combat_violin, "combat_violin_kda", COMBAT_SCHEMA)
    logging.info(f"  ✓ {len(df_combat_violin)} строк")

    logging.info(f"✅ lol_combat: 7 витрин созданы")

    # ============================================================
    # ИТОГИ
    # ============================================================
    conn.close()

    logging.info("=" * 60)
    logging.info("✅ TRANSFORM завершён")
    logging.info(f"📁 Витрины lol_meta:          {lol_meta_dir}")
    for f in sorted(lol_meta_dir.glob("*.parquet")):
        size_kb = f.stat().st_size / 1024
        logging.info(f"   • lol_meta/{f.name} ({size_kb:.0f} KB)")
    logging.info(f"📁 Витрины match_overview:     {match_marts_dir}")
    for f in sorted(match_marts_dir.glob("*.parquet")):
        size_kb = f.stat().st_size / 1024
        logging.info(f"   • match_overview/{f.name} ({size_kb:.0f} KB)")
    logging.info(f"📁 Витрины combat:             {combat_marts_dir}")
    for f in sorted(combat_marts_dir.glob("*.parquet")):
        size_kb = f.stat().st_size / 1024
        logging.info(f"   • combat/{f.name} ({size_kb:.0f} KB)")
    logging.info(f"💾 DuckDB: {duckdb_path}")
    logging.info(f"   • схема lol_meta")
    logging.info(f"   • схема lol_match_overview")
    logging.info(f"   • схема lol_combat")
    logging.info("=" * 60)


if __name__ == "__main__":
    main()