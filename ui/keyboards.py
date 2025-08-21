from telegram import InlineKeyboardButton, InlineKeyboardMarkup

MENU_PREFIX = "menu:"  # общий префикс для навигации

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🧩 Extensions", callback_data=f"{MENU_PREFIX}ext"),
            InlineKeyboardButton("⬅️ Inbound",    callback_data=f"{MENU_PREFIX}in"),
        ],
        [
            InlineKeyboardButton("🛠 System",     callback_data=f"{MENU_PREFIX}sys"),
            InlineKeyboardButton("🧬 GraphQL",    callback_data=f"{MENU_PREFIX}gql"),
        ],
        [InlineKeyboardButton("ℹ️ Help",         callback_data=f"{MENU_PREFIX}help")],
    ]
    return InlineKeyboardMarkup(rows)

def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Назад", callback_data=f"{MENU_PREFIX}home")]]
    )

def ext_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Список",            callback_data=f"{MENU_PREFIX}ext.list"),
            InlineKeyboardButton("✨ Создать",            callback_data=f"{MENU_PREFIX}ext.create"),
        ],
        [
            InlineKeyboardButton("🗑️ Удалить номера",    callback_data=f"{MENU_PREFIX}ext.del"),
        ],
        [
            InlineKeyboardButton("🧨 Удалить ВСЁ",       callback_data=f"{MENU_PREFIX}ext.del_all"),
        ],
        [InlineKeyboardButton("⬅️ Назад",               callback_data=f"{MENU_PREFIX}home")],
    ])

def in_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Список",         callback_data=f"{MENU_PREFIX}in.list"),
            InlineKeyboardButton("➕ Добавить",       callback_data=f"{MENU_PREFIX}in.add"),
        ],
        [
            InlineKeyboardButton("🏭➕ Для всей базы", callback_data=f"{MENU_PREFIX}in.add.eq"),
            InlineKeyboardButton("🗑️ Удалить",        callback_data=f"{MENU_PREFIX}in.del"),
        ],
        [InlineKeyboardButton("⬅️ Назад",            callback_data=f"{MENU_PREFIX}home")],
    ])
    
def sys_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Reconnect", callback_data=f"{MENU_PREFIX}sys.reconnect"),
            InlineKeyboardButton("🏓 Ping",       callback_data=f"{MENU_PREFIX}sys.ping"),
        ],
        [
            InlineKeyboardButton("👤 WhoAmI",     callback_data=f"{MENU_PREFIX}sys.whoami"),
            InlineKeyboardButton("🚪 Logout",     callback_data=f"{MENU_PREFIX}sys.logout"),
        ],
        [InlineKeyboardButton("🔙 Назад",         callback_data=f"{MENU_PREFIX}home")],
    ])

def gql_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📑 Fields",     callback_data=f"{MENU_PREFIX}gql.fields"),
            InlineKeyboardButton("⚒ Mutations",   callback_data=f"{MENU_PREFIX}gql.mutations"),
        ],
        [InlineKeyboardButton("🔙 Назад",         callback_data=f"{MENU_PREFIX}home")],
    ])
