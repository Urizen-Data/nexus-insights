# ============================================================
# COMMON.PY — общий модуль для трёхстраничного дашборда
# ============================================================
# Содержит: конфигурацию, стили, утилиты, общие компоненты
# Импортируется всеми страницами: from common import *
# ============================================================

import os                                    # для построения путей к файлам
import dash                                  # сам Dash (для dcc, html компонентов)
from dash import dcc, html                  # компоненты Dash
import duckdb                                # подключение к DuckDB
import pandas as pd                          # работа с данными (DataFrame)
import numpy as np                           # числовые операции
import plotly.graph_objects as go            # низкоуровневые графики Plotly


# ============================================================
# 1. КОНФИГУРАЦИЯ — все константы
# ============================================================

# ── ★ Пути и схемы ──
DB_PATH = os.path.join("..", "parquet_folder")  # путь к DuckDB и витринам
QUEUE_ID = 420                                    # Ranked Solo/Duo
REMAKE_SEC = 300                                  # порог ремейка (5 минут)
START_DATE = '2026-05-01'                         # начало анализируемого периода

# ── ★ Регионы ──
REGION_PREF_ORDER = ['EU', 'US']                  # порядок отображения регионов
REGION_LOCAL_TZ = {
    'EU': 'Europe/Berlin',                        # центр Европы (CET/CEST)
    'US': 'America/New_York',                     # восточное побережье NA (ET)
}

# Опции для RadioItems-селектора региона
REGION_OPTIONS = [
    {'label': 'ALL', 'value': 'ALL'},
    {'label': 'EU',  'value': 'EU'},
    {'label': 'US',  'value': 'US'},
]
REGION_KEYS = ['ALL', 'EU', 'US']                 # все возможные ключи регионов

# ── ★ Дни недели ──
DAY_ORDER = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']  # порядок в подписях
DAY_FULL = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri', 5: 'Sat', 6: 'Sun'}  # weekday → имя
WEEKEND_DAYS = {'Sat', 'Sun'}                     # выходные (для подсветки)

# ── ★ Тиры (ранги) ──
TIER_ORDER = ['IRON', 'BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'EMERALD',
              'DIAMOND', 'MASTER', 'GRANDMASTER', 'CHALLENGER', 'UNKNOWN']
TIER_COLOR_MAP = {
    'IRON': '#5C6770', 'BRONZE': '#B87333', 'SILVER': '#BDC3C7', 'GOLD': '#D4AF37',
    'PLATINUM': '#3FBFBF', 'EMERALD': '#2ECC71', 'DIAMOND': '#6C8CFF',
    'MASTER': '#8B5FA8', 'GRANDMASTER': '#B23B4B', 'CHALLENGER': '#C9A95B',
    'UNKNOWN': '#7F8C8D',
}
TOP_TIERS = ['MASTER', 'GRANDMASTER', 'CHALLENGER']  # элитные тиры для графиков

# ── ★ Размеры графиков ──
GRAPH_H = 300              # стандартная высота графика
TALL_H = 340               # увеличенная высота для сложных графиков
CHART_HEIGHT = 300         # высота графика (алиас для страницы 2)
PLAYER_CHART_HEIGHT = 300  # высота графиков с игроками (алиас)
CONTENT_MIN_WIDTH = 1400   # минимальная ширина контента
HIST_BIN_MIN = 0.5         # размер бина для гистограмм (минуты)

# Алиасы для обратной совместимости
TALL_HEIGHT = TALL_H
PLAYER_CHART_HEIGHT = CHART_HEIGHT

# ── ★ Шрифты ──
FONT_FAMILY = "'Beaufort for LoL', 'Marcellus', 'Times New Roman', serif"
FS = {'xl': 24, 'lg': 19, 'md': 15, 'sm': 13, 'xs': 11}  # единый конфиг размеров

