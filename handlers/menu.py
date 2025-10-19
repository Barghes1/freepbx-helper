from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from handlers.commands import (
    add_cmd,
    create_cmd,
    del_cmd,
    del_inbound_cmd,
    gql_fields_cmd,
    gql_mutations_cmd,
    list_cmd,
    list_routes_cmd,
    logout_cmd,
    ping_cmd,
    reconnect_cmd,
    whoami_cmd,
    add_inbound_cmd,
    goip_detect_ip_cmd,
    set_incoming_sip_cmd,
    connect_profile_by_key,
    load_profiles_for,
    save_profiles_for,
)
from ui.keyboards import (
    MENU_PREFIX,
    back_home_kb,
    ext_menu_kb,
    gql_menu_kb,
    in_menu_kb,
    main_menu_kb,
    sys_menu_kb,
    ast_menu_kb,
    servers_menu_kb,
    preset_actions_kb,
    not_connected_kb,
    
)
from ui.texts import NOT_CONNECTED
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

def _uid(update: Update) -> int:
    return update.effective_user.id

def _profiles(update: Update) -> dict:
    """Личные пресеты текущего пользователя."""
    return load_profiles_for(_uid(update))

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
        [dict(text="✍️ Ввести номер базы", data=f"{MENU_PREFIX}ext.create.eq.custom")],
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
        [dict(text="✍️ Ввести номер базы", data=f"{MENU_PREFIX}in.add.eq.custom")],
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
    st = context.user_data.get("__await")

    # Разрешаем ввод нового имени пресета даже без подключения
    if not _is_connected(context):
        if not st or st.get("kind") != "preset_rename":
            return

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
        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text(
                "❗ Введите DID/EXT или диапазоны, например: 414 или 401-418 или 401 402 410-418"
            )
            return
        context.args = [text]      # <-- не кастуем к int
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
    
    if kind == "ext_set_secret":
        parts = text.split()
        if not parts:
            await update.message.reply_text("❗ Введите: <code>&lt;ext&gt; [new_secret] [--also-ext]</code>", parse_mode=ParseMode.HTML)
            return
        try:
            ext = str(int(parts[0]))
        except Exception:
            await update.message.reply_text("❗ Первый аргумент должен быть номером EXT, например: 301")
            return

        also_ext = False
        new_pass = None
        rest = parts[1:]
        if rest:
            if "--also-ext" in rest:
                also_ext = True
                rest.remove("--also-ext")
            if rest:
                new_pass = rest[0]

        args = [ext]
        if new_pass:
            args.append(new_pass)
        if also_ext:
            args.append("--also-ext")

        context.args = args
        from handlers.commands import set_secret_cmd
        await set_secret_cmd(update, context)
        context.user_data.pop("__await", None)
        return
    
    if kind == "ext_set_secret_range":
        parts = (update.message.text or "").strip().split()
        if not parts:
            await update.message.reply_text(
                "❗ Формат: <code>&lt;targets&gt; [fixed_pass] [--also-ext]</code>\n"
                "Например: <code>301 302 303-305</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # флаг
        also_ext = False
        if "--also-ext" in parts:
            also_ext = True
            parts.remove("--also-ext")

        import re
        target_tokens, fixed_pass = [], None
        for tok in parts:
            if re.fullmatch(r"\d+(-\d+)?", tok):
                target_tokens.append(tok)
            else:
                fixed_pass = tok
                break

        if not target_tokens:
            await update.message.reply_text(
                "❗ Укажи EXT/диапазоны. Пример: <code>301 302 303-305</code>",
                parse_mode=ParseMode.HTML
            )
            return

        args = [" ".join(target_tokens)]
        if fixed_pass:
            args.append(fixed_pass)
        if also_ext:
            args.append("--also-ext")

        context.args = args
        from handlers.commands import set_secret_cmd
        await set_secret_cmd(update, context)
        context.user_data.pop("__await", None)
        return


    # ===== Переименование пресета (персонально) =====
    if kind == "preset_rename":
        key = st.get("key")
        new_label = text
        if not new_label:
            await update.message.reply_text("❗ Имя не может быть пустым. Попробуйте ещё раз.")
            return

        user_id = update.effective_user.id
        profiles = load_profiles_for(user_id)
        if key not in profiles:
            await update.message.reply_text("❌ Профиль не найден.")
            context.user_data.pop("__await", None)
            return

        profiles[key]["label"] = new_label
        save_profiles_for(user_id, profiles)
        await update.message.reply_text(f"✅ Имя пресета обновлено: <b>{new_label}</b>", parse_mode=ParseMode.HTML)

        # показать карточку пресета ещё раз с кнопками действий
        prof = profiles[key]
        txt = (
            "🔗 <b>Профиль</b>\n"
            f"URL: <code>{prof.get('base_url','')}</code>\n"
            f"Client ID: <code>{(prof.get('client_id') or '')[:12]}…</code>\n"
            f"SSH: <code>{(prof.get('ssh') or {}).get('user','—')}@{(prof.get('ssh') or {}).get('host','—')}</code>"
        )
        await update.message.reply_text(txt, parse_mode=ParseMode.HTML, reply_markup=preset_actions_kb(key))
        context.user_data.pop("__await", None)
        return

    context.user_data.pop("__await", None)


# ===== ГЛАВНЫЙ РОУТЕР КНОПОК =====

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    route = data[len(MENU_PREFIX):]

    # === Разрешаем раздел Presets даже без подключения ===
    if not _is_connected(context):
        # Если юзер нажимает на Presets/действия пресетов — пропускаем внутрь
        if route in ("srv",) or route.startswith("srv."):
            # список пресетов
            if route == "srv":
                profiles = _profiles(update)
                if not profiles:
                    await _safe_edit(
                        q,
                        "🔗 <b>Presets</b>\nПока пусто. Выполните /connect с новыми данными, чтобы сохранить профиль.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=not_connected_kb(False)
                    )
                    return
                await _safe_edit(
                    q,
                    "🔗 <b>Presets</b> — выберите профиль:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=servers_menu_kb(profiles)
                )
                return

            # карточка пресета
            if route.startswith("srv.open."):
                key = route.split(".")[-1]
                profiles = _profiles(update)
                if key not in profiles:
                    await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML,
                                     reply_markup=not_connected_kb(bool(profiles)))
                    return
                prof = profiles[key]
                txt = (
                    "🔗 <b>Профиль</b>\n"
                    f"URL: <code>{prof.get('base_url','')}</code>\n"
                    f"Client ID: <code>{(prof.get('client_id') or '')[:12]}…</code>\n"
                    f"SSH: <code>{(prof.get('ssh') or {}).get('user','—')}@{(prof.get('ssh') or {}).get('host','—')}</code>"
                )
                await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=preset_actions_kb(key))
                return

            # подключиться по пресету
            if route.startswith("srv.do.connect."):
                key = route.split(".")[-1]
                await connect_profile_by_key(update, context, key)
                return

            # переименовать пресет
            if route.startswith("srv.do.rename."):
                key = route.split(".")[-1]
                profiles = _profiles(update)
                if key not in profiles:
                    await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML,
                                     reply_markup=not_connected_kb(bool(profiles)))
                    return
                context.user_data["__await"] = {"kind": "preset_rename", "key": key}
                await _safe_edit(
                    q,
                    "✍️ Введите новое имя для пресета (будет на кнопке):",
                    parse_mode=ParseMode.HTML,
                    reply_markup=not_connected_kb(bool(profiles))
                )
                return

            # удалить пресет
            if route.startswith("srv.do.delete."):
                key = route.split(".")[-1]
                profiles = _profiles(update)
                if key in profiles:
                    profiles.pop(key, None)
                    save_profiles_for(_uid(update), profiles)
                    await _safe_edit(
                        q,
                        "🗑 Профиль удалён.\n\n🔗 <b>Presets</b> — список:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=servers_menu_kb(profiles) if profiles else not_connected_kb(False)
                    )
                    return
                else:
                    await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML,
                                     reply_markup=not_connected_kb(bool(profiles)))
                    return

        # Иначе — показываем подсказку + кнопку Presets (если есть)
        profiles = _profiles(update)
        await _safe_edit(q, NOT_CONNECTED, parse_mode=ParseMode.HTML,
                         reply_markup=not_connected_kb(bool(profiles)))
        return

    # ===== Ниже — логика для подключённого состояния =====

    # разделы верхнего уровня
    if route == "home":
        await _safe_edit(q, "🏠 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb()); return
    if route == "ext":
        await _safe_edit(q, "🧩 <b>Extensions</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=ext_menu_kb()); return
    if route == "in":
        await _safe_edit(q, "⬅️ <b>Inbound</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=in_menu_kb()); return

    # ===== Asterisk =====
    if route == "ast":
        await _safe_edit(q, "📞 <b>Asterisk</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=ast_menu_kb()); return
    if route == "ast.detect":
        await goip_detect_ip_cmd(update, context); return
    if route == "ast.sync":
        await set_incoming_sip_cmd(update, context); return
    if route == "ast.radmin.restart":
        from handlers.commands import radmin_restart_cmd
        await radmin_restart_cmd(update, context); return

    # ===== System =====
    if route == "sys":
        await _safe_edit(q, "🛠 <b>System</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=sys_menu_kb()); return
    if route == "sys.reconnect":
        await reconnect_cmd(update, context); return
    if route == "sys.ping":
        await ping_cmd(update, context); return
    if route == "sys.whoami":
        await whoami_cmd(update, context); return
    if route == "sys.logout":
        await logout_cmd(update, context)
        await _safe_edit(q, "🏠 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        return

    # ===== GraphQL =====
    if route == "gql":
        await _safe_edit(q, "🧬 <b>GraphQL</b> — выберите действие:", parse_mode=ParseMode.HTML, reply_markup=gql_menu_kb()); return
    if route == "gql.fields":
        await gql_fields_cmd(update, context); return
    if route == "gql.mutations":
        await gql_mutations_cmd(update, context); return

    if route == "help":
        await _safe_edit(q, "ℹ️ <b>Help</b>. Команды доступны через меню.", parse_mode=ParseMode.HTML, reply_markup=back_home_kb()); return

    # список
    if route == "ext.list":
        await list_cmd(update, context); return

    # ===== СОЗДАНИЕ =====
    if route == "ext.create":
        await _ext_create_root(q); return
    if route == "ext.create.eq":
        await _ext_create_pick_eq(q); return
    if route == "ext.create.eq.custom":
        context.user_data["__await"] = {"kind": "create_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return
    if route.startswith("ext.create.eq."):
        eq = int(route.split(".")[-1])
        await _ext_create_pick_qty(q, eq); return
    if route.startswith("ext.create.eqqty."):
        parts = route.split(".")
        if len(parts) >= 4 and parts[3] == "custom":
            eq = int(parts[4])
            context.user_data["__await"] = {"kind": "create_eq_qty", "eq": eq}
            await _safe_edit(
                q,
                f"✍️ Введите <b>количество</b> для базы <b>{eq}</b> (целое число):",
                parse_mode=ParseMode.HTML,
                reply_markup=back_home_kb()
            ); return
        eq = int(parts[3]); qty = int(parts[4])
        context.args = [str(eq), str(qty)]
        await create_cmd(update, context); return
    if route == "ext.create.single":
        context.user_data["__await"] = {"kind": "add_single"}
        await _safe_edit(
            q,
            "✍️ Введите EXT и (опционально) префикс имени. Примеры:\n"
            "<code>101</code>\n"
            "<code>101 Офис Киев</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return
    if route == "ext.create.range":
        context.user_data["__await"] = {"kind": "add_range"}
        await _safe_edit(
            q,
            "✍️ Введите диапазон и (опционально) префикс имени. Примеры:\n"
            "<code>101-105</code>\n"
            "<code>401-418 Продажи</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return

    # ===== УДАЛЕНИЕ =====
    if route == "ext.del":
        await _ext_delete_root(q); return
    if route == "ext.del.numbers":
        context.user_data["__await"] = {"kind": "del_numbers"}
        await _safe_edit(
            q,
            "✍️ Введите номера/диапазоны для удаления.\n"
            "Примеры:\n"
            "<code>401</code>\n"
            "<code>401 402 410-418</code>",
            parse_mode=ParseMode.HTML, reply_markup=back_home_kb()
        ); return
    if route in ("ext.del.eq", "ext.del_root"):
        await _ext_delete_pick_eq(q); return
    if route == "ext.del.eq.custom":
        context.user_data["__await"] = {"kind": "del_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return
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
    # ===== ПАРОЛЬ =====
    if route == "ext.secret":
        # делаем универсальный ввод: один или много/диапазоны
        context.user_data["__await"] = {"kind": "ext_set_secret_range"}
        await _safe_edit(
            q,
            "✍️ Введите цели и (опционально) общий пароль.\n"
            "Примеры:\n"
            "<code>301</code>\n"
            "<code>301-305</code>\n"
            "<code>301 302 303-305</code>\n"
            "<code>301-305 OneStrongPass --also-ext</code>\n"
            "Если пароль не указан — каждому EXT будет сгенерирован уникальный.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return


    # ===== Presets =====
    if route == "srv":
        profiles = _profiles(update)
        if not profiles:
            await _safe_edit(
                q,
                "🔗 <b>Presets</b>\nПока пусто. Выполните /connect с новыми данными, чтобы сохранить профиль.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_home_kb()
            ); return
        await _safe_edit(
            q,
            "🔗 <b>Presets</b> — выберите профиль:",
            parse_mode=ParseMode.HTML,
            reply_markup=servers_menu_kb(profiles)
        ); return

    if route.startswith("srv.open."):
        key = route.split(".")[-1]
        profiles = _profiles(update)
        if key not in profiles:
            await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML, reply_markup=back_home_kb()); return
        prof = profiles[key]
        txt = (
            "🔗 <b>Профиль</b>\n"
            f"URL: <code>{prof.get('base_url','')}</code>\n"
            f"Client ID: <code>{(prof.get('client_id') or '')[:12]}…</code>\n"
            f"SSH: <code>{(prof.get('ssh') or {}).get('user','—')}@{(prof.get('ssh') or {}).get('host','—')}</code>"
        )
        await _safe_edit(q, txt, parse_mode=ParseMode.HTML, reply_markup=preset_actions_kb(key)); return

    if route.startswith("srv.do.connect."):
        key = route.split(".")[-1]
        await connect_profile_by_key(update, context, key); return

    if route.startswith("srv.do.rename."):
        key = route.split(".")[-1]
        profiles = _profiles(update)
        if key not in profiles:
            await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML, reply_markup=back_home_kb()); return
        context.user_data["__await"] = {"kind": "preset_rename", "key": key}
        await _safe_edit(
            q,
            "✍️ Введите новое имя для пресета (будет на кнопке):",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return

    if route.startswith("srv.do.delete."):
        key = route.split(".")[-1]
        profiles = _profiles(update)
        if key in profiles:
            profiles.pop(key, None)
            save_profiles_for(_uid(update), profiles)
            await _safe_edit(
                q,
                "🗑 Профиль удалён.\n\n🔗 <b>Presets</b> — список:",
                parse_mode=ParseMode.HTML,
                reply_markup=servers_menu_kb(profiles) if profiles else back_home_kb()
            ); return
        else:
            await _safe_edit(q, "❌ Профиль не найден.", parse_mode=ParseMode.HTML, reply_markup=back_home_kb()); return

    # ===== INBOUND =====
    if route == "in.list":
        await list_routes_cmd(update, context); return
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
        ); return
    if route == "in.add.eq":
        await _in_add_pick_eq(q); return
    if route == "in.add.eq.custom":
        context.user_data["__await"] = {"kind": "in_add_eq_pick"}
        await _safe_edit(
            q,
            "✍️ Введите номер <b>базы</b> (например: 1, 2, 3, 4, 10).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return
    if route.startswith("in.add.eq."):
        eq = int(route.split(".")[-1])
        start = equip_start(eq)
        end = start + 99
        context.args = [f"{start}-{end}"]
        await add_inbound_cmd(update, context); return
    if route == "in.del":
        context.user_data["__await"] = {"kind": "in_del"}
        await _safe_edit(
            q,
            "✍️ Введите DID/EXT для удаления Inbound Route(ов). Допустимы одиночные номера, диапазоны и несколько целей.\n"
            "Примеры:\n"
            "<code>414</code>\n"
            "<code>401-418</code>\n"
            "<code>401 402 410-418</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home_kb()
        ); return


    # фоллбек
    await _safe_edit(
        q,
        f"⏳ Нажато: <code>{route}</code>. Раздел в разработке.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_home_kb()
    )
