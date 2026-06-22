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
# УБИТЬ СТАРЫЙ ПРОЦЕСС НА ПОРТУ 8050
# ═══════════════════════════════════════════════════════════
# ВАЖНО: при debug=True Dash запускает ДВА процесса (watcher + worker).
# kill_port нужно выполнять ТОЛЬКО в главном процессе, иначе reloader
# будет убивать сам себя при каждой перезагрузке.
def kill_port(port):
    """
    Находит и убивает процесс, слушающий указанный порт.
    Использует netstat + taskkill (только для Windows).
    """
    try:
        import subprocess
        # Ищем PID процесса на порту
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


# Выполняем kill_port ТОЛЬКО при самом первом запуске,
# а не при каждой авто-перезагрузке reloader'а.
if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
    kill_port(8050)


# ═══════════════════════════════════════════════════════════
# СОЗДАНИЕ ПРИЛОЖЕНИЯ DASH
# ═══════════════════════════════════════════════════════════
app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,          # разрешаем callback-и для динамических компонентов
    title="LoL Analytics Dashboard"            # заголовок вкладки браузера
)

# ── ★ Кастомный index.html — встраиваем глобальные CSS-стили ──
app.index_string = f'''
<!DOCTYPE html>
<html>
    <head>
        {{%metas%}}
        <title>{{%title%}}</title>
        {{%favicon%}}
        {{%css%}}
        {GLOBAL_CSS}                           <!-- глобальные стили из common.py -->
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
# Передаём ФУНКЦИЮ (не результат вызова), чтобы layout
# пересобирался при каждой перезагрузке (hot reload).
def serve_app_layout():
    """Возвращает полный layout приложения с навигацией."""
    return html.Div([
        # ── Навигационная панель (вкладки) ──
        html.Div([
            html.A("🏆 Champions Meta", href="#", id="nav-champions",
                   className="nav-button active"),      # активная по умолчанию
            html.A("📊 Match Overview", href="#", id="nav-overview",
                   className="nav-button"),
            html.A("⚔️ Blood & Objectives", href="#", id="nav-combat",
                   className="nav-button"),
        ], className="navbar"),

        # ── Контейнер для содержимого активной страницы ──
        html.Div(id="page-content"),

        # ── Общий футер ──
        build_footer()
    ], style=APP_STYLE)


app.layout = serve_app_layout   # ← передаём ФУНКЦИЮ, без скобок ()


# ═══════════════════════════════════════════════════════════
# CALLBACK — переключение вкладок
# ═══════════════════════════════════════════════════════════
@app.callback(
    # Обновляем: содержимое страницы + классы активности кнопок
    [Output("page-content", "children"),           # контент выбранной страницы
     Output("nav-champions", "className"),         # класс кнопки Champions
     Output("nav-overview", "className"),          # класс кнопки Overview
     Output("nav-combat", "className")],           # класс кнопки Combat
    [Input("nav-champions", "n_clicks"),           # клик по Champions
     Input("nav-overview", "n_clicks"),            # клик по Overview
     Input("nav-combat", "n_clicks")]              # клик по Combat
)
def switch_page(n1, n2, n3):
    """
    Переключает содержимое страницы и подсветку активной вкладки.
    При первом запуске (ни один триггер не сработал) — показываем Champions.
    """
    ctx = dash.callback_context

    # Если ни одна кнопка не нажата — страница по умолчанию
    if not ctx.triggered:
        return (page_champions.layout, "nav-button active", "nav-button", "nav-button")

    # Определяем какая кнопка нажата
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]

    if button_id == "nav-champions":
        return (page_champions.layout, "nav-button active", "nav-button", "nav-button")
    elif button_id == "nav-overview":
        return (page_overview.layout, "nav-button", "nav-button active", "nav-button")
    else:  # nav-combat
        return (page_combat.layout, "nav-button", "nav-button", "nav-button active")


# ═══════════════════════════════════════════════════════════
# ТОЧКА ВХОДА — запуск сервера
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("🔗 Дашборд: http://127.0.0.1:8050")
    app.run(
        debug=True,                          # подробные ошибки + hot reload
        use_reloader=True,                   # авто-перезапуск при сохранении файла
        dev_tools_hot_reload=True,           # перезагрузка фронтенда без F5
        dev_tools_hot_reload_interval=1,     # проверять изменения раз в 1 сек
        dev_tools_hot_reload_watch_interval=1,
        port=8050,
    )