# ── ★ Цветовая палитра (единая для всех страниц) ──
COLORS = {
    # Базовые цвета фона и текста
    'primary':    '#C8AA6E', 'secondary':  '#0A1114', 'header_bg':  '#1E282D',
    'text':       '#F0E6D2', 'text_muted': '#A09B8C', 'border':     '#2c2c2c',
    'bar_bg':     '#2c2c2c', 'filter_bg':  '#1E282D', 'card_bg':    '#1a1f24',
    'panel':      '#0F171C', 'panel_lt':   '#13202A',
    # Цвета метрик
    'winrate':    '#2ecc71', 'pickrate':   '#C8AA6E', 'banrate':    '#e74c3c',
    'kda':        '#f39c12', 'kills':      '#3498db', 'gold':       '#FFD700',
    'damage':     '#e67e22', 'objective':  '#1abc9c', 'vision':     '#9b59b6',
    # Цвета сторон и зон (Blood & Objectives)
    'blue_side':  '#0AC8B9', 'blue_glow':  'rgba(10,200,185,0.55)',
    'red_side':   '#E84057', 'red_glow':   'rgba(232,64,87,0.55)',
    'blood':      '#C8413B', 'blood_lt':   '#E5594F',
    'teal':       '#0AC8B9', 'teal_dim':   '#0A6B66',
    'gold':       '#C8AA6E', 'gold_dim':   '#785A28',
    'grid':       'rgba(200,170,110,0.08)',
    'muted':      '#7C8B92',
    'bg':         '#091014',
    # Дополнительные
    'purple':     '#9b59b6',
}


def rgba(hex_color, alpha=1.0):
    """
    Конвертирует HEX-цвет в rgba-строку для теней и заливок.
    Пример: rgba('#C8AA6E', 0.5) → 'rgba(200,170,110,0.5)'
    """
    h = str(hex_color).lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)              # краткая запись #RGB → #RRGGBB
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ── Алиасы для краткости ──
LOL_GOLD  = COLORS['primary']      # золотой (основной акцент)
LOL_GREEN = COLORS['winrate']      # зелёный (винрейт)
LOL_DARK  = COLORS['secondary']    # тёмный фон
LOL_TEXT  = COLORS['text']         # светлый текст
C = COLORS                         # для обратной совместимости с Кодом 3

# ── Цвета регионов (унифицировано) ──
REGION_COLOR = {'EU': COLORS['teal'], 'US': COLORS['gold']}


# ============================================================
# 2. PLOTLY-КОНСТАНТЫ — базовые настройки графиков
# ============================================================

PAD = dict(l=44, r=20, t=42, b=24)          # отступы внутри графиков
NO_GRID = dict(showgrid=False, zeroline=False)  # оси без сетки
GRAPH_CONFIG = {'displayModeBar': False}     # скрыть панель инструментов Plotly
CFG = GRAPH_CONFIG                           # алиас для краткости

# Основной layout для Plotly (страницы 1 и 2)
PLOTLY_LAYOUT = dict(
    paper_bgcolor='rgba(0,0,0,0)',           # прозрачный фон бумаги
    plot_bgcolor='rgba(0,0,0,0)',            # прозрачный фон графика
    font=dict(family=FONT_FAMILY, color=COLORS['text'], size=12),
    title_font=dict(color=COLORS['primary'], size=17),
    title_x=0.5, title_xanchor='center',     # заголовок по центру
    margin=PAD,
    hoverlabel=dict(
        bgcolor=COLORS['header_bg'],
        bordercolor=COLORS['primary'],
        font=dict(color=COLORS['text'], family=FONT_FAMILY),
    ),
)

# Альтернативный layout (страница 3 — Blood & Objectives)
LAYOUT = dict(
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    font=dict(family=FONT_FAMILY, color=COLORS['text'], size=12),
    margin=dict(l=48, r=22, t=52, b=40),
    hoverlabel=dict(
        bgcolor=COLORS['panel_lt'],
        bordercolor=COLORS['gold'],
        font=dict(color=COLORS['text'], family=FONT_FAMILY),
    ),
)

# Базовый заголовок графика
TITLE_BASE = dict(
    font=dict(color=COLORS['text'], size=15),
    x=0.5, xanchor='center', y=0.95,
)

# Сетка: с линиями и без
GRID_FULL = dict(
    showgrid=True,
    gridcolor='rgba(200,170,110,0.10)',       # тонкие золотистые линии
    gridwidth=1,
    zeroline=False,
    linecolor=COLORS['border'],
)
GRID_NONE = dict(showgrid=False, zeroline=False, linecolor=COLORS['border'])


# ============================================================
# 3. СТИЛИ — все CSS-словари для компонентов Dash
# ============================================================

# ── Основной стиль приложения (светлый тёмный фон) ──
APP_STYLE = {
    'fontFamily': FONT_FAMILY,
    'backgroundColor': COLORS['secondary'],
    'minHeight': '100vh',
    'padding': '20px',
    'width': '100%',
    'minWidth': f"{CONTENT_MIN_WIDTH}px",
    'maxWidth': '100%',
    'margin': 'auto',
    'boxSizing': 'border-box',
    'backgroundImage': (
        "radial-gradient(circle at 50% 0%, "
        "rgba(200,170,110,0.05) 0%, rgba(0,0,0,0) 55%)"
    ),
}

