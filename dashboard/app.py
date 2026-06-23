# ============================================================
# app.py — ГЛАВНОЕ ПРИЛОЖЕНИЕ DASH С ВКЛАДКАМИ
# ============================================================
# Запуск: python app.py  →  http://127.0.0.1:8050
#
# Структура:
#   • Вкладка 1 — Champions Meta (page_champions.py)
#   • Вкладка 2 — Match Overview (page_overview.py)
#   • Вкладка 3 — Blood & Objectives (page_combat.py)
#
# Использует: common.py — общие константы, стили, функции
# ============================================================

# ── Импорт библиотек ──
import os                                # для проверки переменных окружения (WERKZEUG_RUN_MAIN)
import dash                              # сам Dash
from dash import dcc, html              # компоненты Dash
from dash.dependencies import Input, Output  # для callback-ов

# ── ★ Импорт общего модуля ──
from common import *                     # все общие константы, стили, утилиты

# ── ★ Импорт страниц (каждая страница — отдельный модуль) ──
import page_champions                    # вкладка 1: Champions Meta
import page_overview                     # вкладка 2: Match Overview
import page_combat                       # вкладка 3: Blood & Objectives


# ═══════════════════════════════════════════════════════════
# ОПРЕДЕЛЯЕМ ГДЕ ЗАПУЩЕНО: локально или в Docker
# ═══════════════════════════════════════════════════════════
IN_DOCKER = os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER', False)


# ═══════════════════════════════════════════════════════════
# УБИТЬ СТАРЫЙ ПРОЦЕСС НА ПОРТУ 8050 (только локально)
# ═══════════════════════════════════════════════════════════
def kill_port(port):
    """
    Находит и убивает процесс, слушающий указанный порт.
    Использует netstat + taskkill (только для Windows).
    В Docker не вызывается.
    """
    try:
        import subprocess
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True, capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if 'LISTENING' in line:
                pid = line.strip().split()[-1]
                subprocess.run(f'taskkill /F /PID {pid}', shell=True,
                               capture_output=True)
                print(f"🔪 Убит старый процесс на порту {port} (PID {pid})")
    except Exception:
        pass


# Выполняем kill_port ТОЛЬКО локально и при первом запуске
if not IN_DOCKER and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    kill_port(8050)


# ═══════════════════════════════════════════════════════════
# СОЗДАНИЕ ПРИЛОЖЕНИЯ DASH
# ═══════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="LoL Analytics Dashboard"
)

# ── ★ Кастомный index.html — встраиваем глобальные CSS-стили ──
app.index_string = f'''
<!DOCTYPE html>
<html>
    <head>
        {{%metas%}}
        <meta name="viewport" content="width=1400, initial-scale=0.5, user-scalable=yes">
        <title>{{%title%}}</title>
        {{%favicon%}}
        {{%css%}}
        {GLOBAL_CSS}
    </head>
    <body>
        {{%app_entry%}}
        <footer>{{%config%}}{{%scripts%}}{{%renderer%}}</footer>
    </body>
</html>
'''


# ═══════════════════════════════════════════════════════════
# LAYOUT — навигация + контент страниц
# ═══════════════════════════════════════════════════════════
def serve_app_layout():
    """Возвращает полный layout приложения с навигацией."""
    return html.Div([
        html.Div([
            html.A("🏆 Champions Meta", href="#", id="nav-champions",
                   className="nav-button active"),
            html.A("📊 Match Overview", href="#", id="nav-overview",
                   className="nav-button"),
            html.A("⚔️ Blood & Objectives", href="#", id="nav-combat",
                   className="nav-button"),
        ], className="navbar"),
        html.Div(id="page-content"),
        build_footer()
    ], style=APP_STYLE)


app.layout = serve_app_layout


# ═══════════════════════════════════════════════════════════
# CALLBACK — переключение вкладок
# ═══════════════════════════════════════════════════════════
@app.callback(
    [Output("page-content", "children"),
     Output("nav-champions", "className"),
     Output("nav-overview", "className"),
     Output("nav-combat", "className")],
    [Input("nav-champions", "n_clicks"),
     Input("nav-overview", "n_clicks"),
     Input("nav-combat", "n_clicks")]
)
def switch_page(n1, n2, n3):
    ctx = dash.callback_context
    if not ctx.triggered:
        return (page_champions.layout, "nav-button active", "nav-button", "nav-button")
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    if button_id == "nav-champions":
        return (page_champions.layout, "nav-button active", "nav-button", "nav-button")
    elif button_id == "nav-overview":
        return (page_overview.layout, "nav-button", "nav-button active", "nav-button")
    else:
        return (page_combat.layout, "nav-button", "nav-button", "nav-button active")


# ═══════════════════════════════════════════════════════════
# ТОЧКА ВХОДА — запуск сервера
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    if IN_DOCKER:
        # Продакшен-режим (Docker / Hugging Face)
        port = int(os.environ.get('PORT', 8050))
        print(f"🔗 Дашборд запущен (production mode) на порту {port}")
        app.run(
            debug=False,
            host='0.0.0.0',
            port=port,
        )
    else:
        # Режим разработки (локально)
        print("🔗 Дашборд: http://127.0.0.1:8050")
        app.run(
            debug=True,
            use_reloader=True,
            dev_tools_hot_reload=True,
            dev_tools_hot_reload_interval=1,
            dev_tools_hot_reload_watch_interval=1,
            host='127.0.0.1',
            port=8050,
        )