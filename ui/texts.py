from telegram import InlineKeyboardMarkup, InlineKeyboardButton

HELP_TEXT = (
    "📘 <b>Помощь</b>\n\n"
    "🔌 <b>Подключение к FreePBX</b>\n"
    "  <code>/connect &lt;ip&gt; &lt;login&gt; &lt;password&gt;</code>\n"
    "  • <i>ip</i> — базовый URL FreePBX (например: http://77.105.146.189)\n"
    "  • <i>login</i> — Client ID из FreePBX API\n"
    "  • <i>password</i> — Client Secret из FreePBX API\n\n"

    "📄 <b>Список линий</b>\n"
    "  <code>/list</code> — список EXT и паролей (с навигацией)\n\n"

    "🆕 <b>Создание линий</b>\n"
    "  <code>/create &lt;оборудование&gt; &lt;кол-во&gt;</code>\n"
    "  • старт номеров: 1→101, 2→201, 3→301, 4→401, 10→1001\n"
    "  • пример: <code>/create 4 10</code> (создаст 401… по порядку)\n\n"

    "🗑️ <b>Удаление линий</b>\n"
    "  • Точечно/диапазон: <code>/del 401 402 410-418</code>\n"
    "  • По оборудованию:  <code>/del_eq 4</code> (все 401–499)\n"
    "  • Все сразу:        <code>/del_all</code>\n\n"

    "♻️ <b>Сессия</b>\n"
    "  <code>/reconnect</code> — переподключение с последними данными\n"
    "  <code>/ping</code> — проверка токена и GraphQL (OK/ошибка)\n"
    "  <code>/whoami</code> — показать текущий URL и Client ID\n"
    "  <code>/logout</code> — сброс сессии\n\n"
    
    "🧩 <b>Добавление линий</b>\n"
    "  <code>/add &lt;ext&gt; [имя]</code>\n"
    "  <code>/add &lt;start-end&gt; [префикс_имени]</code>\n"
    "  • Проверка дублей по номеру и имени\n\n"
    
    "📥 <b>Inbound Routes</b>\n"
    "  <code>/list_routes</code> — список всех маршрутов\n"
    "  <code>/add_inbound &lt;ext&gt;</code> или <code>/add_inbound &lt;start-end&gt;</code>\n"
    "    • Создаёт маршрут DID→EXT, Description=simEXT\n"
    "  <code>/del_inbound &lt;ext&gt;</code>\n"
    "    • Удаляет маршрут по DID\n\n"
)

def _list_page_text(ip: str, pairs_page):
    """Собрать текст для страницы списка EXT/паролей."""
    if not pairs_page:
        return f"{ip}\n\n(пусто)"
    lines = [ip, ""]
    lines += [f"{ext} {pw}" for ext, pw in pairs_page]
    return "\n".join(lines)


def _list_nav_kb(page: int, pages: int):
    """Построить клавиатуру навигации по страницам."""
    prev_btn = InlineKeyboardButton("⬅️", callback_data=f"list:page:{page-1}")
    next_btn = InlineKeyboardButton("➡️", callback_data=f"list:page:{page+1}")
    nums = InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop")
    row = []
    if page > 0:
        row.append(prev_btn)
    row.append(nums)
    if page < pages - 1:
        row.append(next_btn)
    return InlineKeyboardMarkup([row]) if pages > 1 else None

MENU_WELCOME = (
    "✅ Подключение успешно.\n"
    "Выбирайте раздел ниже — больше не нужно писать команды вручную."
)

NOT_CONNECTED = (
    "❗️Сначала подключитесь: "
    "<code>/connect &lt;ip&gt; &lt;login&gt; &lt;password&gt;</code>"
)
