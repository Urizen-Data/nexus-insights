# ============================================================
# DASHBOARD PAGE 2 — Match Overview
# ============================================================
# Использует: common.py — все общие константы, стили, функции
# Показывает: KPI матчей, тренды, тепловую карту активности,
#             распределение длительности, сравнение регионов,
#             surrender rate, LP vs WR, распределение тиров
# ============================================================

# ── Добавляем корень проекта в путь для импорта common ──
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Импорт библиотек ──
import dash                                          # сам Dash
from dash import dcc, html, Input, Output, callback  # компоненты и callback-инструменты
from plotly.subplots import make_subplots            # subplot-ы для графиков с двумя осями Y
import pandas as pd                                  # работа с данными (DataFrame)
import numpy as np                                   # числовые операции
import plotly.graph_objects as go                    # низкоуровневые графики Plotly

# ── ★ Импорт всего общего из common.py ──
from common import (
    # Конфигурация
    COLORS, FONT_FAMILY, FS, DB_PATH, REMAKE_SEC,
    REGION_OPTIONS, REGION_PREF_ORDER, REGION_LOCAL_TZ,
    TIER_ORDER, TIER_COLOR_MAP, TOP_TIERS,
    DAY_ORDER, DAY_FULL, WEEKEND_DAYS,
    CHART_HEIGHT, TALL_H, TALL_HEIGHT, HIST_BIN_MIN, CHART_INFO, AUTHOR,
    # Алиасы цветов
    LOL_GOLD, LOL_GREEN, LOL_DARK, LOL_TEXT, C,
    # Утилиты
    rgba, get_connection, ordered_regions, ordered_tiers,
    normalize_tier, filter_region, fmt_minutes,
    compute_peak, weekend_shapes, region_color,
    gold_title, centered_axis_title, section_title,
    region_scope_label, empty_fig, chart_panel, build_footer,
    # Стили
    APP_STYLE, PAGE_TITLE_STYLE, SECTION_TITLE_STYLE_LEFT,
    PANEL_STYLE, REGION_SELECTOR_STYLE, REGION_LABEL_STYLE,
    REGION_INPUT_STYLE,
    # Plotly
    GRAPH_CONFIG, PLOTLY_LAYOUT, NO_GRID, PAD,
)

# ============================================================
# 1. КОНФИГУРАЦИЯ (специфичная для этой страницы)
# ============================================================

# ── Схема в DuckDB, откуда читаем витрины для этой страницы ──
MATCH_SCHEMA = "lol_match_overview"


# ============================================================
# 2. ЗАГРУЗКА ДАННЫХ — чтение витрин из DuckDB
# ============================================================

# ── Подключаемся к DuckDB через общую функцию ──
conn = get_connection()

# ── ★ Читаем готовые витрины из схемы lol_match_overview ──
RAW     = conn.execute(f"SELECT * FROM {MATCH_SCHEMA}.match_overview_matches").df()
SIDES   = conn.execute(f"SELECT * FROM {MATCH_SCHEMA}.match_overview_sides").df()
CANCEL  = conn.execute(f"SELECT * FROM {MATCH_SCHEMA}.match_overview_cancel").df()
PLAYERS = conn.execute(f"SELECT * FROM {MATCH_SCHEMA}.match_overview_players").df()

conn.close()

# ────────────────────────────────────────────────────────────
# Пост-обработка: RAW (данные матчей)
# ────────────────────────────────────────────────────────────
if not RAW.empty:
    # Приводим регион к верхнему регистру для единообразия
    RAW['region'] = RAW['region'].astype(str).str.upper().str.strip()
    # Помечаем ремейки (матчи короче 300 секунд)
    RAW['is_remake'] = RAW['game_duration'] < REMAKE_SEC

    # Конвертируем timestamp в UTC
    start_utc = pd.to_datetime(RAW['game_start_timestamp'], unit='ms', utc=True)
    hour = pd.Series(index=RAW.index, dtype='float')
    weekday = pd.Series(index=RAW.index, dtype='object')

    # Переводим UTC в локальное время для каждого региона (с учётом DST)
    for region, tz in REGION_LOCAL_TZ.items():
        mask = RAW['region'] == region
        if mask.any():
            local = start_utc[mask].dt.tz_convert(tz)
            hour.loc[mask] = local.dt.hour
            weekday.loc[mask] = local.dt.weekday.map(DAY_FULL)

    # Прочие регионы — оставляем в UTC
    other = ~RAW['region'].isin(REGION_LOCAL_TZ)
    if other.any():
        local = start_utc[other]
        hour.loc[other] = local.dt.hour
        weekday.loc[other] = local.dt.weekday.map(DAY_FULL)

    RAW['hour'] = hour.astype(int)
    RAW['weekday'] = weekday
    # Извлекаем номер патча из версии игры (например "14.12.1" → "14.12")
    RAW['patch'] = RAW['game_version'].astype(str).str.extract(r'^(\d+\.\d+)')

# ────────────────────────────────────────────────────────────
# Пост-обработка: SIDES (баланс синей/красной стороны)
# ────────────────────────────────────────────────────────────
if not SIDES.empty:
    SIDES['region'] = SIDES['region'].astype(str).str.upper().str.strip()

# ────────────────────────────────────────────────────────────
# Пост-обработка: CANCEL (данные о сдаче/ремейках)
# ────────────────────────────────────────────────────────────
if not CANCEL.empty:
    CANCEL['region'] = CANCEL['region'].astype(str).str.upper().str.strip()
    CANCEL['is_remake'] = CANCEL['game_duration'] < REMAKE_SEC
    # Сдача = surrender, но НЕ ремейк (у ремейков surrender тоже true)
    CANCEL['surrendered'] = ((CANCEL['surrendered'] == 1) & (~CANCEL['is_remake'])).astype(int)

