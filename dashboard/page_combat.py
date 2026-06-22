# %load page_combat.py
# ============================================================
# DASHBOARD PAGE 3 — Blood & Objectives (Combat Analytics)
# ============================================================
# Использует: common.py — все общие константы, стили, функции
# Показывает: агрессия (kills, damage), interplay (KDA, winners edge),
#             map control (objectives, vision), сравнение регионов
# ============================================================

# ── Добавляем корень проекта в путь для импорта common ──
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Импорт библиотек ──
import json                              # для распаковки бинов гистограмм из JSON
import time                              # замер времени загрузки
import dash                              # сам Dash
from dash import dcc, html, Input, Output, callback  # компоненты и callback-инструменты
import pandas as pd                      # работа с данными (DataFrame)
import numpy as np                       # числовые операции
import plotly.graph_objects as go        # низкоуровневые графики Plotly

# ── ★ Импорт всего общего из common.py ──
from common import (
    # Конфигурация
    COLORS, FONT_FAMILY, FS, DB_PATH, QUEUE_ID, REMAKE_SEC,
    REGION_OPTIONS, REGION_PREF_ORDER, REGION_KEYS,
    CHART_INFO, AUTHOR,
    # Алиасы цветов
    LOL_GOLD, LOL_GREEN, LOL_DARK, LOL_TEXT, C,
    # Утилиты
    rgba, get_connection, ordered_regions, filter_region, region_color,
    empty_fig, chart_panel, build_footer,
    apply_grid, GRID_FULL, GRID_NONE,
    # Стили
    APP_STYLE_DARK as APP_STYLE,         # тёмный стиль для этой страницы
    CONTAINER, HEADER_STYLE, PAGE_TITLE_STYLE,
    ZONE_HEADER_ROW, ROW3,               # зональная вёрстка (3 колонки)
    PANEL_BLOOD, PANEL_GOLD, PANEL_TEAL, # панели с цветными акцентами
    LAYOUT, TITLE_BASE,                  # базовый layout для Plotly
    # Размеры
    GRAPH_H, TALL_H,
    # Plotly
    CFG,                                 # конфиг отображения графиков
)

# ============================================================
# 1. КОНФИГУРАЦИЯ (специфичная для этой страницы)
# ============================================================

# ── Схема в DuckDB, откуда читаем витрины ──
COMBAT_SCHEMA = "lol_combat"

# ── Максимальный размер выборки для KDA-violin (ограничиваем KDE) ──
VIOLIN_SAMPLE = 6000

# ── Дата начала анализируемого периода ──
START_DATE = '2026-05-01'

# ── Стиль подзаголовка ──
SUB_STYLE = {
    'color': COLORS['muted'], 'fontSize': f"{FS['sm']}px", 'letterSpacing': '3px',
    'marginTop': '8px', 'textTransform': 'uppercase',
}


# ============================================================
# 2. УТИЛИТЫ (специфичные для этой страницы)
# ============================================================

def titled(text):
    """Заголовок графика в верхнем регистре."""
    return {**TITLE_BASE, 'text': str(text).upper()}


def zone_label(text, color, align):
    """
    Заголовок зоны (Aggression / Interplay / Map Control).
    С цветной полоской-градиентом под текстом.
    """
    gradient = (
        f"linear-gradient(90deg, {rgba(color, 0.6)}, rgba(0,0,0,0))" if align == 'left' else
        f"linear-gradient(270deg, {rgba(color, 0.6)}, rgba(0,0,0,0))" if align == 'right' else
        f"linear-gradient(90deg, rgba(0,0,0,0), {rgba(color, 0.6)}, rgba(0,0,0,0))"
    )
    return html.Div([
        html.Span(text, style={
            'color': color, 'fontSize': f"{FS['lg']}px", 'letterSpacing': '3px',
            'textTransform': 'uppercase', 'fontWeight': '600',
        }),
        html.Div(style={'height': '2px', 'marginTop': '6px', 'background': gradient}),
    ], style={'textAlign': 'center'})


# ============================================================
# 3. ЗАГРУЗКА ДАННЫХ — чтение витрин из DuckDB
# ============================================================

print("⏳ Loading data from DuckDB...")
_t0 = time.perf_counter()

# ── Подключаемся к DuckDB через общую функцию ──
conn = get_connection()

# ── ★ Основные витрины ──
MATCH      = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_match").df()
TEAMS      = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_team").df()
PLAYER_AGG = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_player_agg").df()

# ── ★ Бины гистограмм (JSON-строка с counts) ──
HIST_DPM_RAW = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_hist_dpm").df()
HIST_VPM_RAW = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_hist_vpm").df()

# ── ★ Семплы для графиков ──
DENSITY_DF = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_density_2d").df()
VIOLIN_DF  = conn.execute(f"SELECT * FROM {COMBAT_SCHEMA}.combat_violin_kda").df()

conn.close()

EMPTY = MATCH.empty

# ────────────────────────────────────────────────────────────
# Пост-обработка: MATCH (уровень матча)
# ────────────────────────────────────────────────────────────
if not EMPTY:
    MATCH['kpm'] = MATCH['kills'] / MATCH['dur']                              # kills per minute
    MATCH['obj'] = MATCH[['dragons', 'barons', 'towers', 'inhibitors']].sum(axis=1)  # всего объектов
    MATCH['opm'] = MATCH['obj'] / MATCH['dur']                                 # objectives per minute
    MATCH['oci'] = (MATCH['dragons'] + 2 * MATCH['barons']                     # Objective Control Index
                    + 0.5 * MATCH['towers'] + MATCH['inhibitors'])
    MATCH['date'] = pd.to_datetime(MATCH['match_date'])                        # дата для трендов

# ────────────────────────────────────────────────────────────
# Пост-обработка: TEAMS (уровень команды)
# ────────────────────────────────────────────────────────────
if not TEAMS.empty:
    TEAMS['obj'] = TEAMS[['dragons', 'barons', 'towers', 'inhibitors']].sum(axis=1)
    TEAMS['opm'] = TEAMS['obj'] / TEAMS['dur'].clip(lower=1)


