# src/handlers/commands.py
import os
import re
import time
import asyncio
import secrets
import logging
from html import escape
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse
from telegram import BotCommand

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton,     
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
    ReplyKeyboardRemove
    )
from telegram.ext import ContextTypes
from telegram.constants import ParseMode, ChatAction

from core.freepbx import FreePBX, AlreadyExists
from ui.texts import HELP_TEXT, _list_nav_kb, _list_page_text
from utils.common import clean_url, equip_start, parse_targets, next_free

from ui.keyboards import main_menu_kb

log = logging.getLogger(__name__)

# ----- Session / settings -----
SESS: Dict[int, dict] = {}
PAGE_SIZE = 50
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")


# ===== Internal helpers =====
def fb_from_session(chat_id: int) -> FreePBX:
    s = SESS.get(chat_id)
    if not s:
        raise RuntimeError("Сначала /connect <ip> <login> <password>")
    fb = FreePBX(s["base_url"], s["client_id"], s["client_secret"], verify=s["verify"])
    fb.token = s.get("token")
    fb.token_exp = s.get("token_exp", 0)
    return fb

def _need_connect_text() -> str:
    return "Сначала подключитесь:\n<code>/connect &lt;ip&gt; &lt;login&gt; &lt;password&gt;</code>"

async def _ensure_connected(u: Update) -> bool:
    chat = u.effective_chat
    if chat and chat.id in SESS:
        return True
    if getattr(u, "message", None):
        await u.message.reply_text(_need_connect_text())
    elif getattr(u, "callback_query", None):
        await u.callback_query.answer("Сначала подключитесь: /connect <ip> <login> <password>", show_alert=True)
    return False

def _slice_pairs(pairs, page: int, page_size: int = PAGE_SIZE):
    total = len(pairs)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    end = start + page_size
    return pairs[start:end], page, pages


# ===== Commands =====
async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # Сначала уберём старую reply-клавиатуру, если была
    await u.message.reply_text(
        "👋 Привет! Я помогу управлять FreePBX: подключение, список SIP, создание и удаление.\n"
        "Набери /help для инструкции.",
        reply_markup=ReplyKeyboardRemove()
    )
    # И сразу покажем inline-главное меню
    await u.message.reply_text(
        "🏠 <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )

async def help_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP_TEXT)


async def connect_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if len(c.args) < 3:
        await u.message.reply_text(
            "Формат:\n"
            "<code>/connect &lt;ip&gt; &lt;login&gt; &lt;password&gt;</code>\n"
            "Пример: <code>/connect http://77.105.146.189 CID SECRET</code>"
        )
        return

    raw_ip, login, password = c.args[0], c.args[1], c.args[2]
    parsed = urlparse(raw_ip)
    if not parsed.scheme:
        base_url = f"http://{raw_ip}"
        verify = False
    else:
        base_url = raw_ip
        verify = not base_url.startswith("http://")

    await u.message.chat.send_action(ChatAction.TYPING)
    fb = FreePBX(base_url, login, password, verify=verify)

    try:
        fb.ensure_token()
        SESS[u.effective_chat.id] = {
            "base_url": fb.base_url,
            "client_id": login,
            "client_secret": password,
            "verify": verify,
            "token": fb.token,
            "token_exp": fb.token_exp,
        }

        # ставим флаг для меню
        c.user_data["__connected"] = True

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        text = _list_page_text(clean_url(fb.base_url), pairs_page)
        kb = _list_nav_kb(page, pages)

        await u.message.reply_text(f"✅ Подключено к <code>{escape(fb.base_url)}</code>")

        # Показать главное меню
        await u.message.reply_text(
            "🏠 <b>Главное меню</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )

        # (как и раньше) список экстеншенов
        await u.message.reply_text(text, reply_markup=kb)

    except Exception as e:
        await u.message.reply_text(f"Ошибка подключения: <code>{escape(str(e))}</code>")


async def list_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        fb = fb_from_session(u.effective_chat.id)
        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)

        target = u.effective_message  # универсальная цель ответа
        await target.reply_text(
            _list_page_text(clean_url(fb.base_url), pairs_page),
            reply_markup=_list_nav_kb(page, pages)
        )
    except Exception as e:
        target = u.effective_message
        await target.reply_text(f"Ошибка: <code>{escape(str(e))}</code>")