# ────────────────────────────────────────────────────────────
# Пост-обработка: PLAYERS (данные игроков)
# ────────────────────────────────────────────────────────────
if not PLAYERS.empty:
    PLAYERS['region'] = PLAYERS['region'].fillna('UNKNOWN').astype(str).str.upper().str.strip()
    PLAYERS['tier'] = normalize_tier(PLAYERS['tier'])                     # нормализация тиров
    PLAYERS['leaguePoints'] = pd.to_numeric(PLAYERS['leaguePoints'], errors='coerce')
    PLAYERS['wins'] = pd.to_numeric(PLAYERS['wins'], errors='coerce').fillna(0)
    PLAYERS['losses'] = pd.to_numeric(PLAYERS['losses'], errors='coerce').fillna(0)
    PLAYERS = PLAYERS.dropna(subset=['leaguePoints'])
    PLAYERS = PLAYERS[PLAYERS['leaguePoints'] >= 0].copy()
    PLAYERS['games_played'] = PLAYERS['wins'] + PLAYERS['losses']
    PLAYERS['win_rate'] = PLAYERS['wins'] / PLAYERS['games_played'].where(PLAYERS['games_played'] > 0)
    # Формируем читаемое имя игрока (Name#Tag)
    PLAYERS['player_name'] = PLAYERS['riot_game_name'].fillna('Unknown').astype(str)
    has_tag = PLAYERS['riot_tagline'].fillna('').astype(str).str.strip().ne('')
    PLAYERS.loc[has_tag, 'player_name'] = (
        PLAYERS.loc[has_tag, 'player_name'] + '#' + PLAYERS.loc[has_tag, 'riot_tagline'].astype(str))

# Лог загрузки
print(f"✅ matches={len(RAW)} | sides={len(SIDES)} | cancel={len(CANCEL)} | players={len(PLAYERS)}")


# ============================================================
# 3. COMPONENTS — построители графиков (figure builders)
# ============================================================

def matches_kpi(df, region_key='ALL'):
    """
    Сводка KPI по матчам: общее число, разбивка по регионам,
    средняя длительность, surrender rate, remake rate.
    """
    if df.empty:
        return dcc.Graph(figure=empty_fig("No match data", height=CHART_HEIGHT), config=GRAPH_CONFIG)

    total = len(df)
    # Количество матчей по регионам
    vc = df['region'].value_counts()
    eu = int(vc.get('EU', 0))
    us = int(vc.get('US', 0))
    eu_pct = 100.0 * eu / total if total else 0
    us_pct = 100.0 * us / total if total else 0

    avg_dur = fmt_minutes(df['duration_min'].mean())

    # Процент ремейков
    regions_in_df = df['region'].unique().tolist()
    raw_scope = RAW[RAW['region'].isin(regions_in_df)] if not RAW.empty else RAW
    remake_pct = 100.0 * raw_scope['is_remake'].mean() if not raw_scope.empty else 0

    # Число дней в данных
    dates = pd.to_datetime(df['match_date'])
    n_days = (dates.max() - dates.min()).days + 1 if total else 0

    def tile(value, label, color, size):
        """Одна плитка KPI: значение + подпись."""
        return html.Div([
            html.Div(value, style={
                'fontSize': f'{size}px', 'fontWeight': '700', 'color': color,
                'lineHeight': '1.0', 'fontFamily': FONT_FAMILY,
                'textShadow': f"0 0 18px {rgba(color, 0.35)}"}),
            html.Div(label, style={
                'fontSize': f"{FS['xs']}px", 'color': COLORS['text_muted'],
                'letterSpacing': '1.2px', 'marginTop': '6px',
                'textTransform': 'uppercase'}),
        ], style={'textAlign': 'center', 'padding': '4px 6px'})

    # Строка с общим числом матчей
    row_total = html.Div(
        tile(f"{total:,}", "Total Matches", COLORS['text'], 60),
        style={'display': 'flex', 'justifyContent': 'center'})

    # Строка с разбивкой по EU/US
    row_regions = html.Div([
        tile(f"{eu_pct:.0f}%", f"EU · {eu:,}", region_color('EU'), 48),
        tile(f"{us_pct:.0f}%", f"US · {us:,}", region_color('US'), 48),
    ], style={'display': 'flex', 'justifyContent': 'center', 'gap': '48px'})

    # Surrender rate
    if not CANCEL.empty:
        cancel_scope = CANCEL[CANCEL['region'].isin(REGION_PREF_ORDER)].copy()
        cancel_scope = cancel_scope[~cancel_scope['is_remake']]
        if region_key != 'ALL':
            cancel_scope = cancel_scope[cancel_scope['region'] == str(region_key).upper()]
        surr_pct = 100.0 * cancel_scope['surrendered'].mean() if not cancel_scope.empty else 0
    else:
        surr_pct = 0

    # Строка со статистиками
    row_stats = html.Div([
        tile(f"{avg_dur}", "Avg Duration", COLORS['text'], 36),
        tile(f"{surr_pct:.1f}%", "Surrender Rate", COLORS['text'], 36),
        tile(f"{remake_pct:.1f}%", "Remake Rate", COLORS['text'], 36),
    ], style={'display': 'flex', 'justifyContent': 'center', 'gap': '32px'})

    def gold_divider():
        """Золотой разделитель между строками KPI."""
        return html.Hr(style={
            'border': 'none', 'height': '1px', 'width': '70%',
            'background': f"linear-gradient(90deg, {rgba(COLORS['primary'], 0)}, "
                          f"{COLORS['primary']}, {rgba(COLORS['primary'], 0)})",
            'margin': '0', 'opacity': '0.6'})

    return html.Div([
        html.Div(f"over {n_days} days", style={
            'textAlign': 'center', 'color': COLORS['text_muted'],
            'fontSize': f"{FS['xs']}px", 'paddingTop': '6px'}),
        html.Div([
            row_total, gold_divider(), row_regions, gold_divider(), row_stats,
        ], style={
            'display': 'flex', 'flexDirection': 'column',
            'justifyContent': 'space-evenly', 'alignItems': 'center',
            'height': f"{CHART_HEIGHT - 30}px"}),
    ])