def _unpack_hist(raw_df):
    """
    Распаковка бинов гистограмм из JSON.
    Превращает витрину в dict[region_key] → dict(centers, counts, median, avg, width).
    """
    out = {}
    if raw_df.empty:
        return out
    for _, row in raw_df.iterrows():
        rk = row['region_key']
        nbins = int(row['nbins'])
        counts = json.loads(row['counts_json'])
        # Центры бинов: от lo + width/2 до hi - width/2
        centers = np.linspace(
            float(row['lo']) + float(row['width']) / 2,
            float(row['hi']) - float(row['width']) / 2,
            nbins
        )
        out[rk] = dict(
            centers=centers, counts=np.array(counts),
            median=float(row['median']), avg=float(row['avg']),
            width=float(row['width']),
        )
    return out


# ── Распаковываем бины ──
HIST_DMG = _unpack_hist(HIST_DPM_RAW)   # бины Damage/min
HIST_VPM = _unpack_hist(HIST_VPM_RAW)   # бины Vision/min

# ────────────────────────────────────────────────────────────
# Пост-обработка: DENSITY (2D-плотность dpm vs kp)
# ────────────────────────────────────────────────────────────
if not DENSITY_DF.empty:
    # Считаем damage per minute и kill participation
    DENSITY_DF['dpm'] = DENSITY_DF['dmg'] / DENSITY_DF['dur']
    team_k = DENSITY_DF.groupby(['match_id', 'team_id'])['kills'].transform('sum').clip(lower=1)
    DENSITY_DF['kp'] = ((DENSITY_DF['kills'] + DENSITY_DF['assists']) / team_k).clip(upper=1) * 100
    # Обрезаем выбросы по 99-му перцентилю
    hi = float(np.nanpercentile(DENSITY_DF['dpm'], 99))
    DENSITY_DF = DENSITY_DF[DENSITY_DF['dpm'] <= hi]

# Оставляем только нужные колонки (dpm, kp, и region если есть)
if not DENSITY_DF.empty:
    _dens_cols = ['dpm', 'kp'] + (['region'] if 'region' in DENSITY_DF.columns else [])
    DENSITY_DF = DENSITY_DF[_dens_cols].reset_index(drop=True)
else:
    DENSITY_DF = pd.DataFrame()

# ── Для совместимости с компонентами (используют filter_region) ──
DENSITY = DENSITY_DF
VIOLIN = VIOLIN_DF

print(f"✅ Loaded in {time.perf_counter() - _t0:.2f}s | "
      f"{len(MATCH)} matches | {len(TEAMS)} teams")


# ============================================================
# 4. УТИЛИТЫ ДЛЯ ГРАФИКОВ
# ============================================================

def agg_val(region_key, win, col):
    """
    Достать среднее метрики игрока из PLAYER_AGG.
    Фильтрует по региону и исходу (win=True/False).
    """
    if PLAYER_AGG.empty:
        return 0.0
    df = PLAYER_AGG if region_key == 'ALL' else PLAYER_AGG[PLAYER_AGG['region'] == region_key]
    if region_key == 'ALL':
        df = df[df['win'] == win]
        return float(df[col].mean()) if not df.empty else 0.0
    df = df[df['win'] == win]
    return float(df[col].iloc[0]) if not df.empty else 0.0


def agg_overall(region_key, col):
    """
    Среднее метрики по всем исходам (без разбивки win/lose).
    Используется для KPI и сравнения регионов.
    """
    if PLAYER_AGG.empty:
        return 0.0
    df = PLAYER_AGG if region_key == 'ALL' else PLAYER_AGG[PLAYER_AGG['region'] == region_key]
    if df.empty:
        return 0.0
    return float(df[col].mean())


# ============================================================
# 5. KPI HERO TILES — верхняя строка с ключевыми метриками
# ============================================================

def kpi_tile(label, value, accent, icon):
    """
    Одна плитка KPI.
    Иконка, крупное значение, подпись, цветной акцент.
    """
    return html.Div([
        html.Div(icon, style={'fontSize': f"{FS['xl']}px", 'marginBottom': '6px', 'opacity': '0.9'}),
        html.Div(value, style={'fontSize': '40px', 'fontWeight': '700', 'color': accent,
                               'lineHeight': '1', 'textShadow': f"0 0 18px {rgba(accent, 0.4)}"}),
        html.Div(label, style={'fontSize': f"{FS['md']}px", 'color': COLORS['muted'],
                               'letterSpacing': '1.5px', 'marginTop': '8px',
                               'textTransform': 'uppercase'}),
    ], style={
        'background': f"linear-gradient(180deg, {COLORS['panel']}, rgba(0,0,0,0.25))",
        'borderRadius': '16px', 'border': f"1px solid {accent}",
        'borderTop': f"2px solid {accent}",
        'padding': '18px 10px', 'textAlign': 'center', 'flex': '1',
        'boxShadow': f"0 6px 20px rgba(0,0,0,0.4), 0 0 18px {rgba(accent, 0.10)}",
    })