# ── Тёмный стиль для страницы Blood & Objectives ──
APP_STYLE_DARK = {
    'fontFamily': FONT_FAMILY,
    'backgroundColor': COLORS['bg'],
    'minHeight': '100vh',
    'padding': '0 0 50px 0',
    'width': '100%',
    'minWidth': '1400px',
    'maxWidth': '100%',
    'margin': '0 auto',
    'boxSizing': 'border-box',
    'backgroundImage': (
        f"radial-gradient(circle at 50% 0%, "
        f"rgba(200,170,110,0.05) 0%, rgba(0,0,0,0) 55%)"
    ),
}

# ── Заголовок страницы ──
PAGE_TITLE_STYLE = {
    'textAlign': 'center',
    'color': COLORS['primary'],
    'marginBottom': '5px',
    'fontFamily': FONT_FAMILY,
    'letterSpacing': '2px',
    'fontSize': '40px',
    'fontWeight': '400',
    'textTransform': 'uppercase',
    'textShadow': f"0 0 26px rgba(200,170,110,0.35)",
}

# ── Подзаголовок страницы ──
PAGE_SUBTITLE_STYLE = {
    'textAlign': 'center',
    'color': COLORS['text_muted'],
    'marginBottom': '5px',
    'fontFamily': FONT_FAMILY,
}

# ── Статистика в шапке (матчи, игроки, чемпионы) ──
PAGE_STATS_STYLE = {
    'textAlign': 'center',
    'color': COLORS['primary'],
    'marginBottom': '8px',
    'fontSize': f"{FS['md']}px",
    'fontWeight': 'bold',
    'textShadow': '0 0 16px rgba(200,170,110,0.2)',
}

# ── Диапазон дат ──
PAGE_RANGE_STYLE = {
    'textAlign': 'center',
    'color': '#A09B8C',
    'marginBottom': '20px',
    'fontSize': f"{FS['sm']}px",
}

# ── Заголовок секции ──
SECTION_TITLE_STYLE = {
    'fontSize': f"{FS['xl']}px",
    'fontWeight': 'bold',
    'color': COLORS['primary'],
    'textAlign': 'center',
    'marginBottom': '20px',
    'marginTop': '10px',
    'fontFamily': FONT_FAMILY,
    'letterSpacing': '2px',
    'textShadow': '0 0 20px rgba(200,170,110,0.25)',
}

# ── Заголовок секции с левым выравниванием (страница 2) ──
SECTION_TITLE_STYLE_LEFT = {
    'color': COLORS['primary'],
    'fontFamily': FONT_FAMILY,
    'letterSpacing': '1.2px',
    'fontSize': f"{FS['lg']}px",
    'marginTop': '8px',
    'marginBottom': '12px',
    'textAlign': 'left',
    'textTransform': 'uppercase',
    'textShadow': f"0 0 16px rgba(200,170,110,0.20)",
}

# ── Панель для графика ──
PANEL_STYLE = {
    'background': f"linear-gradient(180deg, {COLORS['card_bg']}, rgba(0,0,0,0.25))",
    'borderRadius': '16px',
    'padding': '10px',
    'border': f"1px solid {COLORS['primary']}",
    'borderTop': f"2px solid {COLORS['primary']}",
    'boxShadow': f"0 6px 22px rgba(0,0,0,0.45), 0 0 18px rgba(200,170,110,0.08)",
    'flex': '1',
    'minWidth': '0',
}

# ── Карточка (для champion_card и др.) ──
CARD_STYLE = {
    'backgroundColor': COLORS['card_bg'],
    'borderRadius': '16px',
    'padding': '10px 10px',
    'border': f"1px solid {COLORS['primary']}",
    'textAlign': 'left',
    'boxShadow': f"0 6px 22px rgba(0,0,0,0.45), 0 0 18px rgba(200,170,110,0.08)",
}

# ── Контейнер фильтров ──
FILTER_CONTAINER_STYLE = {
    'display': 'flex',
    'flexWrap': 'wrap',
    'alignItems': 'center',
    'gap': '24px',
    'padding': '20px',
    'width': '100%',
    'boxSizing': 'border-box',
    'backgroundColor': COLORS['filter_bg'],
    'borderRadius': '8px',
    'marginBottom': '20px',
    'border': f"1px solid {COLORS['primary']}",
}