def matches_duration_trend(df):
    """
    Совмещённый график: столбцы (число матчей) + линия (средняя длительность).
    Выходные дни подсвечены.
    """
    if df.empty:
        return dcc.Graph(figure=empty_fig("No match data", height=CHART_HEIGHT), config=GRAPH_CONFIG)

    d = df.assign(date=pd.to_datetime(df['match_date']))
    daily = (d.groupby('date')
             .agg(matches=('match_id', 'count'), avg_dur=('duration_min', 'mean'))
             .reset_index().sort_values('date'))

    # Subplot с двумя осями Y
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Подсветка выходных
    for sh in weekend_shapes(daily['date'], yref='y',
                             y0=0, y1=float(daily['matches'].max()) * 1.05 if len(daily) else 1):
        fig.add_shape(sh)

    # Столбцы — число матчей (левая ось)
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['matches'], mode='lines', fill='tozeroy', name='Matches',
        line=dict(color=COLORS['pickrate'], width=2.4, shape='spline'),
        fillcolor='rgba(200,170,110,0.12)',
        hovertemplate="<b>%{x|%a, %d %b}</b><br>Matches: %{y}<extra></extra>"),
        secondary_y=False)

    # Линия — средняя длительность (правая ось)
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['avg_dur'], mode='lines', name='Avg Duration',
        line=dict(color=COLORS['kda'], width=1.4, dash='dot', shape='spline'),
        customdata=[fmt_minutes(v) for v in daily['avg_dur']],
        hovertemplate="<b>%{x|%a, %d %b}</b><br>Avg: %{customdata}<extra></extra>"),
        secondary_y=True)

    # Маркер для легенды "Weekend"
    fig.add_trace(go.Scatter(x=[None], y=[None], mode='markers',
                             marker=dict(size=11, color=COLORS['purple'], opacity=0.4, symbol='square'),
                             name='Weekend', hoverinfo='skip'), secondary_y=False)

    fig.update_layout(**{**PLOTLY_LAYOUT, 'margin': dict(l=44, r=48, t=42, b=58)},
                      title=gold_title("Matches Uploaded & Avg Duration Trend"), height=CHART_HEIGHT,
                      legend=dict(orientation='h', yanchor='top', y=-0.22, xanchor='center',
                                  x=0.5, font=dict(color=COLORS['text'], size=11)))
    fig.update_xaxes(**NO_GRID)
    fig.update_yaxes(title=centered_axis_title("Matches"), showgrid=True,
                     gridcolor=COLORS['grid'], zeroline=False,
                     tickfont=dict(color=COLORS['pickrate']), secondary_y=False)
    fig.update_yaxes(title=centered_axis_title("Avg (min)"), showgrid=False, zeroline=False,
                     tickfont=dict(color=COLORS['kda']), secondary_y=True)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def side_winrate_gauge(region_key):
    """
    Полукруглый gauge баланса сторон (Blue vs Red).
    Стрелка показывает % побед красной стороны.
    """
    df = filter_region(SIDES, region_key)
    if df.empty:
        return dcc.Graph(figure=empty_fig("No side data"), config=GRAPH_CONFIG)

    total = len(df)
    blue_pct = 100.0 * int(df['blue_win'].sum()) / total
    red_pct = 100.0 - blue_pct

    # Определяем лидера
    if blue_pct >= red_pct:
        leader, lead_color, edge = 'BLUE', COLORS['blue_side'], blue_pct - 50.0
    else:
        leader, lead_color, edge = 'RED', COLORS['red_side'], red_pct - 50.0

    AX_MIN, AX_MAX = 40, 60
    needle = min(max(red_pct, AX_MIN), AX_MAX)

    fig = go.Figure(go.Indicator(
        mode="gauge", value=needle, domain=dict(x=[0, 1], y=[0.18, 0.88]),
        gauge=dict(
            shape='angular',
            axis=dict(range=[AX_MIN, AX_MAX], tickmode='array', tickvals=[40, 45, 50, 55, 60],
                      ticktext=['', '', '50%', '', ''], tickcolor=COLORS['text_muted'],
                      tickfont=dict(color=COLORS['text_muted'], size=11)),
            bar=dict(color='rgba(0,0,0,0)', thickness=0), bgcolor='rgba(0,0,0,0)', borderwidth=0,
            steps=[dict(range=[40, 50], color=COLORS['blue_glow']),    # голубая половина
                   dict(range=[50, 60], color=COLORS['red_glow'])],     # красная половина
            threshold=dict(line=dict(color=COLORS['text'], width=5), thickness=0.9, value=needle),
        ),
    ))

    # Подпись лидера под кольцом
    fig.add_annotation(x=0.5, y=0.4, xref='paper', yref='paper', showarrow=False,
                       text=f"<b>{leader}</b>",
                       font=dict(color=lead_color, size=40, family=FONT_FAMILY))
    fig.add_annotation(x=0.5, y=0.2, xref='paper', yref='paper', showarrow=False,
                       text=("balanced" if edge < 0.5 else f"+{edge:.1f}% edge"),
                       font=dict(color=COLORS['text_muted'], size=16, family=FONT_FAMILY))
    # Подписи процентов по краям
    fig.add_annotation(x=0.1, y=0.99, xref='paper', yref='paper', showarrow=False,
                       text=f"◆ BLUE<br>{blue_pct:.1f}%", align='center',
                       font=dict(color=COLORS['blue_side'], size=18, family=FONT_FAMILY))
    fig.add_annotation(x=0.9, y=0.99, xref='paper', yref='paper', showarrow=False,
                       text=f"RED ◆<br>{red_pct:.1f}%", align='center',
                       font=dict(color=COLORS['red_side'], size=18, family=FONT_FAMILY))

    fig.update_layout(**{**PLOTLY_LAYOUT, 'margin': dict(l=15, r=5, t=46, b=6)},
                      title="⚔️ SIDE WIN RATE", height=CHART_HEIGHT)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def side_winrate_trend(df):
    """
    Динамика side win rate. Линия = Blue WR.
    Заливка: выше 50% — голубая, ниже 50% — красная.
    Вертикальные линии — границы патчей.
    """
    if df.empty:
        return dcc.Graph(figure=empty_fig("No side data"), config=GRAPH_CONFIG)

    d = df.copy()
    d['date'] = pd.to_datetime(d['match_date'])

    # Дневная агрегация
    daily = (d.groupby('date')
             .agg(blue_wins=('blue_win', 'sum'), matches=('blue_win', 'count'))
             .reset_index().sort_values('date'))
    daily['blue_wr'] = daily['blue_wins'] / daily['matches'] * 100.0
    daily['red_wr'] = 100.0 - daily['blue_wr']

    overall_blue = d['blue_win'].mean() * 100.0
    overall_red = 100.0 - overall_blue

    # Зоны выше/ниже 50%
    blue_fill = daily['blue_wr'].clip(lower=50.0)
    red_fill = daily['blue_wr'].clip(upper=50.0)

    fig = go.Figure()

    # Базовая линия 50% + голубая заливка
    fig.add_trace(go.Scatter(x=daily['date'], y=[50.0] * len(daily), mode='lines',
                             line=dict(width=0), hoverinfo='skip', showlegend=False))
    fig.add_trace(go.Scatter(x=daily['date'], y=blue_fill, mode='lines', line=dict(width=0),
                             fill='tonexty', fillcolor=COLORS['blue_glow'],
                             hoverinfo='skip', showlegend=False))

    # Базовая линия 50% + красная заливка
    fig.add_trace(go.Scatter(x=daily['date'], y=[50.0] * len(daily), mode='lines',
                             line=dict(width=0), hoverinfo='skip', showlegend=False))
    fig.add_trace(go.Scatter(x=daily['date'], y=red_fill, mode='lines', line=dict(width=0),
                             fill='tonexty', fillcolor=COLORS['red_glow'],
                             hoverinfo='skip', showlegend=False))

    # Основная линия Blue WR
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['blue_wr'], mode='lines',
        line=dict(color=COLORS['text'], width=2.5, shape='spline'),
        customdata=np.column_stack([daily['red_wr'], daily['matches']]),
        name='Blue WR', showlegend=False,
        hovertemplate=("<b>%{x|%a, %d %b %Y}</b><br>Blue WR: %{y:.1f}%<br>"
                       "Red WR: %{customdata[0]:.1f}%<br>Matches: %{customdata[1]:,}<extra></extra>")))

    # Горизонтальная линия 50%
    fig.add_hline(y=50, line_dash='dash', line_color=COLORS['text_muted'], line_width=1)

    # Вертикальные отметки границ патчей
    patches = (d.dropna(subset=['game_version'])
               .assign(patch=d['game_version'].astype(str).str.extract(r'^(\d+\.\d+)')[0])
               .dropna(subset=['patch'])
               .groupby('patch')['date'].min().reset_index().sort_values('date'))
    for _, row in patches.iloc[1:].iterrows():
        fig.add_shape(type='line', x0=row['date'], x1=row['date'], yref='paper', y0=0, y1=1,
                      line=dict(color=COLORS['primary'], width=1, dash='dot'))
        fig.add_annotation(x=row['date'], y=1.0, yref='paper', text=f"v{row['patch']}",
                           showarrow=False, xanchor='left', yanchor='bottom',
                           font=dict(color=COLORS['primary'], size=10, family=FONT_FAMILY))

    # Легенда: Patch update
    fig.add_trace(go.Scatter(x=[None], y=[None], mode='lines',
                             line=dict(color=COLORS['primary'], width=1, dash='dot'),
                             name='Patch update', showlegend=True, hoverinfo='skip'))

    # Заголовок с итоговыми процентами
    title = (f"Side Win Rate Trend · "
             f"<span style='color:{COLORS['blue_side']}'>Blue {overall_blue:.1f}%</span>"
             f" / <span style='color:{COLORS['red_side']}'>Red {overall_red:.1f}%</span>")

    fig.update_layout(**PLOTLY_LAYOUT, title=title, height=CHART_HEIGHT,
                      legend=dict(orientation='h', yanchor='top', y=-0.12, xanchor='center',
                                  x=0.5, font=dict(color=COLORS['text'], size=12),
                                  bgcolor='rgba(0,0,0,0)'))
    fig.update_xaxes(title=centered_axis_title(""), **NO_GRID)
    fig.update_yaxes(title=centered_axis_title("Blue Side WR (%)"), range=[25, 60],
                     tickvals=[40, 50, 60], ticktext=['±10%', '50%', '±10'],
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def weekday_hour_heatmap(df):
    """
    Тепловая карта активности: день недели × час (локальное время).
    Показывает в какие дни и часы больше всего матчей.
    """
    if df.empty:
        return dcc.Graph(figure=empty_fig(height=CHART_HEIGHT), config=GRAPH_CONFIG)

    # Строим pivot-таблицу: строки = дни, столбцы = часы
    pivot = (df.groupby(['weekday', 'hour']).size().unstack('hour')
             .reindex(DAY_ORDER).reindex(columns=range(24)).fillna(0))

    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=[f"{h:02d}" for h in pivot.columns], y=pivot.index,
        colorscale=[[0.0, COLORS['secondary']], [0.35, '#3a2f1a'],
                    [0.7, COLORS['pickrate']], [1.0, COLORS['banrate']]],
        xgap=2, ygap=2,
        colorbar=dict(title="Matches", tickfont=dict(color=COLORS['text']),
                      outlinewidth=0, thickness=12, len=0.9),
        hovertemplate="<b>%{y} · %{x}:00</b><br>Matches: %{z}<extra></extra>"))

    # Пик активности
    peak_day, peak_hour = compute_peak(df)
    if peak_day is not None:
        title = (f"{gold_title('Activity Heatmap')} · "
                 f"<span style='color:{COLORS['banrate']}'>🔥 {peak_day.upper()} {peak_hour:02d}:00</span>")
    else:
        title = gold_title("Activity Heatmap")

    fig.update_layout(**{**PLOTLY_LAYOUT, 'margin': dict(l=42, r=10, t=42, b=26)},
                      title=title, height=CHART_HEIGHT, autosize=True)
    fig.update_xaxes(title=centered_axis_title("Hour (local)"), **NO_GRID, automargin=False)
    fig.update_yaxes(title=centered_axis_title(""), **NO_GRID)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG, style={'width': '100%'})