def kpi_row(region_key, m, t):
    """
    Строка из 6 KPI-плиток.
    Aggression: Avg Kills/Game, Kills/Min
    Interplay: Avg Damage/Min, Avg KDA
    Map Control: Objectives/Game, Vision Score/Min
    """
    if m.empty:
        return html.Div()

    avg_dpm = agg_overall(region_key, 'dpm')
    avg_kda = agg_overall(region_key, 'kda')
    avg_vpm = agg_overall(region_key, 'vpm')

    tiles = [
        kpi_tile("Avg Kills / Game", f"{m['kills'].mean():.1f}", COLORS['blood'], "⚔️"),
        kpi_tile("Kills / Min",      f"{m['kpm'].mean():.2f}",   COLORS['blood'], "🩸"),
        kpi_tile("Avg Damage / Min", f"{avg_dpm:.0f}",           COLORS['gold'],  "🔥"),
        kpi_tile("Avg KDA",          f"{avg_kda:.2f}",           COLORS['gold'],  "📊"),
        kpi_tile("Objectives / Game", f"{m['obj'].mean():.1f}",  COLORS['teal'],  "🐉"),
        kpi_tile("Vision Score / Min", f"{avg_vpm:.2f}",         COLORS['teal'],  "👁️"),
    ]
    return html.Div(tiles, style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'})


# ============================================================
# 6. CHARTS — все графики
# ============================================================

def _hist_fig(hist, *, color, title, xlab, h=GRAPH_H, fmt="{:.1f}"):
    """
    Единая гистограмма из предрассчитанных бинов.
    Рисует go.Bar (не go.Histogram) для максимальной производительности.
    """
    if hist is None:
        return empty_fig(height=h)

    centers, counts = hist['centers'], hist['counts']
    median, avg, width = hist['median'], hist['avg'], hist['width']

    fig = go.Figure(go.Bar(
        x=centers, y=counts, width=width,
        marker=dict(color=rgba(color, 0.85), line=dict(width=0)),
        opacity=0.9,
        hovertemplate="<b>%{x:.1f}</b><br>Count: %{y}<extra></extra>"))

    # Вертикальные линии медианы и среднего
    fig.add_vline(x=median, line_dash="dash", line_color=COLORS['blood'], line_width=2,
                  annotation_text=f"Median {fmt.format(median)}",
                  annotation_position="top left", annotation_font_color=COLORS['blood'])
    fig.add_vline(x=avg, line_dash="dot", line_color=COLORS['gold'], line_width=2,
                  annotation_text=f"Avg {fmt.format(avg)}",
                  annotation_position="top right", annotation_font_color=COLORS['gold'])

    fig.update_layout(**LAYOUT, title=titled(title), bargap=0.05, height=h)
    apply_grid(fig, xlab=xlab, ylab="count")
    return fig


def _hist_from_series(series, *, color, title, xlab, h=GRAPH_H, fmt="{:.1f}", nbins=40):
    """
    Гистограмма из сырых данных (для kills, obj).
    Использует np.histogram на лёгком массиве MATCH.
    """
    s = series.dropna().to_numpy()
    if s.size == 0:
        return empty_fig(height=h)

    lo, hi = float(s.min()), float(s.max())
    if hi <= lo:
        hi = lo + 1
    counts, edges = np.histogram(s, bins=nbins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    hist = dict(centers=centers, counts=counts,
                median=float(np.median(s)), avg=float(np.mean(s)),
                width=edges[1] - edges[0])
    return _hist_fig(hist, color=color, title=title, xlab=xlab, h=h, fmt=fmt)


# ── 6.1 TOTAL KILLS PER GAME ──
def kills_distribution(m):
    """Гистограмма: распределение общего числа kills за матч."""
    if m.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    fig = _hist_from_series(m['kills'], color=COLORS['blood'], nbins=60,
                            title="Total Kills per Game", xlab="kills", fmt="{:.0f}")
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.2 OBJECTIVES PER GAME ──
def objectives_distribution(m):
    """Гистограмма: распределение числа objectives за матч."""
    if m.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    fig = _hist_from_series(m['obj'], color=COLORS['teal'], nbins=40,
                            title="Objectives per Game",
                            xlab="dragons + barons + towers + inhibs", fmt="{:.0f}")
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.3 DAMAGE PER MINUTE (из предрассчитанных бинов) ──
def damage_distribution(region_key):
    """Гистограмма: Damage per Minute (из витрины бинов)."""
    fig = _hist_fig(HIST_DMG.get(region_key), color=COLORS['blood'],
                    title="Damage per Minute", xlab="damage to champs / min", fmt="{:.0f}")
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.4 VISION SCORE / MIN (из предрассчитанных бинов) ──
def vision_distribution(region_key):
    """Гистограмма: Vision Score per Minute (из витрины бинов)."""
    fig = _hist_fig(HIST_VPM.get(region_key), color=COLORS['teal'],
                    title="Vision Score per Minute", xlab="vision score / min", fmt="{:.2f}")
    if fig is not None and getattr(fig, 'data', None):
        fig.add_annotation(xref='paper', yref='paper', x=0.5, y=-0.18,
                           text="map awareness · wards placed & cleared",
                           showarrow=False, font=dict(color=COLORS['muted'], size=10),
                           xanchor='center')
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.5 KILLS vs DURATION (scatter, выборка из MATCH) ──
def kills_vs_duration(m):
    """
    Диаграмма рассеяния: Kills vs Game Length.
    Цвет точки = kills per minute (KPM). Выборка до 2500 матчей.
    """
    if m.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    s = m.sample(min(len(m), 2500), random_state=1)
    fig = go.Figure(go.Scattergl(
        x=s['dur'], y=s['kills'], mode='markers',
        marker=dict(size=5, color=s['kpm'],
                    colorscale=[[0, COLORS['gold']], [1, COLORS['blood']]],
                    showscale=True, opacity=0.18, line=dict(width=0),
                    colorbar=dict(title=dict(text="k/min", font=dict(color=COLORS['muted'], size=10)),
                                  tickfont=dict(color=COLORS['muted'], size=9),
                                  thickness=10, outlinewidth=0, len=0.8)),
        hovertemplate="<b>%{y} kills</b> · %{x:.0f} min<extra></extra>"))
    fig.update_layout(**LAYOUT, title=titled("Kills vs Game Length"), height=GRAPH_H)
    apply_grid(fig, xlab="duration (min)", ylab="total kills")
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.6 DAMAGE vs KILL PARTICIPATION (Histogram2d) ──
def damage_vs_kda(region_key):
    """
    2D-гистограмма: Damage/min vs Kill Participation %.
    Показывает плотность игроков в пространстве dpm × kp.
    """
    df = filter_region(DENSITY, region_key)
    if df is None or df.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    fig = go.Figure(go.Histogram2d(
        x=df['dpm'], y=df['kp'], nbinsx=80, nbinsy=55,
        colorscale=[[0, 'rgba(0,0,0,0)'], [0.3, rgba(COLORS['blood'], 0.35)],
                    [0.7, COLORS['blood']], [1, COLORS['gold']]],
        colorbar=dict(title=dict(text="players", font=dict(color=COLORS['muted'], size=10)),
                      tickfont=dict(color=COLORS['muted'], size=9),
                      thickness=10, outlinewidth=0, len=0.8),
        hovertemplate="%{x:.0f} dmg/min · %{y:.0f}% KP<br>%{z} players<extra></extra>"))
    fig.update_layout(**LAYOUT, title=titled("Damage vs Kill Participation"),
                      height=GRAPH_H, showlegend=False)
    apply_grid(fig, xlab="damage / min", ylab="kill participation %",
               x_extra={'layer': 'above traces'}, y_extra={'layer': 'above traces'})
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.7 KDA: WINNERS vs LOSERS (violin по выборке) ──
def kda_winners_vs_losers(region_key):
    """
    Violin-график KDA: сравнение победителей и проигравших.
    С аннотациями средних значений и разницы.
    """
    df = filter_region(VIOLIN, region_key)
    if df is None or df.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)

    # Обрезаем выбросы по 99-му перцентилю
    hi = float(np.nanpercentile(df['kda'], 99))
    df = df[df['kda'] <= hi].copy()

    win_s = df[df['win']]['kda']        # KDA победителей
    los_s = df[~df['win']]['kda']       # KDA проигравших
    winner_kda = win_s.mean() if not win_s.empty else 0
    loser_kda = los_s.mean() if not los_s.empty else 0
    diff = winner_kda - loser_kda

    # Ограничиваем размер для KDE
    win_s = win_s.sample(min(len(win_s), VIOLIN_SAMPLE), random_state=1)
    los_s = los_s.sample(min(len(los_s), VIOLIN_SAMPLE), random_state=1)

    VIOLIN_COLOR = COLORS['blood']
    fig = go.Figure()
    fig.add_trace(go.Violin(
        x=['🏆 Winners'] * len(win_s), y=win_s, name='🏆 Winners',
        line_color=VIOLIN_COLOR, fillcolor=rgba(VIOLIN_COLOR, 0.20),
        box_visible=True, meanline_visible=True, points=False, opacity=0.85,
        hovertemplate="<b>Winners</b><br>KDA: %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Violin(
        x=['💀 Losers'] * len(los_s), y=los_s, name='💀 Losers',
        line_color=VIOLIN_COLOR, fillcolor=rgba(VIOLIN_COLOR, 0.20),
        box_visible=True, meanline_visible=True, points=False, opacity=0.85,
        hovertemplate="<b>Losers</b><br>KDA: %{y:.2f}<extra></extra>"))

    # Аннотации
    fig.add_annotation(x=0.05, y=0.98, xref='paper', yref='paper',
                       text=f"<b>Winners</b> avg KDA: <span style='color:{VIOLIN_COLOR}'>{winner_kda:.2f}</span>",
                       showarrow=False, font=dict(size=12, family=FONT_FAMILY), align='left')
    fig.add_annotation(x=0.95, y=0.98, xref='paper', yref='paper',
                       text=f"<b>Losers</b> avg KDA: <span style='color:{VIOLIN_COLOR}'>{loser_kda:.2f}</span>",
                       showarrow=False, font=dict(size=12, family=FONT_FAMILY), align='right')
    fig.add_annotation(x=0.5, y=0.55, xref='paper', yref='paper',
                       text=f"⚡ <b>+{diff:.2f}</b> KDA<br>"
                            f"<span style='font-size:10px;color:{COLORS['muted']}'>winners over losers</span>",
                       showarrow=False, font=dict(color=COLORS['gold'], size=16, family=FONT_FAMILY),
                       align='center')

    fig.update_layout(**LAYOUT, title=titled("KDA: Winners vs Losers"),
                      height=GRAPH_H, showlegend=False)
    fig.update_xaxes(ticktext=['🏆 Winners', '💀 Losers'],
                     tickvals=['🏆 Winners', '💀 Losers'],
                     showgrid=True, gridcolor=rgba(COLORS['gold'], 0.10),
                     zeroline=False, linecolor=COLORS['border'])
    fig.update_yaxes(title=dict(text="KDA (K+A)/D", font=dict(color=COLORS['muted'], size=11, family=FONT_FAMILY)),
                     showgrid=True, gridcolor=rgba(COLORS['gold'], 0.10),
                     zeroline=False, linecolor=COLORS['border'], range=[0, None])
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.8 AGGRESSION vs CONTROL (dual-axis line) ──
def meta_pulse(m):
    """
    Совмещённый график с двумя осями Y:
    Aggression (Kills/min) — красная ось,
    Control (Objectives/min) — бирюзовая ось.
    Пунктир = сырые данные, жирная линия = 14-дневный тренд.
    """
    if m.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)

    # Дневная агрегация
    daily = (m.groupby('date').agg(kpm=('kpm', 'mean'), opm=('opm', 'mean'))
             .reset_index().sort_values('date'))
    daily['kpm_trend'] = daily['kpm'].rolling(window=14, min_periods=1).mean()
    daily['opm_trend'] = daily['opm'].rolling(window=14, min_periods=1).mean()

    fig = go.Figure()

    # Сырые данные (пунктир)
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['kpm'], mode='lines', name='⚔️ Kills / Min',
        line=dict(color=rgba(COLORS['blood'], 0.4), width=1.5, shape='spline', dash='dot'),
        yaxis='y', hovertemplate="<b>%{x|%d %b}</b><br>%{y:.2f} k/min<extra></extra>",
        showlegend=False))
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['opm'], mode='lines', name='🐉 Obj / Min',
        line=dict(color=rgba(COLORS['teal'], 0.4), width=1.5, shape='spline', dash='dot'),
        yaxis='y2', hovertemplate="<b>%{x|%d %b}</b><br>%{y:.2f} obj/min<extra></extra>",
        showlegend=False))

    # Тренды (жирные линии)
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['kpm_trend'], mode='lines', name='⚔️ Aggr',
        line=dict(color=COLORS['blood'], width=3, shape='spline'), yaxis='y',
        hovertemplate="<b>%{x|%d %b}</b><br>Trend: %{y:.2f} k/min<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=daily['date'], y=daily['opm_trend'], mode='lines', name='🐉 Control',
        line=dict(color=COLORS['teal'], width=3, shape='spline'), yaxis='y2',
        hovertemplate="<b>%{x|%d %b}</b><br>Trend: %{y:.2f} obj/min<extra></extra>"))

    # Отметки границ патчей
    if 'game_version' in m.columns:
        patches = (m.dropna(subset=['game_version'])
                   .assign(patch=m['game_version'].astype(str).str.extract(r'^(\d+\.\d+)')[0])
                   .dropna(subset=['patch'])
                   .groupby('patch')['date'].min().reset_index().sort_values('date'))
        for _, prow in patches.iloc[1:].iterrows():
            fig.add_shape(type='line', x0=prow['date'], x1=prow['date'],
                          yref='paper', y0=0, y1=1,
                          line=dict(color=COLORS['gold'], width=1, dash='dot'))
            fig.add_annotation(x=prow['date'], y=1.0, yref='paper',
                               text=f"v{prow['patch']}", showarrow=False,
                               xanchor='left', yanchor='bottom',
                               font=dict(color=COLORS['gold'], size=10, family=FONT_FAMILY))
        # Элемент легенды для патчей
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode='lines',
            line=dict(color=COLORS['gold'], width=1, dash='dot'),
            name='Patch update', showlegend=True, hoverinfo='skip'))

    fig.update_layout(
        **LAYOUT, title=titled("Aggression vs Control — with 14‑day trend"), height=GRAPH_H,
        legend=dict(orientation='h', yanchor='bottom', y=-0.35, xanchor='center',
                    x=0.5, font=dict(color=COLORS['text'], size=11)),
        yaxis=dict(title=dict(text="k/min", font=dict(color=COLORS['blood'], size=11)),
                   showgrid=True, gridcolor=rgba(COLORS['gold'], 0.10), gridwidth=1,
                   zeroline=False, tickfont=dict(color=COLORS['blood'], size=10)),
        yaxis2=dict(title=dict(text="obj/min", font=dict(color=COLORS['teal'], size=11)),
                    overlaying='y', side='right', showgrid=False, zeroline=False,
                    tickfont=dict(color=COLORS['teal'], size=10)))
    fig.update_xaxes(**GRID_FULL)
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.9 FIRST BLOOD GAUGE ──
def fb_gauge(t):
    """
    Gauge: процент побед после First Blood.
    Стрелка показывает win rate команды, взявшей первую кровь.
    """
    if t.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    fb = t[t['first_blood'] == 1]
    wr = fb['win'].mean() * 100 if not fb.empty else 0
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=wr,
        number=dict(suffix="%", font=dict(color=COLORS['blood_lt'], size=40)),
        gauge=dict(
            axis=dict(range=[0, 100], tickcolor=COLORS['muted'],
                      tickfont=dict(color=COLORS['muted'], size=9)),
            bar=dict(color=COLORS['blood'], thickness=0.28),
            bgcolor='rgba(0,0,0,0)', borderwidth=0,
            steps=[dict(range=[0, 50], color=rgba(COLORS['blood'], 0.18)),
                   dict(range=[50, 100], color=rgba(COLORS['teal'], 0.18))],
            threshold=dict(line=dict(color=COLORS['text'], width=2), thickness=0.8, value=50))))
    fig.update_layout(**LAYOUT, title=titled("🩸 First Blood → Win"), height=GRAPH_H)
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.10 VISION ADVANTAGE GAUGE ──
def vision_gauge(region_key):
    """
    Gauge: насколько лучше vision у победителей по сравнению с проигравшими.
    Положительное значение = победители имеют лучший обзор.
    """
    wv = agg_val(region_key, True, 'vpm')
    lv = agg_val(region_key, False, 'vpm')
    if wv == 0 and lv == 0:
        return dcc.Graph(figure=empty_fig(), config=CFG)
    adv = (wv / lv - 1) * 100 if lv else 0
    rng = max(40, abs(adv) * 1.4)
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=adv,
        number=dict(suffix="%", prefix="+" if adv >= 0 else "",
                    font=dict(color=COLORS['teal'], size=40)),
        gauge=dict(
            axis=dict(range=[-rng, rng], tickcolor=COLORS['muted'],
                      tickfont=dict(color=COLORS['muted'], size=9)),
            bar=dict(color=COLORS['teal'], thickness=0.28),
            bgcolor='rgba(0,0,0,0)', borderwidth=0,
            steps=[dict(range=[-rng, 0], color=rgba(COLORS['blood'], 0.18)),
                   dict(range=[0, rng], color=rgba(COLORS['teal'], 0.18))],
            threshold=dict(line=dict(color=COLORS['text'], width=2), thickness=0.8, value=0))))
    fig.update_layout(**LAYOUT, title=titled("👁️ Vision Advantage of Winners"), height=GRAPH_H)
    fig.add_annotation(text=f"winners {wv:.2f} · losers {lv:.2f} vision/min",
                       x=0.5, y=-0.08, showarrow=False,
                       font=dict(color=COLORS['muted'], size=11))
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.11 WINNERS' EDGE ──
def winners_vs_losers(region_key, t):
    """
    Горизонтальная столбчатая диаграмма:
    насколько победители превосходят проигравших по 6 метрикам (в %).
    """
    if PLAYER_AGG.empty or t.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)

    tw, tl = t[t['win']], t[~t['win']]

    def team_oci(df):
        """Objective Control Index на уровне команды."""
        if df.empty:
            return 0.0
        return (df['dragons'] + 2 * df['barons'] + 0.5 * df['towers'] + df['inhibitors']).mean()

    metrics = ['Kills/min', 'Damage/min', 'KDA', 'Kill Part. %', 'Vision/min', 'OCI']
    mcolor = [COLORS['blood'], COLORS['blood'], COLORS['gold'],
              COLORS['gold'], COLORS['teal'], COLORS['teal']]

    # Значения для победителей и проигравших
    wv = [agg_val(region_key, True, 'kpm'), agg_val(region_key, True, 'dpm'),
          agg_val(region_key, True, 'kda'), agg_val(region_key, True, 'kp'),
          agg_val(region_key, True, 'vpm'), team_oci(tw)]
    lv = [agg_val(region_key, False, 'kpm'), agg_val(region_key, False, 'dpm'),
          agg_val(region_key, False, 'kda'), agg_val(region_key, False, 'kp'),
          agg_val(region_key, False, 'vpm'), team_oci(tl)]

    # Разница в процентах
    gap = [((wv[i] / lv[i] - 1) * 100) if lv[i] else 0 for i in range(len(metrics))]
    gap_rounded = [round(g) for g in gap]

    fig = go.Figure(go.Bar(
        y=metrics, x=gap, orientation='h',
        marker=dict(color=[rgba(c, 0.85) for c in mcolor],
                    line=dict(color=mcolor, width=0.8)),
        text=[f"+{g:.0f}%" for g in gap], textposition='outside',
        textfont=dict(color=COLORS['text'], size=11),
        customdata=[[wv[i], lv[i], gap_rounded[i]] for i in range(len(metrics))],
        hovertemplate="<b>%{y}</b><br>winners %{customdata[0]:.2f} · "
                      "losers %{customdata[1]:.2f}<br>edge <b>+%{customdata[2]}%</b><extra></extra>"))
    fig.add_vline(x=0, line_color=COLORS['text'], line_width=1.5)
    maxg = max(gap) * 1.35 if gap else 10
    fig.update_layout(**LAYOUT, title=titled("Winners' Edge — % above losers"),
                      height=GRAPH_H, showlegend=False)
    fig.update_xaxes(title=dict(text="how much winners exceed losers (%)",
                                font=dict(color=COLORS['muted'], size=11, family=FONT_FAMILY)),
                     range=[0, maxg], **GRID_NONE)
    fig.update_yaxes(**GRID_NONE)
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.12 OCI COMPOSITION (stacked barplot) ──
def oci_composition(t):
    """
    Stacked barplot: состав Objective Control Index.
    Показывает вклад Towers, Dragons, Barons, Inhibitors в OCI
    для победителей и проигравших.
    """
    if t.empty:
        return dcc.Graph(figure=empty_fig(), config=CFG)

    # Веса для OCI
    OCI_WEIGHTS = {'Towers': 0.5, 'Dragons': 1.0, 'Inhibitors': 1.0, 'Barons': 2.0}
    OCI_ICON = {'Towers': '🏰', 'Dragons': '🐉', 'Inhibitors': '💎', 'Barons': '👑'}
    OCI_COL = {'Towers': 'towers', 'Dragons': 'dragons', 'Inhibitors': 'inhibitors', 'Barons': 'barons'}
    OCI_ORDER = ['Towers', 'Dragons', 'Barons', 'Inhibitors']
    OCI_SHADE = {'Towers': rgba(COLORS['teal'], 0.95), 'Dragons': rgba(COLORS['teal'], 0.72),
                 'Barons': rgba(COLORS['teal'], 0.50), 'Inhibitors': rgba(COLORS['teal'], 0.30)}

    w, l = t[t['win']], t[~t['win']]
    groups = ['🏆 Wins', '💀 Losses']

    # Вклад каждого типа объектов
    contrib = {}
    for n in OCI_ORDER:
        wv = w[OCI_COL[n]].mean() if not w.empty else 0
        lv = l[OCI_COL[n]].mean() if not l.empty else 0
        contrib[n] = [wv * OCI_WEIGHTS[n], lv * OCI_WEIGHTS[n]]

    total_w = sum(contrib[n][0] for n in OCI_ORDER)
    total_l = sum(contrib[n][1] for n in OCI_ORDER)

    fig = go.Figure()
    for n in OCI_ORDER:
        pts = contrib[n]
        raw = [w[OCI_COL[n]].mean() if not w.empty else 0,
               l[OCI_COL[n]].mean() if not l.empty else 0]
        fig.add_bar(
            name=f"{OCI_ICON[n]} {n}", x=groups, y=pts,
            marker=dict(color=OCI_SHADE[n], line=dict(color=COLORS['teal'], width=0.6)),
            text=[f"{p:.1f}" if p > 0.6 else "" for p in pts],
            textposition='inside', insidetextanchor='middle',
            textfont=dict(color=COLORS['bg'], size=10),
            customdata=[[raw[0], OCI_WEIGHTS[n], groups[0]],
                        [raw[1], OCI_WEIGHTS[n], groups[1]]],
            hoverlabel=dict(bgcolor='#000000', bordercolor=COLORS['teal'],
                            font=dict(color='#FFFFFF', size=12.5)),
            hovertemplate=(
                "<b>" + OCI_ICON[n] + " " + n + "</b> · %{customdata[2]}<br>"
                "Count: %{customdata[0]:.2f}<br>"
                "Weight: ×%{customdata[1]}<br>"
                "OCI: <b>%{y:.2f} pts</b><extra></extra>"))

    # Подписи сумм
    fig.add_annotation(x='🏆 Wins', y=total_w, yshift=14, showarrow=False,
                       text=f"<b>{total_w:.1f}</b>", font=dict(color=COLORS['teal'], size=15))
    fig.add_annotation(x='💀 Losses', y=total_l, yshift=14, showarrow=False,
                       text=f"<b>{total_l:.1f}</b>", font=dict(color=COLORS['muted'], size=15))

    # Инсайт: какой объект вносит наибольший вклад в разницу
    impacts = {n: contrib[n][0] - contrib[n][1] for n in OCI_ORDER}
    top = max(impacts, key=impacts.get)
    gap = impacts[top]
    insight = (f"💡 {OCI_ICON[top]} <b>{top}</b> drive the biggest share of winners' "
               f"control — <b>+{gap:.1f} OCI pts</b> over losers (×{OCI_WEIGHTS[top]} weight).")

    fig.update_layout(
        **LAYOUT, title=titled("Objective Control Index — Composition"),
        height=GRAPH_H, barmode='stack', bargap=0.45,
        legend=dict(orientation='h', yanchor='bottom', y=-0.30, xanchor='center',
                    x=0.5, font=dict(color=COLORS['text'], size=10)))
    fig.update_xaxes(tickfont=dict(size=13), **GRID_NONE)
    fig.update_yaxes(title=dict(text="weighted OCI points / game",
                                font=dict(color=COLORS['muted'], size=11, family=FONT_FAMILY)),
                     range=[0, max(total_w, total_l) * 1.18], **GRID_NONE)
    fig.add_annotation(xref='paper', yref='paper', x=0.5, y=-0.45,
                       text=insight, showarrow=False, align='center',
                       font=dict(color=COLORS['muted'], size=10.5), xanchor='center')
    return dcc.Graph(figure=fig, config=CFG)