async def create_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message  # универсальная "точка ответа" (и для message, и для callback)

    if len(c.args) < 2:
        await target.reply_text(
            "❗ Формат команды:\n"
            "<code>/create &lt;оборудование&gt; &lt;кол-во&gt;</code>\n\n"
            "Где <b>оборудование</b> — номер базы (старт EXT):\n"
            "• 1 → 101…\n• 2 → 201…\n• 3 → 301…\n• 4 → 401…\n• 10 → 1001…\n\n"
            "Пример:\n<code>/create 4 10</code> — создаст 10 линий, начиная с 401.\n",
        )
        return

    if not await _ensure_connected(u):
        return

    try:
        eq = int(c.args[0])
        cnt = int(c.args[1])
        if cnt <= 0:
            raise ValueError
    except Exception:
        await target.reply_text(
            "❗ Некорректные аргументы.\n"
            "Используй: <code>/create &lt;оборудование&gt; &lt;кол-во&gt;</code>\n"
            "Например: <code>/create 4 10</code>"
        )
        return

    fb = fb_from_session(u.effective_chat.id)
    try:
        notice = await target.reply_text(f"⏳ Создаю {cnt} линий… (0/{cnt})")
        await target.chat.send_action(ChatAction.TYPING)

        all_pairs = fb.fetch_all_extensions()
        existing = [ext for ext, _ in all_pairs]
        start = equip_start(eq)
        targets = next_free(existing, start, cnt)

        created = 0
        for i, ext in enumerate(targets, 1):
            fb.create_one(int(ext))
            secret = secrets.token_hex(16)
            fb.set_ext_password(ext, secret)

            created += 1
            if created % 5 == 0 or created == cnt:
                try:
                    await notice.edit_text(f"⏳ Создаю {cnt} линий… ({created}/{cnt})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        if targets:
            try:
                try:
                    await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception:
                    pass
                fb.apply_config()
                try:
                    await notice.edit_text("✅ Конфиг применён. Обновляю список…")
                except Exception:
                    pass
            except Exception as e:
                await target.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)

        await target.reply_text(
            _list_page_text(clean_url(fb.base_url), pairs_page),
            reply_markup=_list_nav_kb(page, pages)
        )
    except Exception as e:
        await target.reply_text(f"Ошибка создания: <code>{escape(str(e))}</code>")


async def del_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message
    if not c.args:
        await target.reply_text("Формат: /del 401 402 410-418")
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    requested = parse_targets(" ".join(c.args))
    try:
        await target.chat.send_action(ChatAction.TYPING)
        by_ext, _, _ = fb.fetch_ext_index()
        existing = set(by_ext.keys())

        targets = [x for x in requested if x in existing]
        missing = [x for x in requested if x not in existing]

        total = len(targets)
        notice = await target.reply_text(f"⏳ Удаляю линии… (0/{total})") if total else None

        ok, failed = [], []
        for i, ext in enumerate(targets, 1):
            try:
                fb.delete_extension(ext)
                ok.append(ext)
            except Exception:
                failed.append(ext)
            if notice and (i % 10 == 0 or i == total):
                try:
                    await notice.edit_text(f"⏳ Удаляю линии… ({i}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        parts = []
        if ok:      parts.append("🗑️ Удалено: " + ", ".join(ok))
        if missing: parts.append("↩️ Пропущено (нет такой линии): " + ", ".join(missing))
        if failed:  parts.append("❌ Ошибка удаления: " + ", ".join(failed))
        if not parts: parts.append("Нечего удалять.")
        await target.reply_text("\n".join(parts))

        if ok:
            try:
                if notice:
                    try: await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                    except Exception: pass
                fb.apply_config()
                if notice:
                    try: await notice.edit_text("✅ Конфиг применён. Обновляю список…")
                    except Exception: pass
            except Exception as e:
                await target.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        page_items, page, pages = _slice_pairs(pairs, page=0)
        await target.reply_text(
            _list_page_text(clean_url(fb.base_url), page_items),
            reply_markup=_list_nav_kb(page, pages)
        )
    except Exception as e:
        await target.reply_text(f"Ошибка удаления: <code>{escape(str(e))}</code>")

async def del_eq_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message
    if not c.args:
        await target.reply_text("Формат: /del_eq <оборудование>")
        return
    if not await _ensure_connected(u):
        return
    eq = int(c.args[0])
    start = equip_start(eq)
    end = start + 99
    c.args = [f"{start}-{end}"]
    await del_cmd(u, c)

async def del_all_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Да, удалить всё", callback_data="delall:yes"),
         InlineKeyboardButton("Отмена", callback_data="delall:no")]
    ])
    await u.message.reply_text("⚠️ Точно удалить все линии?", reply_markup=kb)

