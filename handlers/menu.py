from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from ui.keyboards import (
    MENU_PREFIX, main_menu_kb, back_home_kb,
    ext_menu_kb, in_menu_kb, sys_menu_kb, gql_menu_kb
)
from ui.texts import NOT_CONNECTED

from handlers.commands import (
    list_cmd, create_cmd, add_cmd, del_cmd, 
    reconnect_cmd, ping_cmd, whoami_cmd, logout_cmd, 
    gql_fields_cmd, gql_mutations_cmd,
    list_routes_cmd, add_inbound_cmd, del_inbound_cmd
)

from utils.common import equip_start
# ===== helpers =====

def _is_connected(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("__connected"))

async def _safe_edit(q, text, **kwargs):
    try:
        return await q.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return None
        raise

def _mk_inline(spec_rows):
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for row in spec_rows:
        rows.append([InlineKeyboardButton(b["text"], callback_data=b["data"]) for b in row])
    return InlineKeyboardMarkup(rows)

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.effective_message
    await target.reply_text(
        "🏠 <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


# ===== ЭКРАНЫ: Создание =====

async def _ext_create_root(q, *, text_suffix: str = ""):
    txt = (
        "➕ <b>Создание линий</b>\n"
        "Выберите способ:\n\n"
        "• По оборудованию (например, 4 → 401…)\n"
        "• Один номер (EXT)\n"
        "• Диапазон (start-end)\n"
    )
    if text_suffix:
        txt += f"\n<code>{text_suffix}</code>"
    kb = [
        [dict(text="🏭 По оборудованию", data=f"{MENU_PREFIX}ext.create.eq")],
        [
            dict(text="1️⃣ Один номер", data=f"{MENU_PREFIX}ext.create.single"),
            dict(text="↔️ Диапазон",    data=f"{MENU_PREFIX}ext.create.range"),
        ],
        [dict(text="🔙 Назад", data=f"{MENU_PREFIX}ext")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb))

async def _ext_create_pick_eq(q):
    txt = "🏭 Выберите <b>оборудование</b> (стартовая база EXT):"
    kb = [
        [
            dict(text="1 → 101…", data=f"{MENU_PREFIX}ext.create.eq.1"),
            dict(text="2 → 201…", data=f"{MENU_PREFIX}ext.create.eq.2"),
        ],
        [
            dict(text="3 → 301…", data=f"{MENU_PREFIX}ext.create.eq.3"),
            dict(text="4 → 401…", data=f"{MENU_PREFIX}ext.create.eq.4"),
        ],
        [dict(text="10 → 1001…", data=f"{MENU_PREFIX}ext.create.eq.10")],
        [dict(text="✍️ Ввести номер базы", data=f"{MENU_PREFIX}ext.create.eq.custom")],  # ← НОВОЕ
        [dict(text="🔙 Назад",   data=f"{MENU_PREFIX}ext.create")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML,
                     reply_markup=_mk_inline(kb))


async def _ext_create_pick_qty(q, eq: int):
    txt = f"📦 База <b>{eq}</b>. Выберите <b>количество</b> линий:"
    kb = [
        [
            dict(text="1",  data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.1"),
            dict(text="5",  data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.5"),
            dict(text="10", data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.10"),
        ],
        [
            dict(text="20",            data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.20"),
            dict(text="50",            data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.50"),
            dict(text="✍️ Ввести число", data=f"{MENU_PREFIX}ext.create.eqqty.custom.{eq}"),
        ],
        [dict(text="🔙 Назад", data=f"{MENU_PREFIX}ext.create.eq")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb))
    
async def _ext_create_pick_qty_send(target_message, eq: int):
    txt = f"📦 База <b>{eq}</b>. Выберите <b>количество</b> линий:"
    kb = [
        [
            dict(text="1",  data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.1"),
            dict(text="5",  data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.5"),
            dict(text="10", data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.10"),
        ],
        [
            dict(text="20",            data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.20"),
            dict(text="50",            data=f"{MENU_PREFIX}ext.create.eqqty.{eq}.50"),
            dict(text="✍️ Ввести число", data=f"{MENU_PREFIX}ext.create.eqqty.custom.{eq}"),
        ],
        [dict(text="🔙 Назад", data=f"{MENU_PREFIX}ext.create.eq")],
    ]
    await target_message.reply_text(
        txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb)
    )


async def _in_add_pick_eq(q):
    txt = "🏭 Выберите <b>базу</b>, для которой создать inbound-маршруты для ВСЕХ существующих EXT (диапазон 100 номеров):"
    kb = [
        [
            dict(text="1 → 101-199",  data=f"{MENU_PREFIX}in.add.eq.1"),
            dict(text="2 → 201-299",  data=f"{MENU_PREFIX}in.add.eq.2"),
        ],
        [
            dict(text="3 → 301-399",  data=f"{MENU_PREFIX}in.add.eq.3"),
            dict(text="4 → 401-499",  data=f"{MENU_PREFIX}in.add.eq.4"),
        ],
        [dict(text="10 → 1001-1099", data=f"{MENU_PREFIX}in.add.eq.10")],
        [dict(text="✍️ Ввести номер базы", data=f"{MENU_PREFIX}in.add.eq.custom")],  # ← НОВОЕ
        [dict(text="🔙 Назад",        data=f"{MENU_PREFIX}in")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb))

# ===== ЭКРАНЫ: Удаление =====

async def _ext_delete_root(q):
    txt = (
        "🗑️ <b>Удаление линий</b>\n"
        "Выберите способ:\n"
        "• Удалить по номерам/диапазону (например, 401 402 410-418)\n"
        "• Удалить все 100 номеров конкретной базы (1→101…, 4→401… и т.д.)\n"
        "• Удалить ВСЁ (все EXT)\n"
    )
    kb = [
        [dict(text="🔢 Удалить номера/диапазон", data=f"{MENU_PREFIX}ext.del.numbers")],
        [dict(text="🏭 Удалить по базе",         data=f"{MENU_PREFIX}ext.del.eq")],
        [dict(text="🧨 Удалить ВСЁ",             data=f"{MENU_PREFIX}ext.del_all")],
        [dict(text="🔙 Назад",                   data=f"{MENU_PREFIX}ext")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb))

async def _ext_delete_pick_eq(q):
    txt = "🏭 Выберите <b>базу</b> для удаления её диапазона (100 номеров):"
    kb = [
        [
            dict(text="1 → 101-199",  data=f"{MENU_PREFIX}ext.del.eq.1"),
            dict(text="2 → 201-299",  data=f"{MENU_PREFIX}ext.del.eq.2"),
        ],
        [
            dict(text="3 → 301-399",  data=f"{MENU_PREFIX}ext.del.eq.3"),
            dict(text="4 → 401-499",  data=f"{MENU_PREFIX}ext.del.eq.4"),
        ],
        [dict(text="10 → 1001-1099", data=f"{MENU_PREFIX}ext.del.eq.10")],
        [dict(text="✍️ Ввести номер базы", data=f"{MENU_PREFIX}ext.del.eq.custom")],
        [dict(text="🔙 Назад",        data=f"{MENU_PREFIX}ext")],
    ]
    await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=_mk_inline(kb))

# ===== ТЕКСТОВЫЙ РОУТЕР (ввод пользователя) =====

async def menu_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрируется в main.py как MessageHandler(filters.TEXT & ~filters.COMMAND)."""
    if not _is_connected(context):
        return

    st = context.user_data.get("__await")
    if not st:
        return

    kind = st.get("kind")
    text = (update.message.text or "").strip()

    if kind == "create_eq_qty":
        eq = int(st["eq"])
        try:
            qty = int(text)
            if qty <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text("❗ Введите целое положительное число. Например: 10")
            return
        context.args = [str(eq), str(qty)]
        await create_cmd(update, context)
        context.user_data.pop("__await", None)
        return

    if kind == "add_single":
        parts = text.split(maxsplit=1)
        try:
            ext = str(int(parts[0]))
        except Exception:
            await update.message.reply_text("❗ Введите EXT, например: 101 или '101 Офис Киев'")
            return
        name = parts[1] if len(parts) > 1 else ""
        context.args = [ext] + ([name] if name else [])
        await add_cmd(update, context)
        context.user_data.pop("__await", None)
        return

    if kind == "add_range":
        parts = text.split(maxsplit=1)
        rng = parts[0]
        name_prefix = parts[1] if len(parts) > 1 else ""
        if "-" not in rng:
            await update.message.reply_text("❗ Введите диапазон в формате 101-105 (и опц. префикс)")
            return
        a, b = rng.split("-", 1)
        try:
            _ = int(a); _ = int(b)
        except Exception:
            await update.message.reply_text("❗ Диапазон должен быть числами, например 401-418")
            return
        context.args = [rng] + ([name_prefix] if name_prefix else [])
        await add_cmd(update, context)
        context.user_data.pop("__await", None)
        return

    if kind == "del_numbers":
        context.args = [text]
        await del_cmd(update, context)
        context.user_data.pop("__await", None)
        return
    
    if kind == "in_add":
        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text("❗ Введите EXT или диапазон, например: 414 или 401-418")
            return

        context.args = [text]
        await add_inbound_cmd(update, context)
        context.user_data.pop("__await", None)
        return

    if kind == "in_del":
        did = (update.message.text or "").strip()
        try:
            _ = int(did)
        except Exception:
            await update.message.reply_text("❗ DID должен быть числом, например: 414")
            return
        context.args = [did]
        await del_inbound_cmd(update, context)
        context.user_data.pop("__await", None)
        return
    
    if kind == "create_eq_pick":
        txt = (update.message.text or "").strip()
        try:
            eq = int(txt)
            _ = equip_start(eq)
        except Exception:
            await update.message.reply_text(
                "❗ Некорректный номер базы. Введите одно из: 1, 2, 3, 4, 10."
            )
            return
        await _ext_create_pick_qty_send(update.effective_message, eq)
        context.user_data.pop("__await", None)
        return

    if kind == "in_add_eq_pick":
        txt = (update.message.text or "").strip()
        try:
            eq = int(txt)
            start = equip_start(eq)
            end = start + 99
        except Exception:
            await update.message.reply_text(
                "❗ Некорректный номер базы. Введите одно из: 1, 2, 3, 4, 10."
            )
            return
        context.args = [f"{start}-{end}"]
        await add_inbound_cmd(update, context)
        context.user_data.pop("__await", None)
        return
    
    if kind == "del_eq_pick":
        txt = (update.message.text or "").strip()
        try:
            eq = int(txt)
            from utils.common import equip_start
            _ = equip_start(eq)
        except Exception:
            await update.message.reply_text(
                "❗ Некорректный номер базы. Введите одно из: 1, 2, 3, 4, 10."
            )
            return

        from handlers.commands import del_eq_cmd  
        context.args = [str(eq)]
        await del_eq_cmd(update, context)

        context.user_data.pop("__await", None)
        return


    context.user_data.pop("__await", None)


# ===== ГЛАВНЫЙ РОУТЕР КНОПОК =====

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not _is_connected(context):
        await _safe_edit(q, NOT_CONNECTED, parse_mode=ParseMode.HTML, reply_markup=back_home_kb())
        return

    data = q.data
    route = data[len(MENU_PREFIX):]

    # разделы
    if route == "home":
        await _safe_edit(q, "🏠 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()); return
    if route == "ext":
        await _safe_edit(q, "🧩 <b>Extensions</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=ext_menu_kb()); return
    if route == "ext.create.eq.custom":
        context.user_data["__await"] = {"kind": "create_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        )
        return
    if route == "ext.del.eq.custom":
        context.user_data["__await"] = {"kind": "del_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        )
        return
    if route == "in":
        await _safe_edit(q, "⬅️ <b>Inbound</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=in_menu_kb()); return
    if route == "in.list":
        await list_routes_cmd(update, context)
        return
    if route == "in.add":
        context.user_data["__await"] = {"kind": "in_add"}
        await _safe_edit(
            q,
            "✍️ Введите EXT или диапазон для создания inbound route(ов).\n"
            "Примеры:\n"
            "<code>414</code>\n"
            "<code>401-418</code>\n"
            "Описание будет вида <code>sim{ext}</code>, назначение — на Extension {ext}.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        )
        return
    if route == "in.add.eq":
        await _in_add_pick_eq(q)
        return
    if route == "in.add.eq.custom":
        context.user_data["__await"] = {"kind": "in_add_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        )
        return
    if route.startswith("in.add.eq."):
        eq = int(route.split(".")[-1])
        start = equip_start(eq)           
        end = start + 99                  
        context.args = [f"{start}-{end}"] 
        await add_inbound_cmd(update, context)
        return
    if route == "in.del":
        context.user_data["__await"] = {"kind": "in_del"}
        await _safe_edit(
            q,
            "✍️ Введите DID (EXT) маршрута, который нужно удалить.\n"
            "Например: <code>414</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        )
        return

    if route == "sys":
        await _safe_edit(q, "🛠 <b>System</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=sys_menu_kb()); return
    
    if route == "sys.reconnect":
        await reconnect_cmd(update, context)
        return

    if route == "sys.ping":
        await ping_cmd(update, context)
        return

    if route == "sys.whoami":
        await whoami_cmd(update, context)
        return

    if route == "sys.logout":
        await logout_cmd(update, context)
        await _safe_edit(q, "🏠 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        return

    if route == "gql":
        await _safe_edit(q, "🧬 <b>GraphQL</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=gql_menu_kb()); return
    if route == "gql.fields":
        await gql_fields_cmd(update, context)
        return
    if route == "gql.mutations":
        await gql_mutations_cmd(update, context)
        return

    if route == "help":
        await _safe_edit(q, "ℹ️ <b>Help</b>. Команды доступны через меню.", parse_mode=ParseMode.HTML, reply_markup=back_home_kb()); return

    # список
    if route == "ext.list":
        await list_cmd(update, context); return

    # создание
    if route == "ext.create":
        await _ext_create_root(q); return
    if route == "ext.create.eq":
        await _ext_create_pick_eq(q); return
    if route.startswith("ext.create.eq."):
        eq = int(route.split(".")[-1])
        await _ext_create_pick_qty(q, eq); return
    if route.startswith("ext.create.eqqty."):
        parts = route.split(".")
        if len(parts) >= 4 and parts[3] == "custom":
            eq = int(parts[4])
            context.user_data["__await"] = {"kind": "create_eq_qty", "eq": eq}
            await _safe_edit(q,
                f"✍️ Введите <b>количество</b> для базы <b>{eq}</b> (целое число):",
                parse_mode=ParseMode.HTML,
                reply_markup=back_home_kb()
            )
            return
        eq = int(parts[3]); qty = int(parts[4])
        context.args = [str(eq), str(qty)]
        await create_cmd(update, context); return
    if route == "ext.create.single":
        context.user_data["__await"] = {"kind": "add_single"}
        await _safe_edit(q,
            "✍️ Введите EXT и (опционально) имя. Примеры:\n"
            "<code>101</code>\n"
            "<code>101 Офис Киев</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return
    if route == "ext.create.range":
        context.user_data["__await"] = {"kind": "add_range"}
        await _safe_edit(q,
            "✍️ Введите диапазон и (опционально) префикс имени. Примеры:\n"
            "<code>101-105</code>\n"
            "<code>401-418 Продажи</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return

    # удаление
    if route == "ext.del":
        await _ext_delete_root(q); return
    if route == "ext.del.numbers":
        context.user_data["__await"] = {"kind": "del_numbers"}
        await _safe_edit(q,
            "✍️ Введите номера/диапазоны для удаления.\n"
            "Примеры:\n"
            "<code>401</code>\n"
            "<code>401 402 410-418</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return
    if route in ("ext.del.eq", "ext.del_root"):
        await _ext_delete_pick_eq(q); return
    if route.startswith("ext.del.eq."):
        eq = int(route.split(".")[-1])
        context.args = [f"{eq}"]
        from handlers.commands import del_eq_cmd  # локальный импорт
        await del_eq_cmd(update, context); return
    if route == "ext.del_all":
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, удалить всё", callback_data="delall:yes"),
             InlineKeyboardButton("Отмена",          callback_data="delall:no")]
        ])
        await _safe_edit(q, "⚠️ Точно удалить все линии?", parse_mode=ParseMode.HTML, reply_markup=kb); return

    # фоллбек
    await _safe_edit(q,
        f"⏳ Нажато: <code>{route}</code>. Раздел в разработке.",
        parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
    )