# ── 6.13 REGION COMPARE (diverging barplot) ──
def region_compare():
    """
    Diverging barplot: сравнение EU vs US по 6 метрикам.
    Столбцы вправо = преимущество первого региона, влево = второго.
    """
    regs = ordered_regions(MATCH['region'])
    if len(regs) < 2:
        return dcc.Graph(figure=empty_fig(message="NEED ≥2 REGIONS"), config=CFG)
    r1, r2 = regs[0], regs[1]

    metrics = ['Kills/min', 'Dmg/min', 'KDA', 'Vision/min', 'Obj/min', 'FB→Win%']

    def vals_for(r):
        """Собрать значения метрик для региона."""
        mm = MATCH[MATCH['region'] == r]
        tt = TEAMS[TEAMS['region'] == r]
        fb = tt[tt['first_blood'] == 1]
        dpm = agg_overall(r, 'dpm')
        kda = agg_overall(r, 'kda')
        vpm = agg_overall(r, 'vpm')
        return [mm['kpm'].mean(), dpm, kda, vpm, mm['opm'].mean(),
                fb['win'].mean() * 100 if not fb.empty else 0]

    v1, v2 = vals_for(r1), vals_for(r2)
    diff = [((a - b) / max(abs(b), 1e-6)) * 100 for a, b in zip(v1, v2)]

    # Форматирование подписей
    fmt = lambda val, i: f"{val:.1f}%" if i == 5 else f"{val:.2f}"
    v1_fmt = [fmt(v1[i], i) for i in range(len(metrics))]
    v2_fmt = [fmt(v2[i], i) for i in range(len(metrics))]
    diff_fmt = [f"{d:+.0f}%" for d in diff]

    def label_with_diff(vals, is_winner):
        """Добавляет разницу к подписи если регион лидирует."""
        return [f"{vals[i]}  (+{abs(diff[i]):.0f}%)" if is_winner[i] else vals[i]
                for i in range(len(metrics))]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=metrics, x=[max(0, d) for d in diff], orientation='h', name=r1,
        marker=dict(color=region_color(r1), line=dict(color=region_color(r1), width=0.5)),
        text=label_with_diff(v1_fmt, [d > 0 for d in diff]),
        textposition='outside', textfont=dict(color=COLORS['text'], size=11),
        customdata=list(zip(v2_fmt, diff_fmt)),
        hovertemplate=(f"<b>%{{y}}</b><br>{r1}: %{{text}}<br>"
                       f"vs: %{{customdata[0]}}<br>diff: %{{customdata[1]}}<extra></extra>")))
    fig.add_trace(go.Bar(
        y=metrics, x=[min(0, -d) for d in diff], orientation='h', name=r2,
        marker=dict(color=region_color(r2), line=dict(color=region_color(r2), width=0.5)),
        text=label_with_diff(v2_fmt, [d < 0 for d in diff]),
        textposition='outside', textfont=dict(color=COLORS['text'], size=11),
        customdata=list(zip(v1_fmt, diff_fmt)),
        hovertemplate=(f"<b>%{{y}}</b><br>{r2}: %{{text}}<br>"
                       f"vs: %{{customdata[0]}}<br>diff: %{{customdata[1]}}<extra></extra>")))

    maxabs = (max(abs(d) for d in diff) * 1.35) if diff else 10
    fig.add_vline(x=0, line_color=COLORS['text'], line_width=1.5)
    fig.update_layout(
        **LAYOUT, title=titled(f"⚔️ {r1} vs {r2} — Region Profile"),
        height=GRAPH_H, barmode='relative',
        legend=dict(orientation='h', yanchor='bottom', y=-0.28, xanchor='center',
                    x=0.5, traceorder='reversed'))
    fig.update_xaxes(title=None, range=[-maxabs, maxabs], **GRID_NONE)
    fig.update_yaxes(**GRID_NONE)
    return dcc.Graph(figure=fig, config=CFG)