# ── Группа фильтров (лейбл + контрол) ──
FILTER_GROUP_STYLE = {
    'display': 'flex',
    'flexDirection': 'column',
    'alignItems': 'flex-start',
    'gap': '8px',
    'flex': '1 1 auto',
    'minWidth': '0',
}

# ── Выпадающий список ──
DROPDOWN_STYLE = {
    'backgroundColor': COLORS['header_bg'],
    'color': COLORS['text'],
    'border': f"1px solid {COLORS['primary']}",
    'borderRadius': '5px',
}

# ── Обёртка таблицы ──
TABLE_WRAPPER_STYLE = {
    'overflowX': 'hidden',
    'borderRadius': '8px',
    'border': f"1px solid {COLORS['border']}",
}

# ── Таблица ──
TABLE_STYLE = {
    'width': '100%',
    'borderCollapse': 'collapse',
    'fontFamily': FONT_FAMILY,
    'fontSize': f"{FS['sm']}px",
    'backgroundColor': COLORS['secondary'],
}

# ── Заголовок таблицы ──
TABLE_HEADER_STYLE = {
    'padding': '10px 6px',
    'backgroundColor': COLORS['header_bg'],
    'color': COLORS['primary'],
    'fontWeight': 'bold',
    'fontSize': f"{FS['sm']}px",
    'textAlign': 'center',
}

# ── Ячейка таблицы ──
TABLE_CELL_STYLE = {
    'padding': '8px 6px',
    'color': COLORS['text'],
    'fontWeight': 'bold',
    'fontSize': f"{FS['sm']}px",
}

# ── Базовый стиль лейбла ──
LABEL_BASE_STYLE = {
    'fontWeight': 'bold',
    'marginRight': '0',
    'marginBottom': '0',
    'display': 'block',
    'color': COLORS['primary'],
    'fontFamily': FONT_FAMILY,
}

# ── Заголовок H4 в секции ──
SECTION_H4_STYLE = {
    'marginBottom': '15px',
    'color': COLORS['primary'],
    'borderLeft': f"4px solid {COLORS['primary']}",
    'paddingLeft': '12px',
    'fontSize': f"{FS['lg']}px",
    'fontWeight': 'bold',
}

# ── Текст подсказки ──
HINT_STYLE = {
    'marginBottom': '15px',
    'color': COLORS['text_muted'],
    'fontSize': f"{FS['sm']}px",
    'fontStyle': 'italic',
}

# ── Шапка страницы ──
HEADER_STYLE = {
    'textAlign': 'center',
    'padding': '34px 0 22px 0',
    'borderBottom': f"1px solid {COLORS['border']}",
    'background': f"linear-gradient(180deg, rgba(200,170,110,0.04) 0%, rgba(0,0,0,0) 100%)",
}

# ── Контейнер (страница 3) ──
CONTAINER = {
    'width': '100%',
    'minWidth': f"{CONTENT_MIN_WIDTH}px",
    'maxWidth': '100%',
    'margin': '0 auto',
    'padding': '0 24px',
    'boxSizing': 'border-box',
}

# ── Строка заголовков зон (Aggression / Interplay / Map Control) ──
ZONE_HEADER_ROW = {
    'display': 'grid',
    'gridTemplateColumns': 'repeat(3, 1fr)',
    'gap': '18px',
    'margin': '34px 0 14px 0',
}

# ── Сетка из 3 колонок ──
ROW3 = {
    'display': 'grid',
    'gridTemplateColumns': 'repeat(3, 1fr)',
    'gap': '18px',
    'marginBottom': '18px',
}

# ── Селектор региона ──
REGION_SELECTOR_STYLE = {'textAlign': 'center', 'marginTop': '14px'}

REGION_LABEL_STYLE = {
    'color': COLORS['text'],
    'fontSize': f"{FS['md']}px",
    'cursor': 'pointer',
    'marginRight': '24px',
    'letterSpacing': '1px',
    'display': 'inline-flex',
    'alignItems': 'center',
}

REGION_INPUT_STYLE = {
    'marginRight': '7px',
    'cursor': 'pointer',
    'accentColor': COLORS['primary'],
    'transform': 'scale(1.3)',
}

# ── Детальная таблица (в панели чемпиона) ──
DETAIL_TABLE_STYLE = {
    'width': '100%',
    'borderCollapse': 'collapse',
    'color': COLORS['text'],
    'tableLayout': 'fixed',
}