async def add_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        await u.message.reply_text(
            "Форматы:\n"
            "<code>/add &lt;ext&gt; [имя]</code>\n"
            "<code>/add &lt;start-end&gt; [префикс_имени]</code>\n"
            "Примеры:\n"
            "<code>/add 101</code>\n"
            "<code>/add 101 Офис Киев</code>\n"
            "<code>/add 101-105 Продажи</code>"
        )
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    arg0 = c.args[0]
    name_tail = " ".join(c.args[1:]).strip()
    try:
        await u.message.chat.send_action(ChatAction.TYPING)

        targets = parse_targets(arg0) if "-" in arg0 else [arg0]
        total = len(targets)
        notice = await u.message.reply_text(f"⏳ Добавляю линии… (0/{total})")

        by_ext, name_set, name_ok = fb.fetch_ext_index()
        existing_exts = set(by_ext.keys())

        created, skipped_ext, skipped_name = [], [], []
        name_check_warn = False

        processed = 0
        for raw in targets:
            ext = str(int(raw))
            cand_name = (f"{name_tail} {ext}" if "-" in arg0 else name_tail) if name_tail else ext

            if ext in existing_exts:
                skipped_ext.append(ext)
            elif name_ok and cand_name.strip().lower() in name_set:
                skipped_name.append(f"{ext} ({cand_name})")
            else:
                if not name_ok:
                    name_check_warn = True
                fb.create_one(int(ext), cand_name)
                secret = secrets.token_hex(16)
                fb.set_ext_password(ext, secret)
                created.append(ext)
                existing_exts.add(ext)
                if name_ok and cand_name.strip():
                    name_set.add(cand_name.strip().lower())

            processed += 1
            if processed % 5 == 0 or processed == total:
                try:
                    await notice.edit_text(f"⏳ Добавляю линии… ({processed}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        parts = []
        if created:
            parts.append("✅ Создано: " + ", ".join(created))
        if skipped_ext:
            parts.append("↩️ Уже существуют EXT: " + ", ".join(skipped_ext))
        if skipped_name:
            parts.append("🔁 Дубли имён: " + ", ".join(skipped_name))
        if name_check_warn:
            parts.append("ℹ️ Имя проверить не удалось (сервер не отдаёт имена).")
        if not parts:
            parts.append("Нечего делать.")
        await u.message.reply_text("\n".join(parts))

        if created:
            try:
                try:
                    await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception:
                    pass
                fb.apply_config()
                try:
                    await notice.edit_text("✅ Конфиг применён. Обновляю список…")
                except Exception:
                    pass
            except Exception as e:
                await u.message.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        page_items, page, pages = _slice_pairs(pairs, page=0)
        await u.message.reply_text(
            _list_page_text(clean_url(fb.base_url), page_items),
            reply_markup=_list_nav_kb(page, pages)
        )
    except Exception as e:
        await u.message.reply_text(f"Ошибка /add: <code>{escape(str(e))}</code>")

async def reconnect_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        target = u.effective_message
        s = SESS.get(u.effective_chat.id)
        fb = FreePBX(s["base_url"], s["client_id"], s["client_secret"], verify=s["verify"])
        fb.ensure_token()
        s["token"] = fb.token
        s["token_exp"] = fb.token_exp
        await target.reply_text("🔁 Переподключение выполнено.")
    except Exception as e:
        await target.reply_text(f"Ошибка reconnect: <code>{escape(str(e))}</code>")

async def ping_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        target = u.effective_message
        fb = fb_from_session(u.effective_chat.id)
        fb.ensure_token()
        _ = fb.gql("query { fetchAllExtensions { extension { extensionId } } }")
        await target.reply_text("✅ OK")
    except Exception as e:
        await target.reply_text(f"❌ Unauthorized / ошибка: <code>{escape(str(e))}</code>")

async def whoami_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    target = u.effective_message
    s = SESS.get(u.effective_chat.id)
    ttl = max(0, int(s.get("token_exp", 0) - time.time()))
    await target.reply_text(
        "👤 Текущая сессия\n"
        f"URL: <code>{s['base_url']}</code>\n"
        f"Client ID: <code>{s['client_id']}</code>\n"
        f"TLS verify: <code>{s['verify']}</code>\n"
        f"Токен жив ещё: <code>{ttl} сек</code>"
    )

async def logout_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message
    SESS.pop(u.effective_chat.id, None)
    c.user_data.clear()
    await target.reply_text("🚪 Сессия сброшена. Используйте /connect.")

async def list_routes_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    fb = fb_from_session(u.effective_chat.id)
    target = u.effective_message
    try:
        routes = fb.list_inbound_routes()
        if not routes:
            await target.reply_text("Маршрутов не найдено.")
            return
        lines = ["DID | Description | ID"]
        for r in routes:
            did = r.get("extension")
            desc = r.get("description")
            rid = r.get("id")
            lines.append(f"{did} | {desc} | {rid}")
        await target.reply_text("\n".join(lines))
    except Exception as e:
        await target.reply_text(f"Ошибка /list_routes: <code>{escape(str(e))}</code>")



async def add_inbound_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /add_inbound 414
    /add_inbound 414-420
    Для каждого существующего EXT создаём inbound route:
    DID=_sim{ext}, Description=sim{ext}, Destination -> Extension {ext}
    """
    target = u.effective_message

    if not c.args:
        await target.reply_text(
            "Форматы:\n"
            "<code>/add_inbound &lt;ext&gt;</code>\n"
            "<code>/add_inbound &lt;start-end&gt;</code>\n"
            "Примеры:\n"
            "<code>/add_inbound 414</code>\n"
            "<code>/add_inbound 401-418</code>\n"
            "Маршрут создаётся только если EXT существует. DID будет в формате <code>_simEXT</code>."
        )
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    arg0 = " ".join(c.args)
    targets = parse_targets(arg0)

    try:
        await target.chat.send_action(ChatAction.TYPING)

        # Индекс существующих EXT
        by_ext, _, _ = fb.fetch_ext_index()
        existing_exts = set(by_ext.keys())

        # Уже существующие DID у маршрутов (для защиты от дублей, учитываем оба формата)
        routes_now = fb.list_inbound_routes()
        existing_dids = {r.get("extension") for r in routes_now if r.get("extension")}

        todo = [x for x in targets if x in existing_exts]
        missing = [x for x in targets if x not in existing_exts]

        total = len(todo)
        if total == 0:
            msg = ["Нечего создавать."]
            if missing:
                msg.append("↩️ Пропущено (нет таких EXT): " + ", ".join(missing))
            await target.reply_text("\n".join(msg))
            return

        notice = await target.reply_text(f"⏳ Добавляю Inbound Routes… (0/{total})")

        ok, skipped_exists, failed = [], [], []
        for i, ext in enumerate(todo, 1):
            did_plain = ext
            did_prefx = f"_sim{ext}"
            # если уже есть старый маршрут (DID=ext) или новый (DID=_simext) — пропустим
            if did_plain in existing_dids or did_prefx in existing_dids:
                skipped_exists.append(ext)
            else:
                try:
                    fb.create_inbound_route(did=did_prefx, description=f"sim{ext}", ext=ext)
                    ok.append(ext)
                    existing_dids.add(did_prefx)
                except AlreadyExists:
                    skipped_exists.append(ext)
                except Exception as e:
                    failed.append(f"{ext} ({str(e)[:80]})")

            if i % 5 == 0 or i == total:
                try:
                    await notice.edit_text(f"⏳ Добавляю Inbound Routes… ({i}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        parts = []
        if ok:
            parts.append("✅ Создано маршрутов: " + ", ".join(ok))
        if skipped_exists:
            parts.append("↩️ Пропущено (уже есть DID ext или _simext): " + ", ".join(skipped_exists))
        if missing:
            parts.append("↩️ Нет таких EXT: " + ", ".join(missing))
        if failed:
            parts.append("❌ Ошибки: " + ", ".join(failed))
        await target.reply_text("\n".join(parts) if parts else "Нечего делать.")

        if ok:
            try:
                try:
                    await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception:
                    pass
                fb.apply_config()
                try:
                    await notice.edit_text("✅ Конфиг применён.")
                except Exception:
                    pass
            except Exception as e:
                await target.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

    except Exception as e:
        await target.reply_text(f"Ошибка /add_inbound: <code>{escape(str(e))}</code>")


async def del_inbound_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /del_inbound <ext>
    Удаляет inbound route по extension (DID).
    Принимаем EXT без префикса, а ищем DID и как _simEXT, и как старый EXT.
    """
    target = u.effective_message

    if not c.args:
        await target.reply_text("Использование: <code>/del_inbound &lt;ext&gt;</code>")
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    number = c.args[0]

    try:
        await target.chat.send_action(ChatAction.TYPING)

        routes = fb.list_inbound_routes()
        did_new = f"_sim{number}"
        # Сначала ищем новый формат
        route = next((r for r in routes if r.get("extension") == did_new), None)
        # Затем — старый формат (на всякий случай)
        if not route:
            route = next((r for r in routes if r.get("extension") == number), None)

        if not route:
            await target.reply_text(f"❌ Маршрут для EXT {number} не найден (ни DID={did_new}, ни DID={number})")
            return

        shown = route.get("extension") or number
        notice = await target.reply_text(f"⏳ Удаляю Inbound Route {shown}…")

        res = fb.delete_inbound_route(route["id"])
        status = (res.get("status") if isinstance(res, dict) else None)
        msg = (res.get("message") if isinstance(res, dict) else "") or ""

        status_str = str(status).lower() if status is not None else ""
        okish = status_str in ("ok", "true") or "success" in msg.lower()

        if okish:
            await notice.edit_text(f"✅ Маршрут {shown} удалён. 🔄 Применяю конфиг…")
            try:
                fb.apply_config()
                await notice.edit_text(f"✅ Маршрут {shown} удалён.\n✅ Конфиг применён.")
            except Exception as e:
                await notice.edit_text(
                    f"✅ Маршрут {shown} удалён.\n⚠️ Apply Config не удалось: {escape(str(e))}"
                )
        else:
            await notice.edit_text(f"❌ Ошибка удаления {shown}: {msg or res}")

    except Exception as e:
        await target.reply_text(f"Ошибка /del_inbound: <code>{escape(str(e))}</code>")



async def gql_fields_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    fb = fb_from_session(u.effective_chat.id)
    target = u.effective_message
    try:
        fields = fb.list_query_fields()
        if not fields:
            await target.reply_text("Query-поля не найдены.")
            return
        chunk = []
        total = 0
        for f in fields:
            line = f"- {f}"
            if sum(len(x) for x in chunk) + len(line) + 1 > 3500:
                await target.reply_text("Доступные Query:\n" + "\n".join(chunk))
                chunk = []
            chunk.append(line)
            total += 1
        if chunk:
            await target.reply_text("Доступные Query:\n" + "\n".join(chunk))
    except Exception as e:
        await target.reply_text(f"Ошибка introspect: {escape(str(e))}")
        

async def gql_mutations_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    fb = fb_from_session(u.effective_chat.id)
    target = u.effective_message
    try:
        muts = fb.list_mutations()
        if not muts:
            await target.reply_text("Mutation-поля не найдены.")
            return
        chunk = []
        total = 0
        for m in muts:
            line = f"- {m}"
            if sum(len(x) for x in chunk) + len(line) + 1 > 3500:
                await target.reply_text("Доступные Mutation:\n" + "\n".join(chunk))
                chunk = []
            chunk.append(line)
            total += 1
        if chunk:
            await target.reply_text("Доступные Mutation:\n" + "\n".join(chunk))
    except Exception as e:
        await target.reply_text(f"Ошибка introspect: {escape(str(e))}")
        
async def menu_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    target = u.effective_message
    await target.reply_text(
        "🏠 <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )

# ===== Lifecycle hook (used in main.py via .post_init(on_startup)) =====
async def on_startup(app):
    print("✅ Бот запущен и слушает обновления. Набери /help в Telegram для инструкции.")
    log.info("✅ Бот запущен и слушает обновления. Команда помощи: /help")

    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(int(ADMIN_CHAT_ID), "✅ Бот запущен и готов к работе. Напишите /help.")
        except Exception as e:
            log.warning(f"Не удалось отправить сообщение админу: {e}")

    try:
        await app.bot.delete_my_commands()  # default
        await app.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await app.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await app.bot.delete_my_commands(scope=BotCommandScopeAllChatAdministrators())

        commands = [
            BotCommand("start", "Запуск"),
            BotCommand("help", "Помощь"),
            BotCommand("connect", "Подключиться к FreePBX"),
            BotCommand("menu", "Главное меню"),
            BotCommand("list", "Список EXT"),
            BotCommand("create", "Создать по базе"),
            BotCommand("add", "Добавить EXT/диапазон"),
            BotCommand("del", "Удалить EXT/диапазон"),
            BotCommand("del_eq", "Удалить по базе (100 номеров)"),
            BotCommand("del_all", "Удалить все EXT"),
            BotCommand("add_inbound", "Создать inbound (EXT/диапазон)"),
            BotCommand("del_inbound", "Удалить inbound по DID"),
            BotCommand("list_routes", "Список inbound маршрутов"),
            BotCommand("reconnect", "Переподключиться"),
            BotCommand("ping", "Проверка GraphQL"),
            BotCommand("whoami", "Текущая сессия"),
            BotCommand("logout", "Сброс сессии"),
        ]

        await app.bot.set_my_commands(commands)  # default
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllChatAdministrators())

        try:
            await app.bot.set_chat_menu_button()  # вернёт стандартную «голубую» кнопку
        except Exception:
            pass
    except Exception as e:
        print(f"set_my_commands failed: {e}")