def duration_histogram(df):
    """
    Гистограмма распределения длительности матчей.
    С медианой и средним.
    """
    if df.empty:
        return dcc.Graph(figure=empty_fig(), config=GRAPH_CONFIG)

    dur = df['duration_min']
    median, avg = dur.median(), dur.mean()

    fig = go.Figure(go.Histogram(
        x=dur, xbins=dict(size=HIST_BIN_MIN),
        marker=dict(color=COLORS['pickrate'],
                    line=dict(color=COLORS['secondary'], width=0.5), opacity=0.9),
        hovertemplate="<b>%{x} min</b><br>Matches: %{y}<extra></extra>"))

    # Вертикальные линии медианы и среднего
    fig.add_vline(x=median, line_dash="dash", line_color=COLORS['banrate'], line_width=2,
                  annotation_text=f"Median {fmt_minutes(median)}", annotation_position="top right",
                  annotation_font_color=COLORS['banrate'])
    fig.add_vline(x=avg, line_dash="dot", line_color=COLORS['kills'], line_width=2,
                  annotation_text=f"Avg {fmt_minutes(avg)}", annotation_position="top left",
                  annotation_font_color=COLORS['kills'])

    fig.update_layout(**PLOTLY_LAYOUT, title=gold_title("Match Duration Distribution"),
                      bargap=0.02, height=CHART_HEIGHT)
    fig.update_xaxes(title=centered_axis_title("Duration (min)"),
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    fig.update_yaxes(title=centered_axis_title("Matches"),
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def match_duration_boxplot_by_region(matches_df, selected_region='ALL'):
    """
    Boxplot длительности матчей по регионам.
    При выборе конкретного региона — остальные затемняются.
    """
    if matches_df.empty:
        return dcc.Graph(figure=empty_fig("No match data"), config=GRAPH_CONFIG)

    order = ordered_regions(matches_df['region'])
    fig = go.Figure()
    tick_text = {}

    for region in order:
        sub = matches_df[matches_df['region'] == region]
        if sub.empty:
            continue
        # Прозрачность: выбранный регион = 1.0, остальные = 0.35
        op = 1.0 if selected_region == 'ALL' or region == selected_region else 0.35
        tick_text[region] = f"{region} (AVG {fmt_minutes(sub['duration_min'].mean())})"
        fig.add_trace(go.Box(
            x=sub['region'], y=sub['duration_min'], name=region, boxpoints='outliers',
            marker_color=region_color(region), opacity=op, showlegend=False,
            hovertemplate="<b>%{x}</b><br>Duration: %{y:.2f} min<extra></extra>"))

    title = gold_title("Match Duration by Region")
    if selected_region != 'ALL':
        title += f" · {selected_region}"

    fig.update_layout(**PLOTLY_LAYOUT, title=title, height=CHART_HEIGHT)
    fig.update_xaxes(categoryorder='array', categoryarray=order,
                     title=centered_axis_title("Region"),
                     tickmode='array', tickvals=order,
                     ticktext=[tick_text.get(r, r) for r in order],
                     tickfont=dict(size=15, color=COLORS['text'], family=FONT_FAMILY),
                     **NO_GRID)
    fig.update_yaxes(title=centered_axis_title("Duration (min)"),
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def surrender_rate_by_duration(cancel_df, region_key):
    """
    Столбчатая диаграмма: % сдач по длительности матча (5-15, 15-20, 20-25, 25-30, 30+).
    Сравнение EU vs US.
    """
    if cancel_df.empty:
        return dcc.Graph(figure=empty_fig("No surrender data"), config=GRAPH_CONFIG)

    df = cancel_df[cancel_df['region'].isin(REGION_PREF_ORDER)].copy()
    df = df[~df['is_remake']].copy()                         # исключаем ремейки
    if df.empty:
        return dcc.Graph(figure=empty_fig("No EU/US data"), config=GRAPH_CONFIG)

    # Разбиваем на bucket-ы по длительности
    bins = [5, 15, 20, 25, 30, 200]
    labels = ['5–15', '15–20', '20–25', '25–30', '30+']
    df['bucket'] = pd.cut(df['duration_min'], bins=bins, labels=labels, right=False)

    # Агрегация по региону и bucket-у
    agg = (df.groupby(['region', 'bucket'], observed=True)['surrendered']
           .agg(['mean', 'count']).reset_index())
    agg['rate'] = agg['mean'] * 100.0

    selected = None if region_key == 'ALL' else str(region_key).upper()
    order = [r for r in REGION_PREF_ORDER if r in agg['region'].unique().tolist()]

    fig = go.Figure()
    for region in order:
        sub = agg[agg['region'] == region].set_index('bucket').reindex(labels).reset_index()
        op = 0.65 if selected is None or selected == region else 0.35
        fig.add_trace(go.Bar(
            x=sub['bucket'].astype(str), y=sub['rate'], name=region,
            marker=dict(color=region_color(region), opacity=op,
                        line=dict(color=COLORS['text'], width=2)),
            cliponaxis=False, customdata=sub['count'].fillna(0),
            hovertemplate=(f"<b>{region} · %{{x}} min</b><br>"
                           "Surrender: %{y:.1f}%<br>Matches: %{customdata:,}<extra></extra>")))

    overall = df['surrendered'].mean() * 100.0

    # Заголовок с флагом сдачи
    title = (f"<span style='color:{COLORS['banrate']}'>🏳️</span> "
             f"{gold_title('Surrender Rate by Duration')}")

    fig.update_layout(**PLOTLY_LAYOUT, title=title, height=CHART_HEIGHT, barmode='group',
                      bargap=0.40, bargroupgap=0.264,
                      legend=dict(yanchor='top', y=0.96, xanchor='right', x=0.98,
                                  bgcolor='rgba(26,31,36,0.6)',
                                  bordercolor=COLORS['border'], borderwidth=1,
                                  font=dict(color=COLORS['text'], size=11)))
    # Горизонтальная линия — общий surrender rate
    fig.add_hline(
        y=overall, line_dash='dot', line_color=COLORS['primary'], line_width=2,
        annotation_text=f"OVERALL {overall:.1f}%",
        annotation_position="top right",
        annotation_font=dict(color=COLORS['primary'], size=13, family=FONT_FAMILY))
    fig.update_xaxes(title=centered_axis_title("Duration bucket (min)"), **NO_GRID)
    fig.update_yaxes(title=centered_axis_title("Surrender Rate (%)"),
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False, rangemode='tozero')
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def lp_vs_wr_scatter(players_df, region_key):
    """
    Диаграмма рассеяния: League Points vs Win Rate.
    Размер точки = число сыгранных игр. Цвет = регион.
    """
    df = players_df.dropna(subset=['leaguePoints', 'win_rate']).copy()
    df = df[df['games_played'] > 0]
    if df.empty:
        return dcc.Graph(figure=empty_fig("No LP/WR data"), config=GRAPH_CONFIG)

    order = ordered_regions(df['region'])
    max_games = max(float(df['games_played'].max()), 1.0)

    fig = go.Figure()
    for region in order:
        sub = df[df['region'] == region]
        if sub.empty:
            continue
        # Размер маркера пропорционален корню из числа игр
        sizes = 4 + 7 * (sub['games_played'].clip(lower=1) / max_games) ** 0.5
        custom = sub[['player_name', 'tier', 'games_played']].to_numpy()
        fig.add_trace(go.Scatter(
            x=sub['leaguePoints'], y=sub['win_rate'] * 100.0, mode='markers', name=region,
            customdata=custom,
            marker=dict(size=sizes, color=region_color(region), opacity=0.72,
                        line=dict(color=COLORS['secondary'], width=0.6)),
            hovertemplate=("<b>%{customdata[0]}</b><br>Region: " + region +
                           "<br>Tier: %{customdata[1]}<br>LP: %{x}<br>WR: %{y:.1f}%"
                           "<br>Games: %{customdata[2]}<extra></extra>")))

    fig.update_layout(**PLOTLY_LAYOUT,
                      title=gold_title(f"LP vs Player Win Rate · {region_scope_label(region_key)}"),
                      height=CHART_HEIGHT,
                      legend=dict(yanchor='top', y=0.96, xanchor='right', x=0.98,
                                  bgcolor='rgba(26,31,36,0.6)',
                                  bordercolor=COLORS['border'], borderwidth=1,
                                  font=dict(color=COLORS['text'], size=11)))
    fig.update_xaxes(title=centered_axis_title("League Points"),
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    fig.update_yaxes(title=centered_axis_title("Player WR (%)"), range=[40, 80],
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def tier_distribution_dual_donut(players_df):
    """
    Два кольца (donut): EU сверху, US снизу.
    Распределение игроков по тирам (Master, Grandmaster, Challenger).
    """
    if players_df.empty:
        return dcc.Graph(figure=empty_fig("No player data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    top = players_df[players_df['tier'].isin(TOP_TIERS)]
    if top.empty:
        top = players_df
    tiers = [t for t in TOP_TIERS if t in top['tier'].unique()]
    if not tiers:
        tiers = ordered_tiers(top['tier'])

    # Вертикальные домены: EU сверху, US снизу
    domains = {'EU': dict(y=[0.55, 1.0]), 'US': dict(y=[0.0, 0.45])}

    fig = go.Figure()
    for region in ['EU', 'US']:
        sub = top[top['region'] == region]
        counts = [int((sub['tier'] == t).sum()) for t in tiers]
        if sum(counts) == 0:
            continue
        dom = domains[region]
        fig.add_trace(go.Pie(
            labels=tiers, values=counts, hole=0.58, sort=False, rotation=90,
            domain=dict(x=[0.0, 1.0], y=dom['y']),
            marker=dict(colors=[TIER_COLOR_MAP.get(t, COLORS['text_muted']) for t in tiers],
                        line=dict(color=COLORS['secondary'], width=2)),
            textinfo='percent', textposition='inside',
            insidetextfont=dict(family=FONT_FAMILY, size=12, color=COLORS['secondary']),
            hovertemplate="<b>%{label}</b><br>Players: %{value:,}<br>%{percent}<extra></extra>",
            showlegend=(region == 'EU')))                   # легенду показываем только для EU
        # Подпись региона в центре кольца
        cy = (dom['y'][0] + dom['y'][1]) / 2.0
        fig.add_annotation(xref='paper', yref='paper', x=0.5, y=cy,
                           text=f"<b>{region}</b>", showarrow=False,
                           xanchor='center', yanchor='middle',
                           font=dict(color=region_color(region), size=40, family=FONT_FAMILY))

    fig.update_layout(**PLOTLY_LAYOUT, title=gold_title("Tier Distribution"), height=TALL_HEIGHT,
                      legend=dict(orientation='h', yanchor='middle', y=0.5, xanchor='center', x=0.5,
                                  font=dict(color=COLORS['text'], size=11),
                                  bgcolor='rgba(10,17,20,0.6)',
                                  bordercolor=COLORS['border'], borderwidth=1))
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def lp_by_tier(players_df, region_key):
    """
    LP распределение по тирам — два subplot-а (EU сверху, US снизу).
    Наложенные полупрозрачные гистограммы + KDE-огибающие.
    """
    if players_df.empty:
        return dcc.Graph(figure=empty_fig("No player data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    base = players_df.dropna(subset=['leaguePoints', 'tier'])
    if base.empty:
        return dcc.Graph(figure=empty_fig("No player LP data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    tiers = [t for t in TOP_TIERS if t in base['tier'].unique()]
    if not tiers:
        return dcc.Graph(figure=empty_fig("No tier data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    # Регионы: EU сверху, US снизу
    regions = [r for r in REGION_PREF_ORDER if r in base['region'].unique().tolist()]
    if not regions:
        regions = ordered_regions(base['region'])[:2]
    if not regions:
        return dcc.Graph(figure=empty_fig("No region data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    # Единые параметры бинов для обоих subplot-ов
    lp_all = base['leaguePoints']
    lo, hi = float(lp_all.min()), float(lp_all.max())
    span = max(hi - lo, 1.0)
    bin_size = max(span / 60.0, 1.0)

    selected = None if region_key == 'ALL' else str(region_key).upper()
    n = len(regions)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.10)

    for i, region in enumerate(regions, start=1):
        reg = base[base['region'] == region]
        dim = (selected is not None and selected != region)

        for tier in tiers:
            lp = reg.loc[reg['tier'] == tier, 'leaguePoints'].values
            if len(lp) == 0:
                continue
            color = TIER_COLOR_MAP.get(tier, COLORS['text_muted'])
            line_op = 0.4 if dim else 1.0

            # Гистограмма
            fig.add_trace(go.Histogram(
                x=lp, xbins=dict(start=lo, end=hi + bin_size, size=bin_size),
                name=tier, legendgroup=tier, showlegend=False,
                marker=dict(color=color, opacity=0.3, line=dict(color=color, width=0.5)),
                hovertemplate=f"<b>{region} · {tier}</b><br>LP %{{x}}<br>Players: %{{y}}<extra></extra>"),
                row=i, col=1)

            # KDE-огибающая
            if len(lp) >= 2 and np.std(lp) > 0:
                try:
                    from scipy.stats import gaussian_kde
                    kde = gaussian_kde(lp, bw_method=0.35)
                    xs = np.linspace(lo, hi, 400)
                    ys = kde(xs) * len(lp) * bin_size
                    fig.add_trace(go.Scatter(
                        x=xs, y=ys, mode='lines', name=f"{tier} (curve)",
                        legendgroup=tier, showlegend=False, opacity=line_op,
                        line=dict(color=color, width=2, shape='spline', dash='dot'),
                        hoverinfo='skip'),
                        row=i, col=1)
                except Exception:
                    pass

        # Подпись региона в углу subplot-а
        fig.add_annotation(
            xref=f"x{i if i > 1 else ''} domain", yref=f"y{i if i > 1 else ''} domain",
            x=0.98, y=0.92, text=f"<b>{region}</b>", showarrow=False,
            xanchor='right', yanchor='top',
            font=dict(color=region_color(region), size=34, family=FONT_FAMILY),
            opacity=0.4 if dim else 1.0)

    fig.update_layout(**PLOTLY_LAYOUT,
                      title=f"LP by Tier · {region_scope_label(region_key)}",
                      height=TALL_HEIGHT, barmode='overlay', bargap=0.3,
                      legend=dict(x=0.98, y=0.99, xanchor='right', yanchor='top',
                                  bgcolor='rgba(26,31,36,0.6)',
                                  bordercolor=COLORS['border'], borderwidth=1,
                                  font=dict(color=COLORS['text'], size=11)))
    fig.update_xaxes(showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=COLORS['grid'], zeroline=False,
                     title=centered_axis_title("Players"))
    fig.update_xaxes(title=centered_axis_title("League Points"), row=n, col=1)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


def player_wr_by_region(players_df, selected_region='ALL'):
    """
    Violin-график распределения винрейта игроков по регионам.
    EU и US — всегда первые, с крупными подписями.
    """
    df = players_df.dropna(subset=['win_rate']).copy()
    df = df[df['games_played'] > 0]
    if df.empty:
        return dcc.Graph(figure=empty_fig("No player WR data", height=TALL_HEIGHT), config=GRAPH_CONFIG)

    df['win_rate_pct'] = df['win_rate'] * 100.0

    # Порядок: US, EU, потом остальные
    forced = ['US', 'EU']
    present = df['region'].unique().tolist()
    order = [r for r in forced if r in present] + \
            [r for r in ordered_regions(present) if r not in forced]

    fig = go.Figure()
    for region in order:
        sub = df[df['region'] == region]
        if sub.empty:
            continue
        op = 1.0 if selected_region == 'ALL' or region == selected_region else 0.35
        fig.add_trace(go.Violin(
            x=sub['region'], y=sub['win_rate_pct'], name=region,
            line_color=region_color(region), fillcolor=region_color(region),
            opacity=op, box_visible=True, meanline_visible=True, points=False,
            hovertemplate="<b>%{x}</b><br>WR: %{y:.1f}%<extra></extra>", showlegend=False))

    # Крупные подписи для EU и US
    for region in order:
        if region in forced:
            fig.add_annotation(
                xref='x', yref='paper', x=region, y=0.97, text=f"<b>{region}</b>",
                showarrow=False, xanchor='center', yanchor='top',
                font=dict(color=region_color(region), size=40, family=FONT_FAMILY),
                opacity=1.0 if selected_region == 'ALL' or region == selected_region else 0.4)

    fig.update_layout(**PLOTLY_LAYOUT, title=gold_title("Player Win Rate by Region"),
                      height=TALL_HEIGHT)
    fig.update_xaxes(categoryorder='array', categoryarray=order,
                     title=centered_axis_title("Region"), **NO_GRID)
    fig.update_yaxes(title=centered_axis_title("Player WR (%)"), range=[40, 80],
                     showgrid=True, gridcolor=COLORS['grid'], zeroline=False)
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG)


# ============================================================
# 4. LAYOUT — вёрстка страницы
# ============================================================

# ── ★ Глобальный CSS из common ──
from common import GLOBAL_CSS

def row3(*panels):
    """Сетка из 3 колонок для графиков."""
    return html.Div(list(panels),
                    style={'display': 'grid', 'gridTemplateColumns': 'repeat(3, 1fr)',
                           'gap': '20px', 'marginBottom': '20px', 'alignItems': 'stretch'})


def serve_layout():
    """Возвращает полный layout страницы Match Overview."""
    return html.Div([
        # ── Шапка с заголовком и селектором региона ──
        html.Div([
            html.H1("MATCH OVERVIEW", style=PAGE_TITLE_STYLE),
            dcc.RadioItems(
                id='region-selector', options=REGION_OPTIONS, value='ALL', inline=True,
                style=REGION_SELECTOR_STYLE, labelStyle=REGION_LABEL_STYLE,
                inputStyle=REGION_INPUT_STYLE),
            # Описание страницы
            html.P(
                "Analyze match dynamics across EU & US servers through KPI summaries, "
                "daily upload and duration trends, blue vs red side win rates, and an "
                "activity heatmap by weekday and hour. Explore match structure with "
                "duration distributions, region comparisons, and surrender rates by game "
                "length, then dive into the competitive ladder via LP vs win rate, tier "
                "distribution, and player performance breakdowns. Filter the entire "
                "dashboard by region.",
                style={'textAlign': 'left', 'color': COLORS['text_muted'],
                       'fontSize': f"{FS['sm']}px", 'lineHeight': '1.6',
                       'fontStyle': 'italic',
                       'width': '100%', 'margin': '10px 0 0 0'}),
            # Примечание про LP/WR
            html.P(
                "LP / WR charts use the current players snapshot; "
                "region comparison charts keep all regions visible.",
                style={'textAlign': 'left', 'color': COLORS['text_muted'],
                       'fontSize': f"{FS['xs']}px", 'fontStyle': 'italic',
                       'width': '100%', 'margin': '6px 0 0 0'}),
        ], style={'textAlign': 'center', 'padding': '34px 0 22px 0',
                  'borderBottom': f"1px solid {COLORS['primary']}",
                  'background': f"linear-gradient(180deg, {rgba(COLORS['primary'], 0.04)} 0%, rgba(0,0,0,0) 100%)",
                  'marginBottom': '20px'}),

        # ════════ БЛОК 1: SERVER PULSE ════════
        section_title("SERVER PULSE · matches, sides & activity"),
        row3(
            chart_panel(html.Div(id='matches-donut-panel'), 'matches-donut-panel'),
            chart_panel(html.Div(id='side-gauge-panel'), 'side-gauge-panel'),
            chart_panel(html.Div(id='heatmap-panel'), 'heatmap-panel'),
        ),
        row3(
            chart_panel(html.Div(id='matches-trend-panel'), 'matches-trend-panel'),
            chart_panel(html.Div(id='side-trend-panel'), 'side-trend-panel'),
            chart_panel(html.Div(id='histogram-panel'), 'histogram-panel'),
        ),

        # ════════ БЛОК 2: MATCH STRUCTURE ════════
        section_title("MATCH STRUCTURE · duration, cancels & ladder edge"),
        row3(
            chart_panel(html.Div(id='duration-box-region-panel'), 'duration-box-region-panel'),
            chart_panel(html.Div(id='cancel-rate-panel'), 'cancel-rate-panel'),
            chart_panel(html.Div(id='lp-wr-scatter-panel'), 'lp-wr-scatter-panel'),
        ),

        # ════════ БЛОК 3: THE LADDER ════════
        section_title("THE LADDER · league points & player standing"),
        row3(
            chart_panel(html.Div(id='tier-distribution-panel'), 'tier-distribution-panel'),
            chart_panel(html.Div(id='player-lp-tier-panel'), 'player-lp-tier-panel'),
            chart_panel(html.Div(id='player-wr-region-panel'), 'player-wr-region-panel'),
        ),

    ], style=APP_STYLE)


# ============================================================
# 5. CALLBACK — обновление всех графиков при смене региона
# ============================================================

@callback(
    # Блок 1 — ряд 1
    Output('matches-donut-panel', 'children'),
    Output('side-gauge-panel', 'children'),
    Output('heatmap-panel', 'children'),
    # Блок 1 — ряд 2
    Output('matches-trend-panel', 'children'),
    Output('side-trend-panel', 'children'),
    Output('histogram-panel', 'children'),
    # Блок 2
    Output('duration-box-region-panel', 'children'),
    Output('cancel-rate-panel', 'children'),
    Output('lp-wr-scatter-panel', 'children'),
    # Блок 3
    Output('tier-distribution-panel', 'children'),
    Output('player-lp-tier-panel', 'children'),
    Output('player-wr-region-panel', 'children'),
    Input('region-selector', 'value'),
)
def update_dashboard(region_key):
    """Единый callback для обновления всей страницы при смене региона."""
    raw_region = filter_region(RAW, region_key) if not RAW.empty else RAW
    df = raw_region[~raw_region['is_remake']] if not raw_region.empty else raw_region
    players_region = filter_region(PLAYERS, region_key) if not PLAYERS.empty else PLAYERS
    matches_no_remake = RAW[~RAW['is_remake']] if not RAW.empty else RAW

    return (
        # Блок 1 — ряд 1
        matches_kpi(df, region_key),
        side_winrate_gauge(region_key),
        weekday_hour_heatmap(df),
        # Блок 1 — ряд 2
        matches_duration_trend(df),
        side_winrate_trend(filter_region(SIDES, region_key)),
        duration_histogram(df),
        # Блок 2
        match_duration_boxplot_by_region(matches_no_remake, region_key),
        surrender_rate_by_duration(CANCEL, region_key),
        lp_vs_wr_scatter(players_region, region_key),
        # Блок 3
        tier_distribution_dual_donut(PLAYERS),
        lp_by_tier(players_region, region_key),
        player_wr_by_region(PLAYERS, region_key),
    )


# Чтобы страницу можно было импортировать в app.py с вкладками
layout = serve_layout()
# Greetings