# ── ★ Панели с цветным акцентом для Blood & Objectives ──
def _panel(accent):
    """
    Создаёт стиль панели с цветной верхней границей и свечением.
    accent — HEX-цвет акцента (blood, gold_dim, teal).
    """
    return {
        'background': f"linear-gradient(180deg, {COLORS['panel']}, rgba(0,0,0,0.25))",
        'borderRadius': '16px',
        'padding': '6px 6px 4px 6px',
        'border': f"1px solid {accent}",
        'borderTop': f"2px solid {accent}",
        'boxShadow': f"0 6px 22px rgba(0,0,0,0.45), 0 0 18px {rgba(accent, 0.10)}",
        'flex': '1',
        'minWidth': '0',
    }

PANEL_BLOOD = _panel(COLORS['blood'])      # красная панель (агрессия)
PANEL_GOLD  = _panel(COLORS['gold_dim'])   # золотая панель (взаимодействие)
PANEL_TEAL  = _panel(COLORS['teal'])       # бирюзовая панель (контроль карты)


# ============================================================
# 4. UTILS — общие функции
# ============================================================

def get_connection():
    """
    Возвращает соединение с DuckDB.
    Используется всеми страницами для чтения витрин.
    """
    return duckdb.connect(os.path.join(DB_PATH, "lol.duckdb"))


def region_color(region):
    """
    Возвращает цвет для региона (EU = teal, US = gold).
    Используется для раскраски графиков по регионам.
    """
    return REGION_COLOR.get(str(region).upper(), COLORS['muted'])


def ordered_regions(values):
    """
    Сортирует регионы: сначала EU, US, потом остальные по алфавиту.
    values — список/Series уникальных названий регионов.
    """
    vals = pd.Series(values).dropna().astype(str).str.upper().str.strip().unique().tolist()
    pref = [r for r in REGION_PREF_ORDER if r in vals]
    return pref + sorted([r for r in vals if r not in REGION_PREF_ORDER])


def ordered_tiers(values):
    """
    Сортирует тиры в каноническом порядке (Iron → Challenger), потом остальные.
    """
    vals = pd.Series(values).dropna().astype(str).str.upper().str.strip().unique().tolist()
    pref = [t for t in TIER_ORDER if t in vals]
    return pref + sorted([t for t in vals if t not in TIER_ORDER])


def normalize_tier(series):
    """
    Приводит названия тиров к единому стандарту:
    UNKNOWN, GRAND MASTER → GRANDMASTER, пустые → UNKNOWN.
    """
    return (series.fillna('UNKNOWN').astype(str).str.upper().str.strip()
            .replace({
                '': 'UNKNOWN', 'NONE': 'UNKNOWN', 'NAN': 'UNKNOWN',
                'NULL': 'UNKNOWN', 'GRAND MASTER': 'GRANDMASTER',
            }))


def filter_region(df, region_key):
    """
    Фильтрует DataFrame по региону.
    'ALL' возвращает без фильтрации, иначе — точное совпадение.
    """
    if df is None or df.empty or region_key == 'ALL':
        return df
    return df[df['region'] == str(region_key).upper()]