# ============================================================
# 7. LAYOUT — вёрстка страницы
# ============================================================

# ── ★ Глобальный CSS из common ──
from common import GLOBAL_CSS

def region_selector():
    """Селектор региона (ALL / EU / US)."""
    return html.Div([
        html.Span("REGION", style={'color': COLORS['gold'], 'fontSize': f"{FS['sm']}px",
                                   'letterSpacing': '2px', 'marginRight': '16px'}),
        dcc.RadioItems(
            id='region-filter', options=REGION_OPTIONS, value='ALL', inline=True,
            labelStyle={'color': COLORS['text'], 'fontSize': f"{FS['sm']}px", 'cursor': 'pointer',
                        'marginRight': '20px', 'letterSpacing': '1px',
                        'display': 'inline-flex', 'alignItems': 'center'},
            inputStyle={'marginRight': '6px', 'cursor': 'pointer',
                        'accentColor': COLORS['gold'], 'transform': 'scale(1.2)'}),
    ], style={'textAlign': 'center', 'marginTop': '18px'})


def serve_layout():
    """Возвращает полный layout страницы Blood & Objectives."""
    return html.Div([
        # ── Шапка с заголовком и описанием ──
        html.Div([
            html.H1("⚔  BLOOD & OBJECTIVES  ⚔", style=PAGE_TITLE_STYLE),
            html.Div("AGGRESSION  ·  INTERPLAY  ·  MAP CONTROL", style=SUB_STYLE),
            html.P(
                "A side-by-side portrait of victory: 🩸 raw aggression on the left "
                "(kills, damage, tempo) against 🗺️ map control on the right "
                "(objectives, vision), with ⚖️ overlapping metrics in the middle. "
                "See what truly separates winners from losers, how the meta shifts "
                "between fighting and control, and how regions differ in style.",
                style={
                    'color': COLORS['muted'],
                    'fontSize': f"{FS['sm']}px",
                    'lineHeight': '1.6',
                    'maxWidth': '880px',
                    'margin': '14px auto 0 auto',
                    'fontStyle': 'italic',
                    'textAlign': 'center',
                },
            ),
            region_selector(),
        ], style=HEADER_STYLE),

        html.Div([
            # ── Заголовки зон ──
            html.Div([
                zone_label("🩸 Aggression", COLORS['blood'], 'left'),
                zone_label("⚖️ Interplay", COLORS['gold'], 'center'),
                zone_label("🗺️ Map Control", COLORS['teal'], 'right'),
            ], style=ZONE_HEADER_ROW),

            # ── KPI-плитки ──
            html.Div(id='kpi-row', style={'marginTop': '14px', 'marginBottom': '8px'}),

            # ═══ РЯД 1: Kills Dist | Meta Pulse | Objectives Dist ═══
            html.Div([
                chart_panel(html.Div(id='kills-dist'), 'kills-dist', COLORS['blood'], PANEL_BLOOD),
                chart_panel(html.Div(id='meta-pulse'), 'meta-pulse', COLORS['gold'], PANEL_GOLD),
                chart_panel(html.Div(id='obj-dist'), 'obj-dist', COLORS['teal'], PANEL_TEAL),
            ], style=ROW3),

            # ═══ РЯД 2: KDA Violin | Kills vs Dur | OCI Comp ═══
            html.Div([
                chart_panel(html.Div(id='kda-violin'), 'kda-violin', COLORS['blood'], PANEL_BLOOD),
                chart_panel(html.Div(id='kills-dur'), 'kills-dur', COLORS['gold'], PANEL_GOLD),
                chart_panel(html.Div(id='oci-comp'), 'oci-comp', COLORS['teal'], PANEL_TEAL),
            ], style=ROW3),

            # ═══ РЯД 3: Dmg Dist | Winners Edge | Vision Dist ═══
            html.Div([
                chart_panel(html.Div(id='dmg-dist'), 'dmg-dist', COLORS['blood'], PANEL_BLOOD),
                chart_panel(html.Div(id='winners-losers'), 'winners-losers', COLORS['gold'], PANEL_GOLD),
                chart_panel(html.Div(id='vision-dist'), 'vision-dist', COLORS['teal'], PANEL_TEAL),
            ], style=ROW3),

            # ═══ РЯД 4: FB Gauge | Region Compare | Vision Gauge ═══
            html.Div([
                chart_panel(html.Div(id='fb-gauge'), 'fb-gauge', COLORS['blood'], PANEL_BLOOD),
                chart_panel(html.Div(id='region-compare'), 'region-compare', COLORS['gold'], PANEL_GOLD),
                chart_panel(html.Div(id='vision-gauge'), 'vision-gauge', COLORS['teal'], PANEL_TEAL),
            ], style=ROW3),

        ], style=CONTAINER),
    ], style=APP_STYLE)


