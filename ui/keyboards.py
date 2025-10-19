from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Dict

MENU_PREFIX = "menu:"

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🧩 Extensions", callback_data=f"{MENU_PREFIX}ext"),
            InlineKeyboardButton("⬅️ Inbound",    callback_data=f"{MENU_PREFIX}in"),
        ],
        [
            InlineKeyboardButton("📞 Asterisk",    callback_data=f"{MENU_PREFIX}ast"),
            InlineKeyboardButton("🛠 System",      callback_data=f"{MENU_PREFIX}sys"),
        ],
        [
            InlineKeyboardButton("🔗 Presets",     callback_data=f"{MENU_PREFIX}srv"),  # ← НОВОЕ
            InlineKeyboardButton("🧬 GraphQL",     callback_data=f"{MENU_PREFIX}gql"),
        ],
        [InlineKeyboardButton("ℹ️ Help",          callback_data=f"{MENU_PREFIX}help")],
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
            InlineKeyboardButton("🔑 Пароль (chan-sip)", callback_data=f"{MENU_PREFIX}ext.secret"),

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

def ast_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔎 Detect GOIP IP",  callback_data=f"{MENU_PREFIX}ast.detect"),
            InlineKeyboardButton("🔁 Sync SIP Server", callback_data=f"{MENU_PREFIX}ast.sync"),  # ← НОВОЕ
        ],
        [
            InlineKeyboardButton("🧰 Restart radmsrv",  callback_data=f"{MENU_PREFIX}ast.radmin.restart"),  # ← NEW
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"{MENU_PREFIX}home")],
    ])

def servers_menu_kb(profiles: Dict[str, dict]) -> InlineKeyboardMarkup:
    rows = []
    buttons = []
    for key, prof in profiles.items():
        label = prof.get("label") or prof.get("base_url")
        buttons.append(InlineKeyboardButton(label, callback_data=f"{MENU_PREFIX}srv.open.{key}"))  # ← ОТКРЫТЬ ПОДМЕНЮ
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data=f"{MENU_PREFIX}home")])
    return InlineKeyboardMarkup(rows)

def preset_actions_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔗 Подключиться", callback_data=f"{MENU_PREFIX}srv.do.connect.{key}"),
        ],
        [
            InlineKeyboardButton("✏️ Переименовать", callback_data=f"{MENU_PREFIX}srv.do.rename.{key}"),
            InlineKeyboardButton("🗑 Удалить",        callback_data=f"{MENU_PREFIX}srv.do.delete.{key}"),
        ],
        [InlineKeyboardButton("⬅️ Назад к Presets", callback_data=f"{MENU_PREFIX}srv")],
    ])
    
def not_connected_kb(has_presets: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_presets:
        rows.append([InlineKeyboardButton("🔗 Presets", callback_data=f"{MENU_PREFIX}srv")])
    rows.append([InlineKeyboardButton("ℹ️ Help", callback_data=f"{MENU_PREFIX}help")])
    return InlineKeyboardMarkup(rows)