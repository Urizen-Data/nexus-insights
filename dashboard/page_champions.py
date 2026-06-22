# ============================================================
# DASHBOARD PAGE 1 — LoL Meta Analytics (Champions)
# ============================================================
# Использует: common.py (стили, цвета, утилиты)
# Все функции progress_bar – локальные, глобальные максимумы – локальные
# Для общего app.py с вкладками
# ============================================================

# ── Импорт библиотек ──
import dash                              # сам Dash
from dash import dcc, html, Input, Output, State, ctx, callback  # компоненты и callback-инструменты
import pandas as pd                      # работа с данными (DataFrame)
import plotly.express as px              # быстрые графики Plotly
import requests                          # HTTP-запросы к Data Dragon API
from functools import lru_cache          # кэширование результатов функций
import plotly.graph_objects as go        # низкоуровневые графики Plotly

# ── ★ Импорт общего модуля (стили, цвета, утилиты, общие компоненты) ──
from common import (
    COLORS, FONT_FAMILY, FS, DB_PATH, QUEUE_ID,       # базовые константы
    CONTENT_MIN_WIDTH, REGION_OPTIONS as BASE_REGION_OPTIONS,  # ширина контента и опции регионов
    LOL_GOLD, LOL_GREEN, LOL_DARK, LOL_TEXT,          # фирменные цвета LoL
    rgba, get_connection, ordered_regions, region_color,  # утилиты: цвет, БД, сортировка
    empty_fig, chart_panel, build_footer, AUTHOR,     # компоненты: заглушка, панель, футер
    APP_STYLE, SECTION_TITLE_STYLE, FILTER_CONTAINER_STYLE,  # стили
    FILTER_GROUP_STYLE, DROPDOWN_STYLE, TABLE_WRAPPER_STYLE,
    TABLE_STYLE, TABLE_HEADER_STYLE, TABLE_CELL_STYLE,
    LABEL_BASE_STYLE, SECTION_H4_STYLE, HINT_STYLE,
    CARD_STYLE, DETAIL_TABLE_STYLE,
    PAGE_TITLE_STYLE, PAGE_SUBTITLE_STYLE, PAGE_STATS_STYLE,
    PAGE_RANGE_STYLE,
    GRAPH_CONFIG as CFG,                             # настройки отображения графиков
    GLOBAL_CSS                                       # глобальные CSS-стили
)


# ═══════════════════════════════════════════════════════
# ЛОКАЛЬНЫЕ ФУНКЦИИ ДЛЯ PROGRESS BAR
# ═══════════════════════════════════════════════════════

# Эти словари будут заполнены ниже в секциях конфигурации и загрузки данных
GLOBAL_MAX = {}          # глобальные максимумы метрик (для нормировки шкал)
BAR_CONFIG = {}          # конфигурация каждой полоски (высота, макс. значение)
THRESHOLDS = {}          # пороги для цветов (зелёный/жёлтый/красный)
TIER_COLORS = {}         # цвета тиров (S+, S, A, B, C) — будет переопределён ниже

def gmax(key, default=1.0):
    """Получить глобальный максимум для метрики, с fallback-значением."""
    try:
        return max(float(GLOBAL_MAX.get(key, default)), 1.0)
    except (ValueError, TypeError):
        return max(float(default), 1.0)

def get_bar_color(value, metric):
    """Определить цвет полоски на основе значения метрики и порогов."""
    cfg = THRESHOLDS.get(metric, {})
    if not cfg:
        return COLORS['primary']                     # если нет конфига — золотой цвет
    if cfg.get('inverse'):                            # inverse: чем меньше, тем лучше
        if value <= cfg['high']: return cfg['color_high']
        return cfg['color_mid'] if value <= cfg['mid'] else cfg['color_low']
    # обычный случай: чем больше, тем лучше
    if value >= cfg['high']: return cfg['color_high']
    return cfg['color_mid'] if value >= cfg['mid'] else cfg['color_low']

def bar_widget(value, max_val, color, *, text=None, width=None, height=18, radius=10, min_text_pct=0):
    """
    Горизонтальная полоска (progress bar) с опциональным текстом поверх.
    Используется в таблице чемпионов и карточках метрик.
    """
    # Вычисляем процент заполнения
    try:
        pct = min(float(value) / float(max_val) * 100, 100) if max_val else 0
    except (ValueError, TypeError):
        pct = 0
    pct = max(0, pct)

    # Внешний контейнер (серый фон)
    outer = {
        'backgroundColor': COLORS['bar_bg'], 'borderRadius': f"{radius}px",
        'height': f"{height}px", 'overflow': 'hidden',
    }
    outer['width'] = f"{width}px" if width else '100%'
    if width:
        outer['display'] = 'inline-block'
    if text is not None:
        outer['position'] = 'relative'               # для абсолютного позиционирования текста

    # Внутренняя заполненная часть (цветная)
    inner = {
        'width': f"{pct:.1f}%", 'backgroundColor': color,
        'height': f"{height}px", 'borderRadius': f"{radius}px",
    }
    children = [html.Div(style=inner)]

    # Текст поверх полоски (значение метрики)
    if text is not None:
        text_layer = {
            'position': 'absolute', 'top': '0', 'left': '0', 'width': '100%',
            'height': f"{height}px", 'display': 'flex', 'alignItems': 'center',
            'justifyContent': 'flex-end', 'paddingRight': '8px',  # число справа
            'boxSizing': 'border-box', 'color': COLORS['text'],
            'fontWeight': 'bold', 'fontSize': f"{FS['sm']}px",
            'pointerEvents': 'none', 'whiteSpace': 'nowrap',
            'fontVariantNumeric': 'tabular-nums',              # ровные цифры
            'textShadow': '0 0 3px rgba(0,0,0,0.7)',          # читаемость на любом фоне
        }
        children.append(html.Div(text, style=text_layer))

    return html.Div(style=outer, children=children)

def progress_bar(value, metric):
    """Полоска для конкретной метрики: сама выбирает цвет и максимум."""
    cfg = BAR_CONFIG.get(metric, {'max': 100, 'metric': metric, 'height': '6px'})
    h = int(cfg['height'].replace('px', ''))
    max_val = gmax(metric, cfg['max']) if metric in BAR_CONFIG else cfg['max']
    return bar_widget(value, max_val, get_bar_color(value, cfg['metric']), height=h, radius=3)

def tier_badge(tier, font_size=None, padding='2px 6px', min_width='28px'):
    """Цветной бейдж тира (S+, S, A, B, C)."""
    fs = font_size if font_size is not None else FS['sm']
    color = TIER_COLORS.get(tier, '#A09B8C')
    return html.Span(tier, style={
        'backgroundColor': color,
        # Для светлых тиров (A, GOLD, SILVER) — тёмный текст, иначе светлый
        'color': COLORS['secondary'] if tier in ('A', 'GOLD', 'SILVER') else COLORS['text'],
        'fontWeight': 'bold', 'fontSize': f"{fs}px", 'padding': padding,
        'borderRadius': '6px', 'display': 'inline-block',
        'minWidth': min_width, 'textAlign': 'center',
    })

def _aggregate(df, agg_map, group_cols):
    """Агрегация DataFrame по заданным колонкам с указанными функциями."""
    return df.groupby(group_cols, as_index=False).agg(agg_map)


# ============================================================
# 1. КОНФИГУРАЦИЯ (специфичная для этой страницы)
# ============================================================

# ── Схема в DuckDB, откуда читаем витрины ──
META_SCHEMA = "lol_meta"

# ── Фильтры по умолчанию ──
DEFAULT_REGION    = 'ALL'
ROLES             = ['ALL', 'TOP', 'JUNGLE', 'MIDDLE', 'BOTTOM', 'UTILITY']
DEFAULT_MIN_GAMES = 300      # значение слайдера по умолчанию
TOP_N             = 10       # сколько строк показывать в таблице

# ── Размеры карточек ──
ICON_SIZE, CARD_WIDTH, CARD_PADDING, CARD_GAP = 75, 155, "10px 10px", 5
CHAMPION_NAME_FONTSIZE = FS['md']            # размер шрифта имени чемпиона в таблице
DETAIL_ICON_SIZE, DETAIL_ICON_PAD = "33px", "2px"

# ── Цвета тиров ──
TIER_COLORS = {
    'S+': '#FF4444', 'S': '#FF8C00', 'A': '#FFD700',
    'B': '#28B06E', 'C': '#A09B8C',
}
TIER_ORDER = ['S+', 'S', 'A', 'B', 'C']

# ── Цвета метрик (для графиков) ──
METRIC_COLORS = {
    'kda': '#3498db', 'kills': '#e74c3c', 'deaths': '#f39c12', 'assists': '#2ecc71',
    'gold': '#FFD700', 'cs': '#9b59b6', 'damage': '#e67e22', 'objective': '#1abc9c',
    'inhibitors': '#e74c3c',
}