# ============================================================
# 8. CALLBACK — обновление всех графиков при смене региона
# ============================================================

@callback(
    [Output('kpi-row', 'children'),           # KPI-плитки
     Output('kills-dist', 'children'),        # гистограмма kills
     Output('meta-pulse', 'children'),        # тренд агрессии vs контроля
     Output('obj-dist', 'children'),          # гистограмма objectives
     Output('kda-violin', 'children'),        # violin KDA
     Output('kills-dur', 'children'),         # scatter kills vs duration
     Output('vision-dist', 'children'),       # гистограмма vision
     Output('dmg-dist', 'children'),          # 2D-плотность damage vs KP
     Output('winners-losers', 'children'),    # winners' edge
     Output('oci-comp', 'children'),          # OCI composition
     Output('fb-gauge', 'children'),          # first blood gauge
     Output('region-compare', 'children'),    # сравнение регионов
     Output('vision-gauge', 'children')],     # vision advantage gauge
    [Input('region-filter', 'value')],
)
def update(region_key):
    """Единый callback для обновления всей страницы при смене региона."""
    if EMPTY:
        msg = html.Div("⚠️ NO COMBAT DATA", style={'color': COLORS['muted'],
                       'textAlign': 'center', 'padding': '20px'})
        return [msg] * 13

    m = filter_region(MATCH, region_key)
    t = filter_region(TEAMS, region_key)

    return (
        kpi_row(region_key, m, t),
        kills_distribution(m),
        meta_pulse(m),
        objectives_distribution(m),
        kda_winners_vs_losers(region_key),
        kills_vs_duration(m),
        vision_distribution(region_key),
        damage_vs_kda(region_key),           # ← 2D-плотность (была dmg-dist)
        winners_vs_losers(region_key, t),
        oci_composition(t),
        fb_gauge(t),
        region_compare(),
        vision_gauge(region_key),
    )


# Чтобы страницу можно было импортировать в app.py с вкладками
layout = serve_layout()

# Greetings