def fmt_minutes(value):
    """
    Конвертирует минуты (float) в строку 'MM:SS'.
    Пример: 23.5 → '23:30'.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    total_sec = int(round(float(value) * 60))
    return f"{total_sec // 60:02d}:{total_sec % 60:02d}"


def compute_peak(df):
    """
    Находит день недели и час с максимальным количеством матчей.
    Возвращает (weekday, hour) или (None, None) если данных нет.
    """
    if df is None or df.empty:
        return None, None
    grouped = df.groupby(['weekday', 'hour']).size()
    if grouped.empty:
        return None, None
    peak_day, peak_hour = grouped.idxmax()
    return peak_day, int(peak_hour)


def weekend_shapes(dates, yref='y', y0=0, y1=1):
    """
    Создаёт список прямоугольников для подсветки выходных дней на графике.
    yref — ось Y, к которой привязаны прямоугольники.
    """
    shapes = []
    dates = pd.to_datetime(pd.Series(dates)).sort_values().reset_index(drop=True)
    for d in dates:
        if d.weekday() >= 5:                         # суббота (5) или воскресенье (6)
            shapes.append(dict(
                type='rect', xref='x', yref=yref,
                x0=d - pd.Timedelta(hours=12),       # от начала дня
                x1=d + pd.Timedelta(hours=12),       # до конца дня
                y0=y0, y1=y1,
                fillcolor=COLORS['purple'],
                opacity=0.10,
                line=dict(width=0),
                layer='below',                        # под данными
            ))
    return shapes


def gold_title(text):
    """Заголовок в верхнем регистре (для страницы 2)."""
    return str(text).upper()


def titled(text):
    """Заголовок графика в верхнем регистре (возвращает dict для Plotly)."""
    return {**TITLE_BASE, 'text': str(text).upper()}


def centered_axis_title(text):
    """Подпись оси с единым шрифтом и цветом текста."""
    return dict(text=text, font=dict(color=COLORS['text'], family=FONT_FAMILY))


def ax(text):
    """Подпись оси (краткий алиас с приглушённым цветом)."""
    return dict(text=text, font=dict(color=COLORS['muted'], size=11, family=FONT_FAMILY))


def section_title(text):
    """Заголовок секции с левым выравниванием (для страницы 2)."""
    return html.H2(text.upper(), style=SECTION_TITLE_STYLE_LEFT)


def zone_label(text, color, align):
    """
    Заголовок зоны (Aggression / Interplay / Map Control).
    С цветной полоской-градиентом под текстом.
    align: 'left', 'center', или 'right' — направление градиента.
    """
    gradient = (
        f"linear-gradient(90deg, {rgba(color, 0.6)}, rgba(0,0,0,0))"
        if align == 'left' else
        f"linear-gradient(270deg, {rgba(color, 0.6)}, rgba(0,0,0,0))"
        if align == 'right' else
        f"linear-gradient(90deg, rgba(0,0,0,0), {rgba(color, 0.6)}, rgba(0,0,0,0))"
    )
    return html.Div([
        html.Span(text, style={
            'color': color,
            'fontSize': f"{FS['lg']}px",
            'letterSpacing': '3px',
            'textTransform': 'uppercase',
            'fontWeight': '600',
        }),
        html.Div(style={'height': '2px', 'marginTop': '6px', 'background': gradient}),
    ], style={'textAlign': 'center'})


def region_scope_label(region_key):
    """
    Возвращает читаемую подпись для региона.
    'ALL' → 'ALL REGIONS', иначе как есть.
    """
    return "ALL REGIONS" if region_key == 'ALL' else region_key


def empty_fig(message="No data", height=GRAPH_H):
    """
    Создаёт фигуру-заглушку с сообщением.
    Возвращает go.Figure (не dcc.Graph — обёртку делает вызывающий код).
    """
    fig = go.Figure()
    fig.update_layout(**LAYOUT, height=height)
    fig.add_annotation(
        text=message, x=0.5, y=0.5, showarrow=False,
        font=dict(color=COLORS['text_muted'], size=14),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def apply_grid(fig, *, xlab=None, ylab=None, x_extra=None, y_extra=None):
    """
    Применяет единую сетку (GRID_FULL) к осям фигуры.
    Опционально добавляет подписи осей и дополнительные настройки.
    """
    xkw = dict(GRID_FULL)
    ykw = dict(GRID_FULL)
    if xlab is not None:
        xkw['title'] = ax(xlab)
    if ylab is not None:
        ykw['title'] = ax(ylab)
    if x_extra:
        xkw.update(x_extra)
    if y_extra:
        ykw.update(y_extra)
    fig.update_xaxes(**xkw)
    fig.update_yaxes(**ykw)
    return fig


# ── ★ Chart panel с иконкой «?» для подсказки ──
def chart_panel(component, graph_id=None, accent=None, panel_style=None):
    """
    Оборачивает график в панель с иконкой «?» в правом верхнем углу.
    При наведении показывает описание графика из CHART_INFO.
    
    component   — dcc.Graph или html.Div с графиком
    graph_id    — ключ в словаре CHART_INFO (если None — иконка не показывается)
    accent      — цвет рамки иконки (по умолчанию primary)
    panel_style — переопределение стиля панели
    """
    accent = accent or COLORS['primary']
    style = panel_style or PANEL_STYLE
    children = []

    if graph_id is not None:
        info_text = CHART_INFO.get(graph_id, "Description coming soon.")
        children.append(html.Div([
            html.Span("?", className="info-icon",
                      style={'borderColor': accent, 'color': accent}),
            html.Span(info_text, className="info-tip",
                      style={'borderTopColor': accent}),
        ], className="info-wrap"))

    children.append(component)
    return html.Div(children, style={**style, 'position': 'relative'})


# ============================================================
# 5. CHART DESCRIPTIONS — подсказки для всех графиков
# ============================================================

CHART_INFO = {
    # ── Страница 2: Match Overview ──
    'matches-donut-panel':
        "Key match-level KPIs at a glance: total matches, average game length, "
        "the EU/US split, the surrender rate, and the remake rate. "
        "The header shows how many days of data are covered. "
        "Surrender = a game that ended by /ff (a team gave up) rather than by "
        "destroying the nexus; remakes are excluded from this rate. "
        "Remake = a game canceled in the first 5 minutes (early dodge or "
        "disconnect). Remakes are excluded from every other chart, so the "
        "remake rate here tells you how much raw data was filtered out.",

    'side-gauge-panel':
        "Overall win rate of Blue vs Red side. "
        "The needle leans toward the stronger side; 50% = perfectly balanced. "
        "Reveals whether map sides give a built-in advantage.",

    'heatmap-panel':
        "Match activity by weekday and hour (in players' local time). "
        "Brighter cells = more games. The 🔥 marker flags the peak slot. "
        "Shows when the servers are busiest.",

    'matches-trend-panel':
        "Daily uploaded matches (gold area) and average game length (dotted line). "
        "Purple bands mark weekends. "
        "Tracks data volume and whether games are getting longer or shorter.",

    'side-trend-panel':
        "Blue-side win rate over time, smoothed daily. "
        "Above the 50% line = Blue favored; below = Red favored. "
        "Dotted gold lines mark patch updates — watch for shifts after a patch.",

    'histogram-panel':
        "Distribution of match durations. "
        "The tall peak is a 'typical' game length; the tail = marathon games. "
        "Dashed lines: red — median, blue — average.",

    'duration-box-region-panel':
        "Match duration spread per region (box = middle 50%, dots = outliers). "
        "The label shows each region's average length. "
        "Compares how long games run across regions.",

    'cancel-rate-panel':
        "Surrender rate by game length, split by region (remakes excluded). "
        "Longer games tend to end in surrender more often. "
        "The gold dashed line is the overall average across both regions.",

    'lp-wr-scatter-panel':
        "Each dot is a player: League Points (X) vs personal win rate (Y). "
        "Dot size = games played. "
        "Shows whether higher LP actually comes with a higher win rate.",

    'tier-distribution-panel':
        "Breakdown of top players by tier (Master / Grandmaster / Challenger), "
        "EU on top, US below. "
        "Compares how the elite ladder is composed in each region.",

    'player-lp-tier-panel':
        "League Points distribution within each top tier, split by region. "
        "Dotted curves show the density shape per tier. "
        "Reveals how LP is spread and where each tier concentrates.",

    'player-wr-region-panel':
        "Win-rate distribution of players per region (violin = density). "
        "The box and mean line summarize the center. "
        "Compares how competitive players perform across regions.",

    # ── Страница 3: Blood & Objectives ──
    'kills-dist':
        "Distribution of total kills per match. "
        "The tall peak marks a 'typical' game; the long right tail = bloodbaths. "
        "Dashed lines: red — median, gold — average. "
        "Shows how aggressive the current meta is.",

    'meta-pulse':
        "Two timelines: kills/min (red) and objectives/min (teal), "
        "smoothed by a 14-day trend. "
        "Reveals whether the meta is shifting toward fighting or map control. "
        "Diverging lines = a change in play style across patches.",

    'obj-dist':
        "Distribution of objectives per match "
        "(dragons + barons + towers + inhibitors). "
        "A shift to the right means longer games with heavier map control. "
        "Dashed lines mark the median and average of the sample.",

    'kda-violin':
        "Shape of the KDA distribution for winners vs losers. "
        "The width of each 'violin' = density of players with that KDA. "
        "The gap (⚡) shows how much combat efficiency decides the outcome.",

    'kills-dur':
        "Each dot is a match: game length (X) vs total kills (Y). "
        "Color = kills/min (gold → blood = more aggressive). "
        "Highlights the link: longer games rack up more kills, "
        "though not always at a faster pace.",

    'vision-dist':
        "Distribution of vision score per minute across players. "
        "A map-control metric: wards, sight, and area control. "
        "A shift to the right = greater map awareness. "
        "Dashed lines mark median and average.",

    'dmg-dist':
        "Player density across damage/min (X) and kill participation % (Y). "
        "Bright zones = the most common player profiles. "
        "The top-right corner = carries (high damage + present in fights). "
        "Shows how damage converts into team contribution.",

    'winners-losers':
        "How much winners outperform losers on each metric (in %). "
        "Longer bars = the metric matters more for winning. "
        "Bar color = zone (blood / gold / teal). "
        "A quick answer to: 'what wins games?'",

    'oci-comp':
        "What makes up map control (OCI) for winners vs losers. "
        "Each segment is an objective's weighted contribution: "
        "baron ×2, dragon/inhibitor ×1, tower ×0.5. "
        "Compares control structure and flags the key driver of victory.",

    'fb-gauge':
        "Win rate of teams that secured First Blood. "
        "Needle above 50% = early tempo gives a real edge. "
        "The 50% threshold marks a 'coin-flip' baseline. "
        "Shows the value of early aggression.",

    'region-compare':
        "Mirror comparison of two regions across key metrics. "
        "A bar to the right = the left region leads; to the left = the right region. "
        "The (+%) label shows the size of the gap. "
        "Reveals stylistic differences in the meta between regions.",

    'vision-gauge':
        "How much more vision score winners have than losers (in %). "
        "A positive value = vision control correlates with winning. "
        "Below: the absolute vision/min values for both groups.",
}


# ============================================================
# 6. АВТОР И ПОДВАЛ
# ============================================================

AUTHOR = {
    'name':     'Yuri Kuznetsov',
    'github':   'https://github.com/Urizen-Data',
    'telegram': 'https://t.me/urizen6',
    'email':    'urizen@rambler.ru',
}


def build_footer():
    """
    Создаёт минималистичный подвал с информацией об авторе.
    Ссылки: GitHub, Telegram, Email.
    """
    muted = '#6E6D69'

    def link(label, href):
        """Создаёт ссылку с hover-эффектом (золотой при наведении)."""
        return html.A(
            label, href=href, target='_blank',
            className='author-link',
            style={
                'color': muted,
                'fontSize': f"{FS['sm']}px",
                'margin': '0 14px',
            },
        )

    return html.Div([
        # Золотой разделитель
        html.Hr(style={
            'border': 'none', 'height': '1px', 'width': '60%',
            'margin': '8px auto 8px auto',
            'background': (
                f"linear-gradient(90deg, {rgba(COLORS['primary'], 0)}, "
                f"{rgba(COLORS['primary'], 0.4)}, {rgba(COLORS['primary'], 0)})"
            ),
        }),
        # Информация об авторе
        html.Div([
            html.Span(f"© {AUTHOR['name']}", style={
                'color': muted, 'fontSize': f"{FS['sm']}px",
                'letterSpacing': '0.3px', 'marginRight': '14px',
            }),
            link("GitHub", AUTHOR['github']),
            link("Telegram", AUTHOR['telegram']),
            link("Email", f"mailto:{AUTHOR['email']}"),
        ], style={
            'display': 'flex', 'justifyContent': 'center', 'alignItems': 'center',
            'flexWrap': 'wrap', 'marginBottom': '14px',
        }),
    ])


# ============================================================
# 7. ГЛОБАЛЬНЫЙ CSS — встраивается в app.index_string
# ============================================================

GLOBAL_CSS = '''
<style>
    /* ── Иконка «?» с подсказкой ── */
    .info-wrap {
        position: absolute; top: 6px; right: 10px; z-index: 20;
    }
    .info-icon {
        display: inline-flex; align-items: center; justify-content: center;
        width: 18px; height: 18px; border-radius: 50%;
        border: 1.5px solid; font-size: 11px; font-weight: 700;
        font-family: "Beaufort for LoL", serif; cursor: help;
        opacity: 0.55; transition: opacity 0.2s ease;
        background: rgba(10,17,20,0.7);
    }
    .info-wrap:hover .info-icon { opacity: 1; }

    /* ── Всплывающая подсказка ── */
    .info-tip {
        visibility: hidden; opacity: 0;
        position: absolute; top: 24px; right: 0;
        width: 280px; padding: 12px 14px;
        background: #1a1f24; color: #F0E6D2;
        font-family: "Beaufort for LoL", "Times New Roman", serif;
        font-size: 12.5px; line-height: 1.55; letter-spacing: 0.3px;
        text-align: left;
        border: 1px solid #C8AA6E; border-top: 2px solid #C8AA6E;
        border-radius: 12px;
        box-shadow: 0 8px 26px rgba(0,0,0,0.6), 0 0 18px rgba(200,170,110,0.10);
        transition: opacity 0.2s ease, visibility 0.2s ease;
        pointer-events: none;
    }
    .info-wrap:hover .info-tip { visibility: visible; opacity: 1; }

    /* ── Ссылки автора ── */
    .author-link {
        text-decoration: none; transition: color 0.2s ease;
    }
    .author-link:hover { color: #C8AA6E; }
</style>
'''