# ── Пороги для цветовых шкал (высокое=зелёное, среднее=жёлтое, низкое=красное) ──
THRESHOLDS = {
    # Больше = ЛУЧШЕ
    'winrate':           {'high': 54,    'mid': 50,    'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'kda':               {'high': 3.5,   'mid': 2.5,   'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'kills':             {'high': 7,     'mid': 5,     'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'assists':           {'high': 12,    'mid': 8,     'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'pickrate':          {'high': 8,    'mid': 5,     'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'presence':          {'high': 20,    'mid': 15,    'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'css_score':         {'high': 50,    'mid': 40,    'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'avg_gold':          {'high': 13000, 'mid': 11000, 'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'cs_per_min':        {'high': 8,   'mid': 7.0,   'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'avg_damage':        {'high': 25000, 'mid': 18000, 'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'avg_dragons':       {'high': 0.8,   'mid': 0.4,   'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    'avg_barons':        {'high': 0.3,   'mid': 0.15,  'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
    # Больше = ХУЖЕ (inverse=True)
    'deaths':            {'high': 3,     'mid': 5,     'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F', 'inverse': True},
    'banrate':           {'high': 10,    'mid': 17,    'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F', 'inverse': True},
    # Танковость — больше = зеленее
    'avg_damage_taken':  {'high': 35000, 'mid': 25000, 'color_high': '#28B06E', 'color_mid': '#F0B232', 'color_low': '#B8303F'},
}

# ── Конфиг для progress_bar: макс. значение, метрика для цвета, высота ──
BAR_CONFIG = {
    'winrate':           {'max': 100,   'metric': 'winrate',          'height': '20px'},
    'pickrate':          {'max': 15,    'metric': 'pickrate',         'height': '4px'},
    'banrate':           {'max': 60,    'metric': 'banrate',          'height': '4px'},
    'presence':          {'max': 30,   'metric': 'presence',         'height': '4px'},
    'avg_kills':         {'max': 10,    'metric': 'kills',            'height': '4px'},
    'avg_deaths':        {'max': 8,     'metric': 'deaths',           'height': '4px'},
    'avg_assists':       {'max': 15,    'metric': 'assists',          'height': '4px'},
    'kda':               {'max': 5,     'metric': 'kda',              'height': '4px'},
    'avg_gold':          {'max': 20000, 'metric': 'avg_gold',         'height': '4px'},
    'cs_per_min':        {'max': 12,    'metric': 'cs_per_min',       'height': '4px'},
    'avg_damage':        {'max': 45000, 'metric': 'avg_damage',       'height': '4px'},
    'avg_damage_taken':  {'max': 50000, 'metric': 'avg_damage_taken', 'height': '4px'},
    'avg_dragons':       {'max': 2.0,   'metric': 'avg_dragons',      'height': '4px'},
    'avg_barons':        {'max': 0.5,   'metric': 'avg_barons',       'height': '4px'},
    'css_score':         {'max': 100,   'metric': 'css_score',        'height': '6px'},
}

# ── Заголовки таблицы чемпионов (id, label, иконка, ширина колонки) ──
TABLE_HEADERS = [
    {'id': None,              'label': '#',          'icon': '',   'width': '30px'},
    {'id': None,              'label': '',           'icon': '',   'width': '40px'},
    {'id': 'tier',            'label': 'TIER',       'icon': '🏅', 'width': '40px'},
    {'id': 'champion_name',   'label': 'CHAMPION',   'icon': '🧙', 'width': '80px'},
    {'id': 'games',           'label': 'GAMES',      'icon': '🎮', 'width': '50px'},
    {'id': 'winrate',         'label': 'WIN RATE',   'icon': '🏆', 'width': '80px'},
    {'id': 'pickrate',        'label': 'PICK RATE',  'icon': '🔥', 'width': '50px'},
    {'id': 'banrate',         'label': 'BAN RATE',   'icon': '🚫', 'width': '50px'},
    {'id': 'presence',        'label': 'PRESENCE',   'icon': '👁', 'width': '50px'},
    {'id': 'css_score',       'label': 'STRENGTH',   'icon': '⚡', 'width': '50px'},
    {'id': 'avg_kills',       'label': 'K',          'icon': '🗡️', 'width': '30px'},
    {'id': 'avg_deaths',      'label': 'D',          'icon': '💀', 'width': '30px'},
    {'id': 'avg_assists',     'label': 'A',          'icon': '🤝', 'width': '30px'},
    {'id': 'kda',             'label': 'KDA',        'icon': '⭐', 'width': '50px'},
    {'id': 'avg_gold',        'label': 'GOLD',       'icon': '💰', 'width': '40px'},
    {'id': 'cs_per_min',      'label': 'CS/MIN',     'icon': '⚔️', 'width': '40px'},
    {'id': 'avg_damage',      'label': 'DMG',        'icon': '💥', 'width': '40px'},
    {'id': 'avg_damage_taken','label': 'DMG TAKEN',  'icon': '🛡️', 'width': '40px'},
    {'id': 'avg_dragons',     'label': 'DRAG',       'icon': '🐉', 'width': '40px'},
    {'id': 'avg_barons',      'label': 'BARON',      'icon': '👑', 'width': '40px'},
]
# Маппинг id колонок для сортировки (только те, у которых id не None)
HEADER_COLUMN_MAP = {h['id']: h['id'] for h in TABLE_HEADERS if h['id']}

# ── Списки числовых колонок и словари агрегаций ──
AGG_NUMERIC = ['avg_kills', 'avg_deaths', 'avg_assists', 'winrate', 'kda',
               'avg_gold', 'cs_per_min', 'avg_damage', 'avg_damage_taken',
               'avg_dragons', 'avg_barons']
AGG_BEST    = {'games': 'sum', 'avg_kills': 'mean', 'avg_deaths': 'mean',
               'avg_assists': 'mean', 'winrate': 'mean', 'kda': 'mean',
               'pickrate': 'mean', 'banrate': 'mean'}
AGG_FULL    = {'games': 'sum', **{c: 'mean' for c in AGG_NUMERIC},
               'pickrate': 'mean', 'banrate': 'mean'}
AGG_DETAIL  = {'games': 'sum', **{c: 'mean' for c in AGG_NUMERIC}}
_AGG = {'best': AGG_BEST, 'full': AGG_FULL, 'detail': AGG_DETAIL}

# ── Оси радар-диаграммы (Playstyle) ──
RADAR_AXES = [
    ("💥 Damage",     'm_damage'),
    ("🛡️ Tank",       'm_tank'),
    ("💚 Sustain",    'm_sustain'),
    ("⚔️ Aggro",      'm_aggression'),
    ("🤝 Team",       'm_teamplay'),
    ("💰 Economy",    'm_economy'),
    ("👁️ Vision",     'm_vision'),
    ("🎯 Objects",    'm_objectives'),
]

# ── Подсказки для осей радара (описание и метод расчёта) ──
RADAR_HINTS = {
    "💥 Damage":    {"desc": "Damage dealt to enemy champions",          "calc": "Avg damage to champions per game,<br>ranked as percentile vs all champions"},
    "🛡️ Tank":      {"desc": "Damage absorbed by the champion",          "calc": "Avg damage taken per game,<br>ranked as percentile vs all champions"},
    "💚 Sustain":   {"desc": "Healing & self-sustain capability",        "calc": "Avg total healing per game,<br>ranked as percentile vs all champions"},
    "⚔️ Aggro":     {"desc": "Early aggression & kill pressure",         "calc": "Avg kills + (First Blood rate ÷ 10),<br>ranked as percentile vs all champions"},
    "🤝 Team":      {"desc": "Team contribution & playmaking",           "calc": "Avg assists per game,<br>ranked as percentile vs all champions"},
    "💰 Economy":   {"desc": "Gold income & farming efficiency",         "calc": "(Avg gold ÷ 100) + CS per minute,<br>ranked as percentile vs all champions"},
    "👁️ Vision":    {"desc": "Map control & vision impact",              "calc": "Avg vision score + wards placed,<br>ranked as percentile vs all champions"},
    "🎯 Objects":   {"desc": "Objective control (epic monsters & structures)", "calc": "Avg dragons + barons + turrets,<br>ranked as percentile vs all champions"},
}


# ============================================================
# 2. СТИЛИ (специфичные для этой страницы)
# ============================================================

TABLE_IDX_STYLE = {
    'textAlign': 'center', 'padding': '8px 4px', 'fontWeight': 'bold',
    'color': COLORS['primary'],
}
TABLE_VAL_STYLE = {
    'textAlign': 'center', 'padding': '8px 4px', 'color': COLORS['text'],
}
TABLE_ROW_STYLE = {'borderBottom': f"1px solid {COLORS['border']}"}
TH_BTN_STYLE = {
    'width': '100%', 'background': 'transparent', 'border': 'none',
    'padding': '0', 'margin': '0', 'color': 'inherit', 'font': 'inherit',
    'cursor': 'pointer', 'textAlign': 'center',
}
CARD_ROW_STYLE = {'display': 'flex', 'alignItems': 'center', 'gap': '12px'}
CARD_TITLE_STYLE = {
    'fontSize': f"{FS['md']}px", 'color': COLORS['primary'], 'marginBottom': '6px',
}

# ── Стиль «свитка легенды» для лора чемпиона ──
LORE_BOX_STYLE = {
    'position': 'relative', 'height': '100%', 'boxSizing': 'border-box',
    'background': 'linear-gradient(160deg, rgba(30,40,45,0.95) 0%, rgba(10,17,20,0.97) 100%)',
    'border': f"1px solid {LOL_GOLD}", 'borderRadius': '12px', 'padding': '16px 22px',
    'boxShadow': f"0 0 22px {rgba(LOL_GOLD, 0.20)}, inset 0 0 28px rgba(0,0,0,0.5)",
    'minWidth': '0', 'overflow': 'hidden', 'display': 'flex', 'flexDirection': 'column',
}


# ============================================================
# 3. ЗАГРУЗКА ДАННЫХ — чтение витрин из DuckDB
# ============================================================

# ── Подключаемся к DuckDB через общую функцию ──
conn = get_connection()

# ── ★ Читаем готовые витрины из схемы lol_meta ──
df_all     = conn.execute(f"SELECT * FROM {META_SCHEMA}.df_all").df()
df_by_role = conn.execute(f"SELECT * FROM {META_SCHEMA}.df_by_role").df()
df_items   = conn.execute(f"SELECT * FROM {META_SCHEMA}.df_items").df()
SPELLS_DF  = conn.execute(f"SELECT * FROM {META_SCHEMA}.SPELLS_DF").df()
EXTRA_DF   = conn.execute(f"SELECT * FROM {META_SCHEMA}.EXTRA_DF").df().set_index('champion_name')
RADAR_BASE = conn.execute(f"SELECT * FROM {META_SCHEMA}.RADAR_BASE").df().set_index('champion_name')

# ── ★ Справочники из main (Data Dragon) ──
ITEMS_NAMES = dict(conn.execute("SELECT item_id, item_name FROM main.items").df().values)
champions_df = conn.execute("SELECT * FROM main.champions").df()
ITEMS_TAGS = dict(conn.execute("SELECT item_id, item_tags FROM main.items").df().values)
SPELL_DESC = dict(conn.execute("SELECT spell_id, spell_description FROM main.spells").df().values)

# ── ★ Заклинания: имя + путь к иконке из справочника main.spells (НЕ из сети) ──
#    spell_image хранится как "{version}/img/spell/SummonerFlash.png"
_spells_raw = conn.execute(
    "SELECT spell_id, spell_name, spell_image FROM main.spells"
).df()
SPELL_NAMES  = {int(r.spell_id): r.spell_name  for r in _spells_raw.itertuples()}
SPELL_IMAGES = {int(r.spell_id): r.spell_image for r in _spells_raw.itertuples()}

# ── ★ Мета-данные для шапки дашборда ──
min_date, max_date = conn.execute(
    "SELECT MIN(match_date), MAX(match_date) FROM main.match_info"
).fetchone()
TOTAL_MATCHES = conn.execute(
    f"SELECT COUNT(DISTINCT match_id) FROM main.match_info WHERE queue_id={QUEUE_ID}"
).fetchone()[0]
TOTAL_PLAYERS = conn.execute("SELECT COUNT(DISTINCT puuid) FROM main.participants").fetchone()[0]
TOTAL_CHAMPIONS = conn.execute("SELECT COUNT(DISTINCT champion_name) FROM main.participants").fetchone()[0]

conn.close()

# ── ★ Версия Data Dragon для иконок (кэшируется) ──
@lru_cache(maxsize=1)
def get_dd_version():
    """Получить последнюю версию Data Dragon."""
    try:
        return requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=5).json()[0]
    except Exception:
        return "16.11.1"              # fallback если API недоступен

DD_VERSION = get_dd_version()
DD_BASE = f"https://ddragon.leagueoflegends.com/cdn/{DD_VERSION}/img/champion"
print(f"✅ Data Dragon: {DD_VERSION}")

# ── ★ Опции для фильтра региона ──
REGION_OPTIONS = [{'label': 'ALL', 'value': 'ALL'}] + [
    {'label': r, 'value': r} for r in sorted(df_all['region'].unique())
]
ALL_CHAMPIONS = sorted(df_all['champion_name'].unique())
print(f"✅ Витрины lol_meta загружены: {df_all.shape[0]} строк")

# ── ★ Расчёт значений для слайдера Min Games ──
_games_per_champion = df_all.groupby('champion_name')['games'].sum()
AVG_GAMES = int(round(_games_per_champion.mean())) if not _games_per_champion.empty else 50
SLIDER_MIN = 1
SLIDER_MAX = AVG_GAMES * 2
DEFAULT_MIN_GAMES = min(DEFAULT_MIN_GAMES, SLIDER_MAX)

_M = {'color': COLORS['text_muted'], 'fontSize': f"{FS['sm']}px"}
SLIDER_MARKS = {
    SLIDER_MIN:                 {'label': str(SLIDER_MIN),                 'style': _M},
    AVG_GAMES // 2:             {'label': str(AVG_GAMES // 2),             'style': _M},
    AVG_GAMES:                  {'label': f"⌀ {AVG_GAMES}",
                                 'style': {'color': LOL_GOLD, 'fontSize': f"{FS['sm']}px",
                                           'fontWeight': 'bold'}},
    AVG_GAMES + AVG_GAMES // 2: {'label': str(AVG_GAMES + AVG_GAMES // 2), 'style': _M},
    SLIDER_MAX:                 {'label': str(SLIDER_MAX),                 'style': _M},
}
print(f"✅ Avg games: {AVG_GAMES} | Slider: {SLIDER_MIN}–{SLIDER_MAX}")

# ── ★ Заклинания берутся из main.spells (SPELL_NAMES / SPELL_IMAGES, см. выше) ──
#    Онлайн-запрос summoner.json удалён: при сбое сети он возвращал {},
#    из-за чего иконки заклинаний не отображались.

def _gmax(col, default):
    """Максимум по колонке во всём df_all, с fallback-значением."""
    m = df_all[col].max() if not df_all.empty else 0
    return m if m and m > 0 else default

# Заполняем глобальные максимумы (реальные данные из витрин)
GLOBAL_MAX = {
    # Вычисляются из данных (реальный максимум по всем чемпионам)
    'avg_gold':         _gmax('avg_gold',          20000),
    'avg_damage':       _gmax('avg_damage',        50000),
    'cs_per_min':       _gmax('cs_per_min',           12),
    'avg_dragons':      _gmax('avg_dragons',         2.0),
    'avg_barons':       _gmax('avg_barons',          0.5),
    'kda':              _gmax('kda',                   5),
    'avg_kills':        _gmax('avg_kills',            10),
    'avg_assists':      _gmax('avg_assists',          15),
    'avg_deaths':       _gmax('avg_deaths',            8),
    'pickrate':         _gmax('pickrate',            100),
    'banrate':          _gmax('banrate',             100),
    'winrate':          _gmax('winrate',             100),
    'avg_damage_taken': _gmax('avg_damage_taken',  50000),

    # Жёстко заданные (метрика ограничена диапазоном или нет колонки в df_all)
    'avg_vision':       100,
    'avg_turrets':        5,
    'avg_inhibitors':     2,
    'css_score':        85,
    'presence':         30,
}


# ============================================================
# 4. УТИЛИТЫ (специфичные для этой страницы)
# ============================================================

# ── Исправление имён чемпионов для URL Data Dragon ──
_NAME_FIX = {'FiddleSticks': 'Fiddlesticks'}

def _fix_name(name):
    """Исправить имя чемпиона для корректного URL."""
    return _NAME_FIX.get(name, name)

def get_icon(name):
    """URL иконки чемпиона."""
    return f"{DD_BASE}/{_fix_name(name)}.png"

def get_champion_portrait(name):
    """URL портрета чемпиона (для детальной карточки)."""
    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{_fix_name(name)}_0.jpg"

def get_item_icon(item_id):
    """URL иконки предмета."""
    return f"https://ddragon.leagueoflegends.com/cdn/{DD_VERSION}/img/item/{item_id}.png"

def get_item_name(item_id):
    """Название предмета по ID."""
    return ITEMS_NAMES.get(item_id, f"Item {item_id}")

def get_spell_name(spell_id):
    """Название заклинания по ID (из main.spells, не из сети)."""
    return SPELL_NAMES.get(int(spell_id), f"Spell {spell_id}")

def get_spell_icon(spell_id):
    """URL иконки заклинания (из main.spells, не из сети)."""
    img = SPELL_IMAGES.get(int(spell_id))   # напр. "15.x.x/img/spell/SummonerFlash.png"
    if img:
        return f"https://ddragon.leagueoflegends.com/cdn/{img}"
    return ""

def champ_icon(name, *, size=48, radius=8, border=None, extra_style=None):
    """HTML-компонент иконки чемпиона (кликабельный)."""
    style = {
        'width': f"{size}px", 'height': f"{size}px",
        'borderRadius': f"{radius}px", 'display': 'block', 'cursor': 'pointer',
    }
    if border:
        style['border'] = border
    if extra_style:
        style.update(extra_style)
    return html.Img(
        src=get_icon(name),
        id={'type': 'champion-select', 'index': name},   # id для callback-ов
        n_clicks=0, style=style,
    )

def get_item_tooltip(item_id):
    """Всплывающая подсказка для предмета."""
    name = get_item_name(item_id)
    tags = ITEMS_TAGS.get(item_id, '')
    return f"{name}\nTags: {tags}" if tags else name

def get_spell_tooltip(spell_id):
    """Всплывающая подсказка для заклинания."""
    name = get_spell_name(spell_id)
    desc = SPELL_DESC.get(spell_id, '')
    return f"{name}\n{desc}" if desc else name


def _add_meta_cols(df):
    """
    Добавляет динамические колонки: presence, css_score, PBI, Tier.
    ★ Зависит от текущего среза (нормировка внутри фильтра).
    """
    df = df.copy()
    # Presence = Pick% + Ban%
    df['presence'] = (df['pickrate'] + df['banrate']).clip(upper=100).round(1)

    # Взвешенный средний винрейт для PBI
    if not df.empty and df['games'].sum() > 0:
        avg_wr = (df['winrate'] * df['games']).sum() / df['games'].sum()
    else:
        avg_wr = 50.0

    def _norm(col):
        """Нормировка колонки в [0, 1]."""
        lo, hi = df[col].min(), df[col].max()
        return (df[col] - lo) / (hi - lo) if hi > lo else df[col] * 0

    # Strength Score = WR×0.5 + Pick×0.3 + Ban×0.2 (нормированные, масштаб 0-100)
    if not df.empty and len(df) > 1:
        df['css_score'] = (
            (_norm('winrate') * 0.5 + _norm('pickrate') * 0.3
             + _norm('banrate') * 0.2) * 100
        ).round(2)
    else:
        df['css_score'] = df['winrate'].round(2) if not df.empty else pd.Series(dtype=float)

    # PBI = (WR − avg_WR) × Pick% / (100 − Ban%)
    denom = (100 - df['banrate']).replace(0, 1)
    df['pbi'] = ((df['winrate'] - avg_wr) * df['pickrate'] / denom).round(3)

    # Tier — на основе квантилей css_score
    if not df.empty:
        q = df['css_score'].quantile([0.20, 0.50, 0.80, 0.95]).tolist()
        bins, eps = [-float('inf')] + q + [float('inf')], 1e-9
        for i in range(1, len(bins)):
            if bins[i] <= bins[i - 1]:
                bins[i] = bins[i - 1] + eps           # гарантируем строгое возрастание
        df['tier'] = pd.cut(
            df['css_score'], bins=bins,
            labels=['C', 'B', 'A', 'S', 'S+'], include_lowest=True,
        ).astype(str)
    else:
        df['tier'] = 'C'
    return df


@lru_cache(maxsize=256)
def get_filtered_data(region, role, agg='full', min_games=1):
    """
    Возвращает отфильтрованный и агрегированный DataFrame.
    ★ Результат кэшируется lru_cache для быстрых повторных вызовов.
    """
    agg_map = _AGG[agg]
    if region == 'ALL':
        base = df_all if role == 'ALL' else df_by_role[df_by_role['team_position'] == role]
        result = _aggregate(base, agg_map, 'champion_name')
    elif role == 'ALL':
        result = df_all[df_all['region'] == region]
    else:
        result = df_by_role[
            (df_by_role['region'] == region) & (df_by_role['team_position'] == role)
        ]
    return _add_meta_cols(result[result['games'] >= min_games])


def get_role_data(champion_name, region):
    """Данные по ролям для конкретного чемпиона."""
    if region == 'ALL':
        data = _aggregate(
            df_by_role[df_by_role['champion_name'] == champion_name],
            AGG_DETAIL, ['champion_name', 'team_position'],
        )
    else:
        data = df_by_role[
            (df_by_role['champion_name'] == champion_name) & (df_by_role['region'] == region)
        ]
    data = data[(data['team_position'] != '') & (data['games'] > 0)]
    return data.sort_values('games', ascending=False)


# ============================================================
# 5. КОМПОНЕНТЫ — карточки лидеров
# ============================================================

def champion_card(champ, title, value, unit, color, label):
    """Карточка чемпиона-лидера по конкретной метрике."""
    return html.Div(style=CARD_STYLE, children=[
        html.Div(title, style=CARD_TITLE_STYLE),
        html.Div([
            champ_icon(champ['champion_name'], size=ICON_SIZE, radius=10,
                       extra_style={'marginRight': '2px'}),
            html.Div([
                html.Div(champ['champion_name'],
                         style={'fontWeight': 'bold', 'fontSize': f"{FS['md']}px",
                                'color': COLORS['text']}),
                html.Div(html.Span(f"{value}{unit}",
                         style={'fontSize': f"{FS['xl']}px", 'fontWeight': 'bold',
                                'color': color})),
                html.Div(label, style={'fontSize': f"{FS['md']}px",
                                       'color': COLORS['text_muted']}),
            ])
        ], style=CARD_ROW_STYLE)
    ])


def strength_card(champ):
    """Карточка сильнейшего чемпиона (по css_score)."""
    return html.Div(style={
        **CARD_STYLE, 'border': f"2px solid {COLORS['primary']}",
        'background': f"linear-gradient(180deg, {COLORS['card_bg']}, rgba(0,0,0,0.25))",
    }, children=[
        html.Div("⚡ STRONGEST", style={**CARD_TITLE_STYLE, 'fontWeight': 'bold'}),
        html.Div([
            champ_icon(champ['champion_name'], size=ICON_SIZE, radius=10,
                       border=f"2px solid {COLORS['primary']}",
                       extra_style={'marginRight': '2px'}),
            html.Div([
                html.Div(champ['champion_name'],
                         style={'fontWeight': 'bold', 'fontSize': f"{FS['md']}px",
                                'color': COLORS['text']}),
                html.Div(html.Span(f"{champ['css_score']:.1f}",
                         style={'fontSize': f"{FS['xl']}px", 'fontWeight': 'bold',
                                'color': COLORS['primary']})),
                html.Div("strength score",
                         style={'fontSize': f"{FS['md']}px", 'color': COLORS['text_muted']}),
            ])
        ], style=CARD_ROW_STYLE)
    ])


def best_champions_row(region, role, min_games=1):
    """Ряд из 6 карточек: Strongest + 5 лидеров по метрикам."""
    df = get_filtered_data(region, role, 'best', min_games)
    if df.empty:
        return html.Div(
            f"⚠️ No champions with ≥ {min_games} games",
            style={'color': COLORS['text_muted'], 'textAlign': 'center',
                   'padding': '20px', 'fontSize': f"{FS['md']}px"},
        )
    # Какие метрики показываем
    picks = [
        ('winrate',   '🏆 BEST WINNER',  '%', COLORS['winrate'],  'winrate',   '.1f'),
        ('pickrate',  '🔥 MOST POPULAR', '%', COLORS['pickrate'], 'pickrate',  '.1f'),
        ('banrate',   '🚫 MOST BANNED',  '%', COLORS['banrate'],  'banrate',   '.1f'),
        ('kda',       '⭐ BEST KDA',      '', COLORS['kda'],       'kda',       '.2f'),
        ('avg_kills', '🗡️ BEST KILLER',   '', COLORS['kills'],     'avg kills', '.1f'),
    ]
    cards = []
    if 'css_score' in df.columns:
        cards.append(strength_card(df.nlargest(1, 'css_score').iloc[0]))
    for col, title, unit, color, label, fmt in picks:
        best = df.nlargest(1, col).iloc[0]
        cards.append(champion_card(best, title, format(best[col], fmt), unit, color, label))
    return html.Div(
        cards,
        style={'display': 'grid',
               'gridTemplateColumns': f"repeat(auto-fit, minmax({CARD_WIDTH}px, 1fr))",
               'gap': f"{CARD_GAP}px", 'marginBottom': '20px'},
    )


# ============================================================
# 6. КОМПОНЕНТЫ — Rank List
# ============================================================

def build_tier_list(df):
    """Визуальный список чемпионов по тирам (S+ → C)."""
    if df.empty:
        return html.Div("No data", style={'color': COLORS['text_muted'], 'textAlign': 'center'})

    # Цвета фона для каждого тира
    tier_bg = {
        'S+': 'rgba(255,68,68,0.12)', 'S': 'rgba(255,140,0,0.12)',
        'A': 'rgba(255,215,0,0.10)', 'B': 'rgba(40,176,110,0.10)',
        'C': 'rgba(160,155,140,0.07)',
    }

    rows = []
    for tier in TIER_ORDER:
        champs = df[df['tier'] == tier].sort_values('css_score', ascending=False)
        if champs.empty:
            continue
        icons = []
        for _, r in champs.iterrows():
            icons.append(html.Div([
                champ_icon(r['champion_name'], size=48, radius=8,
                           border=f"2px solid {TIER_COLORS[tier]}"),
                html.Div(r['champion_name'],
                         style={'fontSize': f"{FS['sm']}px", 'color': COLORS['text_muted'],
                                'textAlign': 'center', 'marginTop': '2px',
                                'maxWidth': '52px', 'overflow': 'hidden',
                                'textOverflow': 'ellipsis', 'whiteSpace': 'nowrap'}),
                html.Div(f"{r['winrate']:.1f}%",
                         style={'fontSize': f"{FS['sm']}px", 'color': TIER_COLORS[tier],
                                'textAlign': 'center', 'fontWeight': 'bold'}),
            ], title=(f"{r['champion_name']} | WR:{r['winrate']:.1f}% | "
                      f"Pick:{r['pickrate']:.1f}% | Ban:{r['banrate']:.1f}% | "
                      f"Strength:{r['css_score']:.1f}"),
                style={'cursor': 'pointer', 'margin': '3px'}))

        rows.append(html.Div([
            html.Div(tier, style={
                'width': '52px', 'minWidth': '52px', 'height': '60px',
                'backgroundColor': TIER_COLORS[tier],
                'color': LOL_DARK if tier == 'A' else LOL_TEXT,
                'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
                'fontWeight': 'bold', 'fontSize': f"{FS['xl']}px", 'borderRadius': '8px',
                'marginRight': '14px', 'flexShrink': 0,
            }),
            html.Div(icons, style={'display': 'flex', 'flexWrap': 'wrap',
                                   'alignItems': 'center', 'gap': '4px', 'flex': 1}),
        ], style={'display': 'flex', 'alignItems': 'center',
                  'padding': '8px 12px', 'marginBottom': '4px',
                  'borderRadius': '8px', 'backgroundColor': tier_bg[tier],
                  'border': f"1px solid {TIER_COLORS[tier]}33"}))

    return html.Div([
        html.H4("🏅 RANK LIST", style=SECTION_H4_STYLE),
        html.P("Ranked by Champion Strength Score "
               "(normalized WR × 0.5 + Pick × 0.3 + Ban × 0.2, scaled 0–100) "
               "| 👆 Click an icon to open details", style=HINT_STYLE),
        *rows
    ], style={'backgroundColor': COLORS['secondary'], 'borderRadius': '12px',
              'padding': '20px', 'border': f"1px solid {COLORS['border']}",
              'marginBottom': '20px', 'width': '100%', 'boxSizing': 'border-box'})


# ============================================================
# 7. КОМПОНЕНТЫ — Tier Distribution (гистограмма тиров)
# ============================================================

def build_tier_distribution(df, selected_tiers=None):
    """Гистограмма распределения чемпионов по тирам (кликабельная)."""
    if df.empty:
        return html.Div("No data", style={'color': COLORS['text_muted'], 'textAlign': 'center'})

    selected_tiers = selected_tiers or []
    counts  = df['tier'].value_counts().reindex(TIER_ORDER, fill_value=0)
    max_cnt = max(int(counts.max()), 1)
    total   = int(counts.sum())

    rows = []
    for tier in TIER_ORDER:
        cnt    = int(counts[tier])
        color  = TIER_COLORS[tier]
        is_sel = tier in selected_tiers
        dimmed = bool(selected_tiers) and not is_sel          # затемняем невыбранные
        pct    = (cnt / total * 100) if total else 0

        badge = html.Div(tier, style={
            'width': '54px', 'minWidth': '54px', 'height': '54px',
            'backgroundColor': color,
            'color': LOL_DARK if tier == 'A' else LOL_TEXT,
            'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
            'fontWeight': 'bold', 'fontSize': f"{FS['xl']}px", 'borderRadius': '12px',
            'flexShrink': 0, 'opacity': 0.35 if dimmed else 1,
            'boxShadow': f"0 0 14px {color}" if is_sel else 'none',
        })

        bar = bar_widget(cnt, max_cnt, color, text=f"{cnt}", height=40, radius=20, min_text_pct=18)

        rows.append(html.Tr([
            html.Td(badge, style={'padding': '10px 8px', 'width': '70px', 'verticalAlign': 'middle'}),
            html.Td(bar,   style={'padding': '10px 8px', 'verticalAlign': 'middle',
                                  'opacity': 0.35 if dimmed else 1}),
            html.Td(f"{pct:.0f}%",
                    style={'padding': '10px 8px', 'width': '52px', 'textAlign': 'right',
                           'verticalAlign': 'middle', 'color': color, 'fontWeight': 'bold',
                           'fontSize': f"{FS['md']}px", 'opacity': 0.35 if dimmed else 1}),
        ], id={'type': 'tier-dist-row', 'tier': tier}, n_clicks=0,
            style={'cursor': 'pointer', 'borderBottom': f"1px solid {COLORS['border']}"}))

    if selected_tiers:
        chosen = " · ".join(t for t in TIER_ORDER if t in selected_tiers)
        hint   = f"🔎 Showing: {chosen}  —  click to toggle, re-click to remove"
    else:
        hint = "👆 Click rows to filter the scatter (multi-select)"

    return html.Div([
        html.H4("TIER DISTRIBUTION",
                style={'color': COLORS['primary'], 'fontSize': f"{FS['md']}px",
                       'fontWeight': 'bold', 'textAlign': 'center',
                       'marginBottom': '14px', 'letterSpacing': '1px'}),
        html.Table([html.Tbody(rows)],
                   style={'width': '100%', 'borderCollapse': 'separate',
                          'borderSpacing': '0 6px', 'tableLayout': 'fixed',
                          'color': LOL_TEXT, 'flex': '1'}),
        html.P(hint, style={'textAlign': 'center', 'color': COLORS['text_muted'],
                            'fontSize': f"{FS['sm']}px", 'fontStyle': 'italic',
                            'marginTop': '10px', 'marginBottom': '0'}),
    ], style={'backgroundColor': COLORS['secondary'], 'borderRadius': '8px',
              'border': f"1px solid {COLORS['border']}", 'padding': '20px',
              'height': '500px', 'boxSizing': 'border-box', 'display': 'flex',
              'flexDirection': 'column', 'justifyContent': 'space-between'})


# ============================================================
# 8. КОМПОНЕНТЫ — Scatter WR vs Pick Rate
# ============================================================

def build_scatter(df, selected_tiers=None):
    """Диаграмма рассеяния: Win Rate vs Pick Rate (размер пузырька = Ban Rate)."""
    if df.empty:
        return html.Div("No data", style={'color': COLORS['text_muted'], 'textAlign': 'center'})

    selected_tiers = selected_tiers or []
    if selected_tiers:
        df = df[df['tier'].isin(selected_tiers)].copy()
        if df.empty:
            chosen = ", ".join(selected_tiers)
            return html.Div(
                f"No champions in tier(s) {chosen}",
                style={'color': COLORS['text_muted'], 'textAlign': 'center', 'padding': '40px'},
            )

    # Средние значения для опорных линий
    avg_wr   = df['winrate'].mean()
    avg_pick = df['pickrate'].mean()

    fig = px.scatter(
        df, x='pickrate', y='winrate', size='banrate', size_max=45,
        color='tier', color_discrete_map=TIER_COLORS,
        hover_name='champion_name',
        hover_data={'pickrate': ':.1f', 'winrate': ':.1f', 'banrate': ':.1f',
                    'presence': ':.1f', 'css_score': ':.1f', 'tier': True},
        text='champion_name', category_orders={'tier': TIER_ORDER},
        labels={'pickrate': 'Pick Rate %', 'winrate': 'Win Rate %', 'tier': 'Tier'},
    )

    # Опорные линии (средние)
    fig.add_hline(y=avg_wr,   line_dash='dash', line_color='rgba(200,170,110,0.4)', line_width=1)
    fig.add_vline(x=avg_pick, line_dash='dash', line_color='rgba(200,170,110,0.4)', line_width=1)

    # Подписи квадрантов
    x_max, y_max, y_min = df['pickrate'].max() * 1.05, df['winrate'].max(), df['winrate'].min()
    for txt, x, y, anchor in [
        ('🔥 OVERPOWERED',      x_max, y_max,       'right'),
        ('🕵️ HIDDEN OP',         0,     y_max,       'left'),
        ('📉 POPULAR BUT WEAK', x_max, y_min + 0.3, 'right'),
        ('💤 WEAK',              0,     y_min + 0.3, 'left'),
    ]:
        fig.add_annotation(x=x, y=y, text=txt, showarrow=False,
                           font={'color': 'rgba(160,155,140,0.6)', 'size': FS['xs']},
                           xanchor=anchor)

    fig.update_traces(textposition='top center',
                      textfont={'size': FS['xs'], 'color': COLORS['text_muted']},
                      marker={'line': {'width': 1, 'color': COLORS['border']}})

    sub = f"  —  {', '.join(t for t in TIER_ORDER if t in selected_tiers)}" if selected_tiers else ""
    fig.update_layout(
        paper_bgcolor=COLORS['secondary'], plot_bgcolor=COLORS['header_bg'],
        font={'family': FONT_FAMILY, 'color': COLORS['text']},
        title={'text': f'WIN RATE vs PICK RATE  (bubble size = BAN RATE){sub}',
               'font': {'color': COLORS['primary'], 'size': FS['lg'], 'weight': 'bold'},
               'x': 0.5},
        showlegend=False,
        xaxis={'gridcolor': COLORS['border'], 'zerolinecolor': COLORS['border']},
        yaxis={'gridcolor': COLORS['border'], 'zerolinecolor': COLORS['border']},
        margin={'l': 50, 'r': 30, 't': 50, 'b': 50}, height=500,
    )

    return html.Div([
        dcc.Graph(id='scatter-graph', figure=fig, config=CFG,
                  style={'borderRadius': '8px', 'border': f"1px solid {COLORS['border']}"}),
    ], style={'width': '100%'})


# ============================================================
# 9. КОМПОНЕНТЫ — таблица чемпионов
# ============================================================

def make_table(df_filtered, sort_col='winrate', sort_dir='desc'):
    """Интерактивная таблица топ-10 чемпионов с сортировкой по колонкам."""
    def arrow(col):
        """Стрелка сортировки."""
        if sort_col == col:
            return " ▼" if sort_dir == 'desc' else " ▲"
        return "   "

    def vspan(text):
        """Жирный span для текста метрики."""
        return html.Span(text, style={'fontWeight': 'bold', 'marginRight': '6px',
                                      'color': COLORS['text']})

    def bar_cell(row, col):
        """Ячейка с полоской progress bar."""
        is_int = col in ('avg_gold', 'avg_damage', 'avg_damage_taken')
        val    = row[col]
        if is_int:
            txt = f"{int(val):,}"
        elif col in ('winrate', 'pickrate', 'banrate', 'presence'):
            txt = f"{val:.1f}%"
        elif col == 'css_score':
            txt = f"{val:.1f}"
        else:
            txt = f"{val:.2f}"
        return html.Td([vspan(txt), progress_bar(val, col)],
                       style={'padding': '6px 4px', 'minWidth': '80px'})

    rows = []
    for i, (_, row) in enumerate(df_filtered.iterrows(), 1):
        champ_btn = html.Button(
            row['champion_name'],
            id={'type': 'champion-select', 'index': row['champion_name']},
            style={'background': 'transparent', 'border': 'none',
                   'color': COLORS['primary'], 'cursor': 'pointer', 'fontWeight': 'bold',
                   'fontSize': f"{CHAMPION_NAME_FONTSIZE}px", 'fontFamily': FONT_FAMILY},
        )
        rows.append(html.Tr([
            html.Td(f"{i}", style=TABLE_IDX_STYLE),
            html.Td(champ_icon(row['champion_name'], size=48, radius=8,
                               border=f"2px solid {COLORS['primary']}"),
                    style={'textAlign': 'center'}),
            html.Td(tier_badge(row.get('tier', 'C')),
                    style={'textAlign': 'center', 'padding': '6px 4px'}),
            html.Td(champ_btn, style=TABLE_CELL_STYLE),
            html.Td(f"{int(row['games'])}", style=TABLE_VAL_STYLE),
            bar_cell(row, 'winrate'),
            bar_cell(row, 'pickrate'),
            bar_cell(row, 'banrate'),
            bar_cell(row, 'presence'),
            bar_cell(row, 'css_score'),
            html.Td(f"{row['avg_kills']:.1f}",   style=TABLE_VAL_STYLE),
            html.Td(f"{row['avg_deaths']:.1f}",  style=TABLE_VAL_STYLE),
            html.Td(f"{row['avg_assists']:.1f}", style=TABLE_VAL_STYLE),
            html.Td(f"{row['kda']:.2f}",         style=TABLE_VAL_STYLE),
            bar_cell(row, 'avg_gold'),
            bar_cell(row, 'cs_per_min'),
            bar_cell(row, 'avg_damage'),
            bar_cell(row, 'avg_damage_taken'),
            bar_cell(row, 'avg_dragons'),
            bar_cell(row, 'avg_barons'),
        ], style=TABLE_ROW_STYLE))

    header_cells = []
    for h in TABLE_HEADERS:
        if h['id'] is None:
            header_cells.append(html.Th(
                "#" if h['label'] == '#' else "", style=TABLE_HEADER_STYLE,
            ))
        else:
            header_cells.append(html.Th(
                html.Button(
                    f"{h['icon']} {h['label']}{arrow(h['id'])}",
                    id={'type': 'table-header', 'column': h['id']},
                    n_clicks=0, style=TH_BTN_STYLE,
                ),
                style={**TABLE_HEADER_STYLE, 'minWidth': h['width']},
            ))

    return html.Div([
        html.H4("🏆 CHAMPION STATISTICS", style=SECTION_H4_STYLE),
        html.P("💡 Click column header to sort | 👆 Click icon or name for details",
               style=HINT_STYLE),
        html.Div(
            style={**TABLE_WRAPPER_STYLE, 'overflowX': 'auto'},
            children=[
                html.Table(
                    [html.Thead(html.Tr(header_cells)), html.Tbody(rows)],
                    style={**TABLE_STYLE, 'minWidth': f"{CONTENT_MIN_WIDTH}px"},
                )
            ],
        )
    ])


# ============================================================
# 10. CHAMPION DETAIL PANEL — полная карточка чемпиона
# ============================================================

def build_champion_radar(champion_name):
    """Радар-диаграмма Playstyle для чемпиона."""
    if champion_name not in RADAR_BASE.index:
        return html.Div("No radar data", style={'color': COLORS['text_muted'],
                        'textAlign': 'center', 'padding': '20px', 'fontSize': f"{FS['sm']}px"})
    row = RADAR_BASE.loc[champion_name]

    labels, values, hovers = [], [], []
    for axis_label, col in RADAR_AXES:
        pct = row.get(col + '_pct')
        labels.append(axis_label)
        values.append(round(float(pct), 1) if pd.notna(pct) else 0.0)
        h = RADAR_HINTS.get(axis_label, {"desc": "", "calc": ""})
        hovers.append(
            f"<b style='color:#C8AA6E;font-size:13px'>{axis_label}</b><br>"
            f"<span style='color:#F0E6D2'>{h['desc']}</span><br><br>"
            f"<span style='color:#A09B8C;font-size:10px'>📐 <b>How it's calculated:</b><br>{h['calc']}</span>"
        )

    labels_c = labels + [labels[0]]
    values_c = values + [values[0]]
    hovers_c = hovers + [hovers[0]]

    fig = go.Figure(go.Scatterpolar(
        r=values_c, theta=labels_c, fill='toself',
        fillcolor='rgba(200,170,110,0.25)',
        line={'color': LOL_GOLD, 'width': 2}, marker={'size': 6, 'color': LOL_GOLD},
        customdata=hovers_c,
        hovertemplate='%{customdata}<br><br><b style="color:#0AC8B9">Score: %{r:.0f}/100</b><extra></extra>',
    ))

    fig.update_layout(
        polar={'bgcolor': COLORS['header_bg'],
               'radialaxis': {'visible': True, 'range': [0, 100], 'showticklabels': False,
                              'gridcolor': COLORS['border'], 'linecolor': COLORS['border']},
               'angularaxis': {'gridcolor': COLORS['border'],
                               'tickfont': {'size': FS['sm'], 'color': COLORS['primary']}}},
        paper_bgcolor='rgba(0,0,0,0)', font={'family': FONT_FAMILY, 'color': COLORS['text']},
        margin={'l': 50, 'r': 50, 't': 25, 'b': 25}, height=270, showlegend=False,
        hoverlabel={'bgcolor': '#010A13', 'bordercolor': LOL_GOLD,
                    'font': {'family': FONT_FAMILY, 'size': FS['sm'], 'color': '#F0E6D2'},
                    'align': 'left'},
    )

    return dcc.Graph(figure=fig, config=CFG, style={'width': '100%'})


def _metric_category(title, color, defs, font_size=None):
    """Карточка одной категории метрик (Combat, Vision, Survival, Economy)."""
    fs = font_size if font_size is not None else FS['sm']
    rows = [html.Tr([
        html.Td(label, style={'padding': '3px 5px', 'color': LOL_TEXT, 'fontWeight': 'bold',
                              'fontSize': f"{fs}px", 'whiteSpace': 'nowrap', 'width': '42%'}),
        html.Td(bar_widget(val, mx, color, text=text, height=14, radius=8),
                style={'padding': '3px 5px', 'width': '58%'}),
    ]) for label, val, mx, text in defs]
    return html.Div([
        html.H5(title, style={'color': color, 'textAlign': 'center', 'marginTop': '0',
                              'marginBottom': '5px', 'fontSize': f"{fs}px", 'letterSpacing': '1px'}),
        html.Table([html.Tbody(rows)], style=DETAIL_TABLE_STYLE),
    ], style={'minWidth': '0', 'background': 'rgba(0,0,0,0.18)', 'borderRadius': '8px',
              'padding': '6px 10px', 'border': f"1px solid {color}55"})


def _build_stats_grid(crow, extra, font_size=None):
    """Сетка 2×2 с метриками: Combat, Vision & Obj, Survival, Economy."""
    fs = font_size if font_size is not None else FS['sm']

    def g(d, k, default=0):
        """Безопасное получение значения из словаря/Series."""
        try:    return d.get(k, default)
        except: return default

    # Цвета категорий
    C_COMBAT, C_ECON, C_SURVIVE, C_VISION = '#E84057', '#C8AA6E', '#0AC8B9', '#4A90D9'

    # Сырые списки метрик (будут отсортированы по % заполнения)
    combat_raw = [
        ("🏆 Winrate",     crow.get('winrate', 0),          100,                f"{crow.get('winrate', 0):.1f}%"),
        ("⭐ KDA",         crow.get('kda', 0),              gmax('kda'),        f"{crow.get('kda', 0):.2f}"),
        ("🗡️ Kills",       crow.get('avg_kills', 0),        gmax('avg_kills'),  f"{crow.get('avg_kills', 0):.1f}"),
        ("💀 Deaths",      crow.get('avg_deaths', 0),       gmax('avg_deaths'), f"{crow.get('avg_deaths', 0):.1f}"),
        ("🤝 Assists",     crow.get('avg_assists', 0),      gmax('avg_assists'),f"{crow.get('avg_assists', 0):.1f}"),
        ("💥 Damage",      crow.get('avg_damage', 0),       gmax('avg_damage'), f"{int(crow.get('avg_damage', 0)):,}"),
        ("🩸 First Blood", g(extra, 'firstblood_rate', 0),  30,                 f"{g(extra, 'firstblood_rate', 0):.1f}%"),
    ]
    econ_raw = [
        ("💰 Gold",        crow.get('avg_gold', 0),         gmax('avg_gold'),   f"{int(crow.get('avg_gold', 0)):,}"),
        ("⚔️ CS/min",      crow.get('cs_per_min', 0),       gmax('cs_per_min'), f"{crow.get('cs_per_min', 0):.1f}"),
        ("🌾 Total CS",    g(extra, 'avg_cs_total', 0),     300,                f"{int(g(extra, 'avg_cs_total', 0)):,}"),
    ]
    survive_raw = [
        ("🛡️ Tanked",      crow.get('avg_damage_taken', 0), 50000,             f"{int(crow.get('avg_damage_taken', 0)):,}"),
        ("💚 Heal",        g(extra, 'avg_heal', 0),         18000,             f"{int(g(extra, 'avg_heal', 0)):,}"),
        ("📈 Level",       g(extra, 'avg_level', 0),        18,                f"{g(extra, 'avg_level', 0):.1f}"),
        ("⚡ Ult Casts",   g(extra, 'avg_r', 0),            8,                 f"{g(extra, 'avg_r', 0):.1f}"),
    ]
    vision_raw = [
        ("👁️ Vision",      g(extra, 'avg_vision', 0),       gmax('avg_vision'),f"{g(extra, 'avg_vision', 0):.1f}"),
        ("🟢 Wards Set",   g(extra, 'avg_wards_placed', 0), 30,                f"{g(extra, 'avg_wards_placed', 0):.1f}"),
        ("🔴 Wards Kill",  g(extra, 'avg_wards_killed', 0), 12,                f"{g(extra, 'avg_wards_killed', 0):.1f}"),
        ("🐉 Dragons",     crow.get('avg_dragons', 0),      gmax('avg_dragons'),f"{crow.get('avg_dragons', 0):.2f}"),
        ("👑 Barons",      crow.get('avg_barons', 0),       gmax('avg_barons'),f"{crow.get('avg_barons', 0):.2f}"),
        ("🏰 Turrets",     g(extra, 'avg_turrets', 0),      gmax('avg_turrets'),f"{g(extra, 'avg_turrets', 0):.2f}"),
    ]

    def sort_by_fill(metrics):
        """Сортировка метрик по % заполнения шкалы (от большего к меньшему)."""
        def fill_pct(item):
            val = float(item[1]) if isinstance(item[1], (int, float)) else 0
            mx = float(item[2]) if isinstance(item[2], (int, float)) else 1
            return min((val / mx) * 100, 100) if mx > 0 else 0
        return sorted(metrics, key=fill_pct, reverse=True)

    cards = [
        _metric_category("⚔️ COMBAT",       C_COMBAT,  sort_by_fill(combat_raw),  fs),
        _metric_category("👁️ VISION & OBJ", C_VISION,  sort_by_fill(vision_raw),  fs),
        _metric_category("🛡️ SURVIVAL",     C_SURVIVE, sort_by_fill(survive_raw), fs),
        _metric_category("💰 ECONOMY",      C_ECON,    sort_by_fill(econ_raw),    fs),
    ]

    return html.Div(cards, style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr',
                                  'gridAutoRows': 'min-content', 'gap': '10px', 'minWidth': '0'})


def _icon_grid(df, icon_fn, title, bar_color, empty_msg, id_col, name_fn, tooltip_fn=None):
    """Сетка иконок предметов/заклинаний с полосками частоты."""
    if df.empty:
        return html.Div([
            html.H5(title, style={'color': LOL_GOLD, 'textAlign': 'center', 'marginTop': '0',
                                  'marginBottom': '8px', 'fontSize': f"{FS['sm']}px",
                                  'letterSpacing': '1px'}),
            html.P(empty_msg, style={'textAlign': 'center', 'color': '#A09B8C',
                                     'fontSize': f"{FS['sm']}px"}),
        ])

    max_count = max(int(df['count'].max()), 1)
    cells = []
    for _, r in df.iterrows():
        _id     = int(r[id_col])
        cnt     = int(r['count'])
        name    = name_fn(_id)
        tooltip = tooltip_fn(_id) if tooltip_fn else f"{name} — {cnt:,}"
        cells.append(html.Div([
            html.Img(src=icon_fn(_id), style={
                'width': '55px', 'height': '55px', 'borderRadius': '8px',
                'border': f"2px solid {LOL_GOLD}",
                'boxShadow': '0 2px 4px rgba(0,0,0,0.5)',
            }),
            html.Div(name, style={
                'fontSize': f"{FS['xs']}px", 'color': LOL_TEXT, 'fontWeight': 'bold',
                'whiteSpace': 'nowrap', 'overflow': 'hidden', 'textOverflow': 'ellipsis',
                'maxWidth': '74px', 'textAlign': 'center', 'marginTop': '3px',
            }),
            bar_widget(cnt, max_count, bar_color, text=f"{cnt:,}", height=14, radius=6),
        ], title=tooltip, style={
            'display': 'flex', 'flexDirection': 'column', 'alignItems': 'center',
            'gap': '2px', 'width': '78px', 'padding': '6px 4px',
            'borderRadius': '8px', 'background': 'rgba(0,0,0,0.25)',
        }))

    return html.Div([
        html.H5(title, style={'color': LOL_GOLD, 'textAlign': 'center', 'marginTop': '0',
                              'marginBottom': '8px', 'fontSize': f"{FS['sm']}px",
                              'letterSpacing': '1px'}),
        html.Div(cells, style={'display': 'grid', 'gridTemplateColumns': 'repeat(2, 1fr)',
                 'gap': '8px', 'justifyItems': 'center'}),
    ], style={'minWidth': '0'})


def _build_role_table(role_data):
    """Таблица статистики чемпиона по ролям."""
    headers = ["Role", "Games", "WR%", "K/D/A", "KDA", "Gold", "CS/m", "Damage"]
    widths  = ['12%', '15%', '12%', '15%', '11%', '13%', '10%', '12%']
    max_games = int(role_data['games'].max()) if not role_data.empty else 1
    rows = []
    for _, r in role_data.iterrows():
        wr_color = LOL_GREEN if r['winrate'] >= 50 else '#f39c12'
        rows.append(html.Tr([
            html.Td(r['team_position'], style={
                'padding': '4px', 'textAlign': 'center', 'fontWeight': 'bold',
                'fontSize': f"{FS['sm']}px", 'color': LOL_GOLD,
            }),
            html.Td(bar_widget(r['games'], max_games, LOL_GREEN,
                    text=f"{int(r['games']):,}", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
            html.Td(bar_widget(r['winrate'], 100, wr_color,
                    text=f"{r['winrate']:.1f}%", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
            html.Td(f"{r['avg_kills']:.1f}/{r['avg_deaths']:.1f}/{r['avg_assists']:.1f}",
                    style={'padding': '4px', 'textAlign': 'center',
                           'fontSize': f"{FS['sm']}px", 'color': LOL_TEXT}),
            html.Td(bar_widget(r['kda'], gmax('kda'), METRIC_COLORS['kda'],
                    text=f"{r['kda']:.2f}", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
            html.Td(bar_widget(r['avg_gold'], gmax('avg_gold'), METRIC_COLORS['gold'],
                    text=f"{int(r['avg_gold']):,}", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
            html.Td(bar_widget(r['cs_per_min'], gmax('cs_per_min'), METRIC_COLORS['cs'],
                    text=f"{r['cs_per_min']:.1f}", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
            html.Td(bar_widget(r['avg_damage'], gmax('avg_damage'), METRIC_COLORS['damage'],
                    text=f"{int(r['avg_damage']):,}", height=20, radius=4, min_text_pct=30),
                    style={'padding': '4px'}),
        ]))
    return html.Table([
        html.Thead(html.Tr([
            html.Th(h, style={'padding': '6px', 'color': LOL_GOLD, 'textAlign': 'center',
                              'fontSize': f"{FS['sm']}px", 'width': w,
                              'borderBottom': f"1px solid {LOL_GOLD}"})
            for h, w in zip(headers, widths)
        ])),
        html.Tbody(rows)
    ], style=DETAIL_TABLE_STYLE)


def _build_lore_block(lore_text):
    """Блок легенды чемпиона в виде стилизованного свитка."""
    if not lore_text or str(lore_text).strip() in ('', 'nan', 'None'):
        lore_text = "No legend available for this champion."

    return html.Div([
        # Заголовок с орнаментом
        html.Div([
            html.Span("❖ ", style={'color': LOL_GOLD, 'fontSize': f"{FS['lg']}px",
                                    'textShadow': f"0 0 12px {rgba(LOL_GOLD, 0.35)}"}),
            html.Span("LEGEND", style={
                'color': LOL_GOLD, 'fontWeight': 'bold', 'fontSize': f"{FS['xl']}px",
                'letterSpacing': '8px',
                'textShadow': f"0 0 18px {rgba(LOL_GOLD, 0.35)}, 0 0 34px {rgba(LOL_GOLD, 0.18)}",
            }),
            html.Span(" ❖", style={'color': LOL_GOLD, 'fontSize': f"{FS['lg']}px",
                                    'textShadow': f"0 0 12px {rgba(LOL_GOLD, 0.35)}"}),
        ], style={'textAlign': 'center', 'marginBottom': '8px', 'flexShrink': 0}),
        # Золотой разделитель
        html.Hr(style={
            'border': 'none', 'height': '1px',
            'background': f"linear-gradient(90deg, {rgba(LOL_GOLD, 0)}, {LOL_GOLD}, {rgba(LOL_GOLD, 0)})",
            'margin': '0 0 12px 0', 'opacity': '0.7', 'flexShrink': 0,
        }),
        # Текст легенды с буквицей
        html.P([
            html.Span("📜", style={'fontSize': f"{FS['xl'] + 8}px", 'fontStyle': 'normal',
                                    'float': 'left', 'lineHeight': '1', 'marginRight': '12px',
                                    'marginTop': '4px',
                                    'filter': 'drop-shadow(0 0 10px rgba(200, 155, 60, 0.55))'}),
            str(lore_text)
        ], style={'color': '#D7C9A8', 'fontSize': f"{FS['md'] + 1}px", 'lineHeight': '1.75',
                  'fontStyle': 'italic', 'textAlign': 'justify', 'margin': '0', 'flex': '1',
                  'overflowY': 'auto', 'paddingRight': '8px',
                  'textShadow': '0 1px 2px rgba(0,0,0,0.5)'}),
        # Нижний орнамент
        html.Div("⚜", style={'textAlign': 'center', 'color': rgba(LOL_GOLD, 0.55),
                              'fontSize': f"{FS['lg']}px", 'marginTop': '6px', 'flexShrink': 0}),
    ], style=LORE_BOX_STYLE)


def create_champion_detail_panel(champion_name, region, role='ALL', min_games=1):
    """Полная панель с детальной информацией о чемпионе."""
    if not champion_name:
        return html.Div()

    # Информация о чемпионе из справочника (титул, теги, легенда)
    info  = champions_df[champions_df['champion_name'] == champion_name]
    title = info['champion_title'].iloc[0] if not info.empty and 'champion_title' in info.columns else ""
    tags  = info['champion_tags'].iloc[0]  if not info.empty and 'champion_tags'  in info.columns else ""
    lore  = info['champion_lore'].iloc[0]  if not info.empty and 'champion_lore'  in info.columns else ""

    # Статистика чемпиона
    if region == 'ALL':
        cstats = _aggregate(
            df_all[df_all['champion_name'] == champion_name], AGG_DETAIL, 'champion_name',
        )
    else:
        cstats = df_all[
            (df_all['champion_name'] == champion_name) & (df_all['region'] == region)
        ]
    if cstats.empty:
        return html.Div("No detailed data available for this champion",
                        style={'color': LOL_TEXT, 'textAlign': 'center', 'padding': '20px'})

    crow        = cstats.iloc[0]
    total_games = int(crow.get('games', 0))
    total_wr    = float(crow.get('winrate', 0))
    extra       = EXTRA_DF.loc[champion_name] if champion_name in EXTRA_DF.index else pd.Series(dtype='float64')

    # Данные по ролям
    role_data = get_role_data(champion_name, region)
    if not role_data.empty and total_games > 0:
        main_role     = role_data.iloc[0]['team_position']
        main_role_pct = int(role_data.iloc[0]['games']) / total_games * 100
    else:
        main_role, main_role_pct = "N/A", 0

    # Мета-данные (presence, css_score, tier) — через get_filtered_data
    meta_df    = get_filtered_data(region, role, 'full', min_games)
    champ_meta = meta_df[meta_df['champion_name'] == champion_name]
    if not champ_meta.empty:
        presence_val = float(champ_meta['presence'].iloc[0])
        css_val      = float(champ_meta['css_score'].iloc[0])
        tier_val     = champ_meta['tier'].iloc[0]
    else:
        fallback = get_filtered_data(region, role, 'full', 1)
        fb_row   = fallback[fallback['champion_name'] == champion_name]
        if not fb_row.empty:
            presence_val = float(fb_row['presence'].iloc[0])
            css_val      = float(fb_row['css_score'].iloc[0])
            tier_val     = fb_row['tier'].iloc[0]
        else:
            presence_val, css_val, tier_val = 0.0, 0.0, 'C'

    # Топ предметов и заклинаний
    items_df    = df_items[df_items['champion_name'] == champion_name].nlargest(6, 'count')
    items_table = _icon_grid(items_df, get_item_icon, "🛡️ TOP ITEMS", LOL_GREEN, "No items",
                             'item_id', get_item_name, tooltip_fn=get_item_tooltip)

    spells_df    = SPELLS_DF[SPELLS_DF['champion_name'] == champion_name].nlargest(6, 'count')
    spells_table = _icon_grid(spells_df, get_spell_icon, "✨ TOP SPELLS",
                              METRIC_COLORS['cs'], "No spells", 'spell_id',
                              get_spell_name, tooltip_fn=get_spell_tooltip)

    # Сетка метрик и радар
    stats_grid  = _build_stats_grid(crow, extra)
    radar_chart = build_champion_radar(champion_name)
    role_table  = _build_role_table(role_data)

    # Выпадающий список для выбора чемпиона
    selector = html.Div([
        dcc.Dropdown(
            id='champion-selector-dropdown',
            options=[{'label': c, 'value': c} for c in ALL_CHAMPIONS],
            value=champion_name, clearable=False,
            style={'width': '100%', 'backgroundColor': COLORS['header_bg'],
                   'color': COLORS['text'], 'border': f"1px solid {LOL_GOLD}",
                   'borderRadius': '5px', 'fontSize': f"{FS['md']}px"},
        )
    ], style={'width': '200px', 'minWidth': '200px', 'flexShrink': 0})

    def sspan(txt, color, mt=0):
        """Маленький span с текстом (для подписей под портретом)."""
        return html.Span(txt, style={
            'fontSize': f"{FS['sm']}px", 'color': color,
            'display': 'block', 'textAlign': 'center',
            **({'marginTop': f"{mt}px"} if mt else {}),
        })

    # Блок с портретом и подписями
    portrait_block = html.Div([
        html.Img(src=get_champion_portrait(champion_name),
                 style={'width': '100%', 'maxWidth': '150px', 'borderRadius': '12px',
                        'border': f"2px solid {LOL_GOLD}", 'marginBottom': '8px'}),
        html.Div([
            sspan(f"Games: {total_games:,}",                LOL_TEXT),
            sspan(f"🏆 WR: {total_wr:.1f}%",                 LOL_GREEN, 4),
            sspan(f"🎭 {main_role} ({main_role_pct:.0f}%)",   LOL_GOLD,  4),
            sspan(f"👁 Pres: {presence_val:.1f}%",
                  TIER_COLORS.get(tier_val, '#A09B8C'), 4),
            sspan(f"⚡ Str: {css_val:.1f}",                   '#FF8C00', 4),
        ], style={'marginBottom': '0'}),
    ], style={'textAlign': 'center'})

    # Блок с радаром
    radar_block = html.Div([
        html.H5("PLAYSTYLE", style={'color': LOL_GOLD, 'textAlign': 'center',
                                    'marginBottom': '6px', 'fontSize': f"{FS['lg']}px",
                                    'letterSpacing': '1px'}),
        radar_chart,
        html.P("Percentile vs all (0–100)", style={
            'textAlign': 'center', 'color': COLORS['text_muted'],
            'fontSize': f"{FS['xs']}px", 'fontStyle': 'italic',
            'marginTop': '0', 'marginBottom': '0',
        }),
    ], style={'minWidth': '0'})

    # Блок с таблицей по ролям
    roles_block = html.Div([
        html.H5("DETAILED STATS BY ROLE", style={
            'color': LOL_GOLD, 'textAlign': 'center',
            'marginBottom': '8px', 'fontSize': f"{FS['md']}px",
        }),
        html.Hr(style={
            'border': 'none', 'height': '1px',
            'background': f"linear-gradient(90deg, rgba(200,170,110,0), {LOL_GOLD}, rgba(200,170,110,0))",
            'margin': '0 20px 12px 20px', 'opacity': '0.6',
        }),
        role_table
    ], style={'minWidth': '0'})

    lore_block = _build_lore_block(lore)

    return html.Div([
        html.Div([
            # Верх: легенда на всю ширину
            html.Div(lore_block, style={'width': '100%', 'boxSizing': 'border-box',
                                        'marginBottom': '20px'}),
            # Середина: портрет слева, имя + блоки справа
            html.Div([
                html.Div(portrait_block, style={'width': '170px', 'minWidth': '170px',
                                                'flexShrink': 0}),
                html.Div([
                    # Строка с именем, титулом, тегами, тиром и селектором
                    html.Div([
                        html.Span(champion_name, style={
                            'color': LOL_GOLD, 'fontSize': f"{FS['xl'] + 10}px",
                            'fontWeight': 'bold', 'marginRight': '15px',
                            'textShadow': f"0 0 20px {rgba(LOL_GOLD, 0.3)}, 0 0 40px {rgba(LOL_GOLD, 0.15)}",
                        }),
                        html.Span(f'"{title}"', style={
                            'color': '#C4B998', 'fontSize': f"{FS['lg']}px",
                            'fontStyle': 'italic', 'fontWeight': 'bold', 'marginRight': '15px',
                        }),
                        html.Span(tags, style={
                            'color': '#A09B8C', 'fontWeight': 'bold',
                            'fontSize': f"{FS['lg']}px", 'marginRight': '15px',
                        }),
                        tier_badge(tier_val, font_size=FS['xl'], padding='6px 14px',
                                   min_width='48px'),
                        html.Div([
                            html.Span("SELECT YOUR CHAMPION", style={
                                'color': LOL_GOLD, 'fontSize': f"{FS['sm']}px",
                                'fontWeight': 'bold', 'letterSpacing': '1.5px',
                                'whiteSpace': 'nowrap',
                                'textShadow': f"0 0 10px {rgba(LOL_GOLD, 0.25)}",
                            }),
                            selector,
                        ], style={'marginLeft': 'auto', 'display': 'flex', 'alignItems': 'center',
                                  'gap': '10px', 'flexShrink': 0}),
                    ], style={'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap',
                              'gap': '15px', 'marginBottom': '15px'}),
                    # Ряд блоков: радар | метрики | предметы | заклинания
                    html.Div([radar_block, stats_grid, items_table, spells_table],
                             style={'display': 'grid',
                                    'gridTemplateColumns': '330px 2fr 0.65fr 0.65fr',
                                    'gap': '12px', 'alignItems': 'start'}),
                ], style={'flex': '1', 'minWidth': '0'}),
            ], style={'display': 'flex', 'alignItems': 'flex-start', 'gap': '20px',
                      'marginBottom': '20px'}),
            # Низ: таблица по ролям
            roles_block,
        ], style={'backgroundColor': COLORS['header_bg'], 'borderRadius': '12px',
                  'padding': '20px', 'border': f"1px solid {LOL_GOLD}",
                  'minWidth': f"{CONTENT_MIN_WIDTH}px", 'boxSizing': 'border-box'}),
    ], style={'overflowX': 'auto'})


# ============================================================
# 11. LAYOUT — вёрстка страницы
# ============================================================

def _live_stats_block(n_champions, n_picks):
    """Живая статистика в фильтре: число чемпионов и пиков в текущем срезе."""
    _base = FS['xl'] + 4
    _fs_champions = int(round(_base * 2.0))   # число чемпионов — в 2 раза крупнее
    _fs_picks     = int(round(_base * 1.5))   # число пиков — в 1.5 раза крупнее

    def stat(value, label, value_fs):
        return html.Div([
            html.Div(f"{value:,}", style={
                'color': COLORS['text'], 'fontWeight': 'bold',
                'fontSize': f"{value_fs}px", 'fontVariantNumeric': 'tabular-nums',
                'lineHeight': '1.05', 'textAlign': 'center',
            }),
            html.Div(label, style={
                'color': COLORS['text_muted'], 'fontSize': f"{FS['md']}px",
                'textAlign': 'center', 'letterSpacing': '1px', 'marginTop': '2px',
            }),
        ], style={'textAlign': 'center'})

    return [
        html.Div("👁 IN VIEW", style={
            'color': COLORS['primary'], 'fontSize': f"{FS['md']}px",
            'fontWeight': 'bold', 'letterSpacing': '1px', 'marginBottom': '10px',
            'textAlign': 'center',
        }),
        html.Div([
            stat(n_champions, "CHAMPIONS", _fs_champions),
            stat(n_picks,     "PICKS",     _fs_picks),
        ], style={'display': 'flex', 'justifyContent': 'center', 'gap': '28px',
                  'alignItems': 'flex-end'}),
    ]


def build_filters():
    """Панель фильтров: InView, Min Games, Region, Role."""
    label_style = {
        **LABEL_BASE_STYLE,
        'fontSize': f"{FS['lg']}px",
        'letterSpacing': '1px',
        'whiteSpace': 'nowrap',
    }
    return html.Div([
        # Живая статистика
        html.Div(id='filter-live-stats', style={
            'display': 'flex', 'flexDirection': 'column', 'gap': '4px',
            'paddingRight': '32px', 'marginRight': '12px',
            'borderRight': f"1px solid {COLORS['primary']}66",
            'flexShrink': 0, 'minWidth': '280px',
            'justifyContent': 'center',
        }),
        # Слайдер Min Games
        html.Div([
            html.Label(id='min-games-label', children=f"MIN GAMES: {DEFAULT_MIN_GAMES}",
                       style=label_style),
            html.Div([dcc.Slider(
                id='min-games-slider', min=SLIDER_MIN, max=SLIDER_MAX, step=1,
                value=DEFAULT_MIN_GAMES, marks=None,
                tooltip={'placement': 'bottom', 'always_visible': False},
                updatemode='mouseup',
                className='lol-gold-slider',
            )], style={'width': '200px'}),
        ], style={**FILTER_GROUP_STYLE, 'flexGrow': 1}),
        # Выбор региона
        html.Div([
            html.Label("REGION:", style=label_style),
            dcc.RadioItems(
                options=REGION_OPTIONS, value=DEFAULT_REGION,
                id='region-filter', inline=True,
                className='lol-region-radio',
                style={'display': 'inline-block'},
                labelStyle={
                    'color': COLORS['text'],
                    'fontSize': f"{FS['md']}px",
                    'marginRight': '14px',
                    'cursor': 'pointer',
                    'display': 'inline-flex',
                    'alignItems': 'center',
                },
                inputStyle={
                    'marginRight': '6px',
                    'cursor': 'pointer',
                    'accentColor': LOL_GOLD,
                    'transform': 'scale(1.2)',
                },
            ),
        ], style=FILTER_GROUP_STYLE),
        # Выбор роли
        html.Div([
            html.Label("🎭 ROLE:", style=label_style),
            dcc.Dropdown(
                id='role-filter',
                options=[{'label': r, 'value': r} for r in ROLES],
                value='ALL', clearable=False,
                style={'width': '160px', **DROPDOWN_STYLE,
                       'fontSize': f"{FS['md']}px"},
            ),
        ], style=FILTER_GROUP_STYLE),
    ], style=FILTER_CONTAINER_STYLE)


# ── Чемпион по умолчанию (самый сильный) ──
DEFAULT_CHAMPION = get_filtered_data(DEFAULT_REGION, 'ALL', 'full', DEFAULT_MIN_GAMES) \
    .sort_values('css_score', ascending=False).iloc[0]['champion_name']


def serve_layout():
    """Возвращает полный layout страницы."""
    return html.Div([
        # Шапка с заголовком и статистикой
        html.Div([
            html.H1("LEAGUE OF LEGENDS META DASHBOARD", style=PAGE_TITLE_STYLE),
            html.P("High MMR Analysis | Challenger • Grandmaster • Master | 🏆 RANKED SOLO 5×5",
                   style=PAGE_SUBTITLE_STYLE),
            html.P(f"⚔️ {TOTAL_MATCHES:,} MATCHES | 👥 {TOTAL_PLAYERS:,} PLAYERS | "
                   f"🎮 {TOTAL_CHAMPIONS} CHAMPIONS", style=PAGE_STATS_STYLE),
            html.P(f"📅 {min_date} — {max_date} | Avg {AVG_GAMES} games per champion",
                   style=PAGE_RANGE_STYLE),
        ], style={'borderBottom': f"2px solid {COLORS['primary']}", 'paddingBottom': '15px'}),
        # Описание страницы
        html.Div([
            html.H2("🏆 CHAMPIONS", style=SECTION_TITLE_STYLE),
            html.P(
                "Explore the League of Legends champion meta through interactive filters, "
                "tier rankings, a win rate vs pick rate chart, and a sortable stats table. "
                "Click any champion to open a detailed profile with categorized stats, "
                "playstyle radar, role performance, top items, summoner spells, portrait, and lore.",
                style={
                    'color': COLORS['text_muted'],
                    'fontSize': f"{FS['sm']}px",
                    'lineHeight': '1.6',
                    'maxWidth': '950px',
                    'margin': '4px 0 12px 0',
                    'fontStyle': 'italic',
                },
            ),
        ]),
        # Фильтры
        build_filters(),
        # Карточки лидеров
        html.Div(id='best-cards-container', style={'marginBottom': '20px'}),
        # Rank List
        html.Div(id='tier-list-container', style={'width': '100%', 'marginBottom': '20px'}),
        # Tier Distribution + Scatter
        html.Div([
            html.Div(id='tier-dist-container', style={'minWidth': '0'}),
            html.Div(id='scatter-container',   style={'minWidth': '0'}),
        ], style={'display': 'grid', 'gridTemplateColumns': '1fr 2fr', 'gap': '20px',
                  'marginBottom': '20px'}),
        # Хранилища состояния (Stores)
        dcc.Store(id='selected-champion', data=None),
        dcc.Store(id='sort-store', data={'column': 'winrate', 'direction': 'desc'}),
        dcc.Store(id='selected-tiers', data=[]),
        dcc.Store(id='champ-clicks-prev', data=[]),
        # Таблица чемпионов
        html.Div(id='champions-table'),
        # Якорь для прокрутки к деталям
        html.Div(id='detail-anchor'),
        # Панель деталей чемпиона
        html.Div(id='champion-detail-container', style={'marginTop': '20px'}),
    ], style=APP_STYLE)


# ============================================================
# 12. CALLBACKS — интерактивность
# ============================================================

@callback(
    Output('best-cards-container', 'children'),  # карточки лидеров
    Output('min-games-label',      'children'),  # текст на слайдере
    Output('filter-live-stats',    'children'),  # живая статистика
    Input('region-filter',    'value'),
    Input('role-filter',      'value'),
    Input('min-games-slider', 'value'),
)
def update_best_cards(region, role, min_games):
    """Обновляет карточки лидеров, лейбл слайдера и живую статистику."""
    df = get_filtered_data(region, role, 'full', min_games)
    n_champions = int(len(df))
    n_picks     = int(df['games'].sum()) if not df.empty else 0
    return (
        best_champions_row(region, role, min_games),
        f"MIN GAMES: {min_games}",
        _live_stats_block(n_champions, n_picks),
    )


@callback(
    Output('tier-list-container', 'children'),  # Rank List
    Output('scatter-container',   'children'),  # Scatter график
    Output('tier-dist-container', 'children'),  # Tier Distribution
    Input('region-filter',    'value'),
    Input('role-filter',      'value'),
    Input('min-games-slider', 'value'),
    Input('selected-tiers',   'data'),
)
def update_meta_visuals(region, role, min_games, selected_tiers):
    """Обновляет Rank List, Scatter и Tier Distribution."""
    df = get_filtered_data(region, role, 'full', min_games)
    return (
        build_tier_list(df),
        build_scatter(df, selected_tiers),
        build_tier_distribution(df, selected_tiers),
    )


@callback(
    Output('selected-tiers', 'data'),                   # обновляем хранилище
    Input({'type': 'tier-dist-row', 'tier': dash.ALL}, 'n_clicks'),  # клики по рядам
    State('selected-tiers', 'data'),                    # текущее состояние
    prevent_initial_call=True,
)
def toggle_tier(_clicks, current):
    """Выбор/снятие тира при клике на ряд в Tier Distribution."""
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict) or triggered.get('type') != 'tier-dist-row':
        return dash.no_update
    if not any(_clicks):
        return dash.no_update
    current = list(current or [])
    clicked = triggered.get('tier')
    if clicked in current:
        current.remove(clicked)
    else:
        current.append(clicked)
    return [t for t in TIER_ORDER if t in current]


@callback(
    Output('selected-tiers', 'data', allow_duplicate=True),
    Input('region-filter',    'value'),
    Input('role-filter',      'value'),
    Input('min-games-slider', 'value'),
    prevent_initial_call=True,
)
def reset_tiers_on_filter_change(_r, _ro, _mg):
    """Сбрасывает выбранные тиры при смене любого фильтра."""
    return []


@callback(
    Output('champions-table', 'children'),             # таблица
    Output('sort-store',      'data'),                 # состояние сортировки
    Input('region-filter',    'value'),
    Input('role-filter',      'value'),
    Input('min-games-slider', 'value'),
    Input({'type': 'table-header', 'column': dash.ALL}, 'n_clicks'),  # клики по заголовкам
    State('sort-store', 'data'),
    prevent_initial_call=False,
)
def update_table(region, role, min_games, _header_clicks, current_sort):
    """Обновляет таблицу чемпионов с учётом сортировки."""
    current_sort = current_sort or {'column': 'winrate', 'direction': 'desc'}
    triggered = ctx.triggered_id
    if isinstance(triggered, dict) and triggered.get('type') == 'table-header':
        col = triggered.get('column')
        if col in HEADER_COLUMN_MAP:
            if current_sort['column'] == col:
                current_sort['direction'] = 'asc' if current_sort['direction'] == 'desc' else 'desc'
            else:
                current_sort = {'column': col, 'direction': 'desc'}
    filtered = get_filtered_data(region, role, 'full', min_games)
    if filtered.empty:
        return (
            html.Div(f"⚠️ No champions with ≥ {min_games} games for this filter",
                     style={'color': COLORS['text_muted'], 'textAlign': 'center',
                            'padding': '30px', 'fontSize': f"{FS['md']}px"}),
            current_sort,
        )
    filtered = filtered.sort_values(current_sort['column'],
                                    ascending=(current_sort['direction'] == 'asc'))
    return (
        make_table(filtered.head(TOP_N), current_sort['column'], current_sort['direction']),
        current_sort,
    )


@callback(
    Output('selected-champion', 'data'),               # выбранный чемпион
    Output('champ-clicks-prev', 'data'),               # предыдущие клики
    Input({'type': 'champion-select', 'index': dash.ALL}, 'n_clicks'),  # все клики по чемпионам
    State({'type': 'champion-select', 'index': dash.ALL}, 'id'),
    State('champ-clicks-prev', 'data'),
    prevent_initial_call=True,
)
def select_champion(clicks_list, ids_list, prev):
    """Определяет какого чемпиона выбрали по клику."""
    prev    = prev or []
    current = [c or 0 for c in clicks_list]
    selected = dash.no_update
    if len(prev) == len(current):
        for i, (now, was) in enumerate(zip(current, prev)):
            if now > was:                              # клик увеличил счётчик
                selected = ids_list[i].get('index')
                break
    return selected, current


@callback(
    Output('selected-champion', 'data', allow_duplicate=True),
    Input('champion-selector-dropdown', 'value'),      # выбор из выпадающего списка
    prevent_initial_call=True,
)
def select_from_dropdown(value):
    """Выбор чемпиона из выпадающего списка в детальной панели."""
    return value or dash.no_update


@callback(
    Output('selected-champion', 'data', allow_duplicate=True),
    Input('scatter-graph', 'clickData'),               # клик по точке на scatter
    prevent_initial_call=True,
)
def select_from_scatter(click_data):
    """Выбор чемпиона по клику на scatter-графике."""
    if not click_data:
        return dash.no_update
    point = click_data['points'][0]
    champ = point.get('hovertext') or point.get('text')
    return champ or dash.no_update


@callback(
    Output('champion-detail-container', 'children'),   # панель деталей
    Input('selected-champion', 'data'),
    Input('region-filter',     'value'),
    Input('role-filter',       'value'),
    Input('min-games-slider',  'value'),
    prevent_initial_call=False,
)
def show_champion_details(champion_name, region, role, min_games):
    """Отображает детальную панель чемпиона."""
    if not champion_name:
        return html.Div()
    return create_champion_detail_panel(champion_name, region, role, min_games)


# Клиентский callback — плавная прокрутка к деталям при выборе чемпиона
dash.clientside_callback(
    """
    function(champion) {
        if (champion) {
            const el = document.getElementById('detail-anchor');
            if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); }
        }
        return '';
    }
    """,
    Output('detail-anchor', 'children'),
    Input('selected-champion', 'data'),
)


# Чтобы страницу можно было импортировать в app.py с вкладками
layout = serve_layout()
# Greetings!