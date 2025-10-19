import asyncio
import json
import logging
import os
import re
import secrets
import sys
import time
from html import escape
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, ContextTypes

from core.asterisk import (
    SSHExecError,
    _ssh_run,
    fetch_endpoint_raw_via_ssh,
    fetch_goip_ips_via_ssh,
    fetch_pjsip_endpoints_via_ssh,
    set_extension_chansip_secret_via_ssh,
    set_incoming_trunk_sip_server_via_ssh,
    create_outbound_route_with_ranges_via_ssh,
)
from core.freepbx import AlreadyExists, FreePBX
from core.goip import GoIP, GoipStatus

from ui.keyboards import main_menu_kb, not_connected_kb
from ui.texts import HELP_TEXT, _list_nav_kb, _list_page_text

from utils.common import (
    _gen_secret,
    _profile_key,
    _slice_pairs,
    clean_url,
    equip_start,
    next_free,
    parse_targets,
    _ext_to_slot
)


log = logging.getLogger(__name__)

def _default_presets_path() -> str:
    env = os.getenv("PRESETS_PATH")
    if env:
        p = Path(env).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    appdir = "freepbx-telegram-bot"
    home = Path.home()

    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA", home / "AppData" / "Roaming"))
        root = base / appdir
    elif sys.platform == "darwin":
        root = home / "Library" / "Application Support" / appdir
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", home / ".config"))
        root = base / appdir

    root.mkdir(parents=True, exist_ok=True)
    return str(root / "presets.json")

PRESETS_PATH = _default_presets_path()

SESS: Dict[int, dict] = {}
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
GOIP_SESS: Dict[int, dict] = {}
GOIP_STATE_CACHE: Dict[int, str] = {}

def _presets_dir() -> Path:
    p = Path(_default_presets_path()).expanduser()
    root = p.parent if p.suffix else p
    root.mkdir(parents=True, exist_ok=True)
    return root

def _user_presets_path(user_id: int) -> Path:
    return _presets_dir() / f"presets.{user_id}.json"

def load_profiles_for(user_id: int) -> dict:
    p = _user_presets_path(user_id)
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        log.warning(f"Failed to load presets for {user_id} from {p}: {e}")
    return {}

def save_profiles_for(user_id: int, profiles: dict) -> None:
    p = _user_presets_path(user_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        log.info(f"Saved {len(profiles)} presets to {p}")
    except Exception as e:
        log.warning(f"Failed to save presets for {user_id} to {p}: {e}")

def _uid(u: Update) -> int:
    return u.effective_user.id

def fb_from_session(chat_id: int) -> FreePBX:
    s = SESS.get(chat_id)
    if not s:
        raise RuntimeError("Сначала /connect <ip> <login> <password>")
    fb = FreePBX(s["base_url"], s["client_id"], s["client_secret"], verify=s["verify"])
    fb.token = s.get("token")
    fb.token_exp = s.get("token_exp", 0)
    return fb

def _need_connect_text() -> str:
    return (
        "❗️Сначала подключитесь:\n"
        "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; [&lt;ssh_login&gt; &lt;ssh_password&gt;]</code>\n\n"
        "…или откройте <b>Presets</b> ниже 👇"
    )

async def _ensure_connected(u: Update) -> bool:
    chat = u.effective_chat
    if chat and chat.id in SESS:
        return True

    # Показываем текст + кнопку "Presets"
    text = _need_connect_text()
    kb = not_connected_kb(True)  # рисуем кнопку Presets даже если список пуст — внутри меню это обработается

    # Унифицированный ответ: если это обычное сообщение — reply_text, если callback — alert + редактирование не трогаем
    if getattr(u, "message", None):
        await u.message.reply_text(text, reply_markup=kb)
    elif getattr(u, "callback_query", None):
        # Покажем alert и отправим отдельным сообщением подсказку с кнопками
        try:
            await u.callback_query.answer("Сначала подключитесь: /connect … или используйте Presets", show_alert=True)
        except Exception:
            pass
        # отправим в чат подсказку с клавиатурой
        try:
            await u.callback_query.message.reply_text(text, reply_markup=kb)
        except Exception:
            pass
    return False

async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "👋 Привет! Я помогу управлять FreePBX: подключение, список SIP, создание и удаление.\n"
        "Набери /help для инструкции.",
        reply_markup=ReplyKeyboardRemove()
    )
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
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; [ssh_login] [ssh_password]</code>\n"
            "Примеры:\n"
            "• <code>/connect http://77.105.146.189 CID SECRET</code>\n"
            "• <code>/connect 77.105.146.189 CID SECRET root Very$trongPass</code>"
        )
        return

    raw_host, client_id, client_secret = c.args[0], c.args[1], c.args[2]
    ssh_login = c.args[3] if len(c.args) >= 4 else None
    ssh_password = " ".join(c.args[4:]) if len(c.args) >= 5 else None  # пароль может иметь пробелы

    parsed = urlparse(raw_host)
    if not parsed.scheme:
        base_url = f"http://{raw_host}"
        verify = False
        host_for_ssh = raw_host
    else:
        base_url = raw_host
        verify = not base_url.startswith("http://")
        host_for_ssh = parsed.hostname or raw_host

    await u.message.chat.send_action(ChatAction.TYPING)
    fb = FreePBX(base_url, client_id, client_secret, verify=verify)

    try:
        # авторизация
        fb.ensure_token()

        # собираем сессию
        sess = {
            "base_url": fb.base_url,
            "client_id": client_id,
            "client_secret": client_secret,
            "verify": verify,
            "token": fb.token,
            "token_exp": fb.token_exp,
        }
        if ssh_login and ssh_password:
            sess["ssh"] = {
                "host": host_for_ssh,
                "user": ssh_login,
                "password": ssh_password,
                "port": 22,
            }

        SESS[u.effective_chat.id] = sess
        c.user_data["__connected"] = True

        user_id = _uid(u)
        profiles = load_profiles_for(user_id)
        key = _profile_key(fb.base_url, client_id)
        is_new_profile = key not in profiles
        if is_new_profile:
            profiles[key] = {
                "base_url": fb.base_url,
                "client_id": client_id,
                "client_secret": client_secret,
                "verify": verify,
                "ssh": sess.get("ssh"),
                "label": f"{host_for_ssh} • {client_id[:6]}…",
            }
            save_profiles_for(user_id, profiles)


        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        text = _list_page_text(clean_url(fb.base_url), pairs_page)
        kb = _list_nav_kb(page, pages)

        # сообщения
        msg = [f"✅ Подключено к <code>{escape(fb.base_url)}</code>"]
        if "ssh" in sess:
            shown_user = escape(sess["ssh"]["user"])
            shown_host = escape(sess["ssh"]["host"])
            msg.append(f"🔐 SSH сохранён: <code>{shown_user}@{shown_host}</code>")
        if is_new_profile:
            msg.append("💾 Профиль подключений сохранён в разделе меню <b>🔗 Presets</b>.")

        await u.message.reply_text("\n".join(msg), parse_mode=ParseMode.HTML)

        await u.message.reply_text(
            "🏠 <b>Главное меню</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )

        await u.message.reply_text(text, reply_markup=kb)

    except Exception as e:
        await u.message.reply_text(f"Ошибка подключения: <code>{escape(str(e))}</code>")

async def connect_profile_by_key(u: Update, c: ContextTypes.DEFAULT_TYPE, key: str):
    user_id = _uid(u)
    profiles = load_profiles_for(user_id)
    prof = profiles.get(key)
    if not prof:
        msg = "❌ Профиль не найден (в ваших пресетах)."
        if getattr(u, "callback_query", None):
            try:
                await u.callback_query.answer(msg, show_alert=True)
            except Exception:
                pass
            return
        await u.effective_message.reply_text(msg)
        return

    base_url = prof["base_url"]
    client_id = prof["client_id"]
    client_secret = prof["client_secret"]
    verify = prof.get("verify", True)
    ssh = prof.get("ssh")

    await u.effective_message.chat.send_action(ChatAction.TYPING)
    fb = FreePBX(base_url, client_id, client_secret, verify=verify)
    try:
        fb.ensure_token()
        sess = {
            "base_url": fb.base_url,
            "client_id": client_id,
            "client_secret": client_secret,
            "verify": verify,
            "token": fb.token,
            "token_exp": fb.token_exp,
        }
        if ssh:
            sess["ssh"] = {
                "host": ssh.get("host"),
                "user": ssh.get("user"),
                "password": ssh.get("password"),
                "port": ssh.get("port", 22),
            }
        SESS[u.effective_chat.id] = sess
        c.user_data["__connected"] = True

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        text = _list_page_text(clean_url(fb.base_url), pairs_page)
        kb = _list_nav_kb(page, pages)

        msgs = [f"✅ Подключено к <code>{escape(fb.base_url)}</code>"]
        if "ssh" in sess:
            shown_user = escape(sess["ssh"]["user"] or "—")
            shown_host = escape(sess["ssh"]["host"] or "—")
            msgs.append(f"🔐 SSH: <code>{shown_user}@{shown_host}</code>")
        await u.effective_message.reply_text("\n".join(msgs), parse_mode=ParseMode.HTML)
        await u.effective_message.reply_text("🏠 <b>Главное меню</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_kb())
        await u.effective_message.reply_text(text, reply_markup=kb)
    except Exception as e:
        await u.effective_message.reply_text(f"Ошибка подключения: <code>{escape(str(e))}</code>")

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
    target = u.effective_message

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
    token = s.get("token", "<нет токена>")

    await target.reply_text(
        "👤 <b>Текущая сессия</b>\n"
        f"URL: <code>{s['base_url']}</code>\n"
        f"Client ID: <code>{s['client_id']}</code>\n"
        f"TLS verify: <code>{s['verify']}</code>\n"
        f"Токен жив ещё: <code>{ttl} сек</code>\n"
        f"Access Token:\n<code>{token}</code>",
        parse_mode=ParseMode.HTML
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

    def ext_to_slot(ext: str):
        try:
            n = int(ext)
            s = n % 100
            return s if 1 <= s <= 32 else None
        except Exception:
            return None

    try:
        await target.chat.send_action(ChatAction.TYPING)

        by_ext, _, _ = fb.fetch_ext_index()
        existing_exts = set(by_ext.keys())

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
            did_prefx = f"_sim{ext}"
            if ext in existing_dids or did_prefx in existing_dids:
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

            try:
                goip = goip_from_session(u.effective_chat.id)
                slots = sorted({ext_to_slot(x) for x in ok if ext_to_slot(x)})
                if slots:
                    done, errs = [], []
                    for s in slots:
                        ok1, msg1 = goip.set_incoming_enabled(s, True)
                        (done if ok1 else errs).append(str(s) if ok1 else f"{s} ({msg1})")
                        await asyncio.sleep(0)
                    lines = []
                    if done: lines.append("📲 GOIP: включены входящие для слотов: " + ", ".join(done))
                    if errs: lines.append("⚠️ GOIP: ошибки по слотам: " + ", ".join(errs))
                    if lines:
                        await target.reply_text("\n".join(lines))
                else:
                    await target.reply_text("ℹ️ GOIP: подходящих слотов (1..32) не найдено.")
            except Exception as e:
                await target.reply_text(f"⚠️ GOIP: не удалось применить изменения: <code>{escape(str(e))}</code>")

    except Exception as e:
        await target.reply_text(f"Ошибка /add_inbound: <code>{escape(str(e))}</code>")

async def del_inbound_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message

    if not c.args:
        await target.reply_text(
            "Использование: <code>/del_inbound &lt;ext|диапазоны&gt;</code>\n"
            "Примеры: <code>/del_inbound 414</code> или <code>/del_inbound 401-418</code> или <code>/del_inbound 401 402 410-418</code>",
            parse_mode=ParseMode.HTML
        )
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    raw_targets = " ".join(c.args)

    try:
        await target.chat.send_action(ChatAction.TYPING)

        exts = parse_targets(raw_targets)
        if not exts:
            await target.reply_text("❗ Не удалось распарсить цели. Пример: <code>/del_inbound 401 402 410-418</code>", parse_mode=ParseMode.HTML)
            return

        routes = fb.list_inbound_routes()
        route_by_did = { (r.get("extension") or "").strip(): r for r in routes if r.get("extension") }

        todo, missing = [], []
        for ext in exts:
            did_sim = f"_sim{ext}"
            r = route_by_did.get(did_sim) or route_by_did.get(ext)
            if r:
                shown = r.get("extension") or ext
                todo.append((shown, r, ext))
            else:
                missing.append(ext)

        if not todo:
            msg = ["Нечего удалять."]
            if missing:
                msg.append("↩️ Не найдены маршруты для: " + ", ".join(missing))
            await target.reply_text("\n".join(msg))
            return

        total = len(todo)
        notice = await target.reply_text(f"⏳ Удаляю Inbound Routes… (0/{total})")

        ok, failed = [], []
        for i, (shown, route, ext) in enumerate(todo, 1):
            try:
                res = fb.delete_inbound_route(route["id"])
                status = (res.get("status") if isinstance(res, dict) else None)
                msg = (res.get("message") if isinstance(res, dict) else "") or ""
                status_str = str(status).lower() if status is not None else ""
                okish = status_str in ("ok", "true") or "success" in msg.lower()
                (ok if okish else failed).append((shown, ext, msg or str(res)))
            except Exception as e:
                failed.append((shown, ext, str(e)))

            if i % 5 == 0 or i == total:
                try:
                    await notice.edit_text(f"⏳ Удаляю Inbound Routes… ({i}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        try:
            await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
        except Exception:
            pass
        try:
            fb.apply_config()
            try:
                await notice.edit_text("✅ Конфиг применён. Формирую отчёт…")
            except Exception:
                pass
        except Exception as e:
            try:
                await notice.edit_text(f"⚠️ Apply Config не удалось: {escape(str(e))}")
            except Exception:
                pass

        try:
            if ok:
                goip = goip_from_session(u.effective_chat.id)
                # импортируй один раз сверху файла:
                # from utils.common import _ext_to_slot
                slots = sorted({ _ext_to_slot(ext) for _, ext, _ in ok if _ext_to_slot(ext) })
                if slots:
                    done, errs = [], []
                    for s in slots:
                        ok1, msg1 = goip.set_incoming_enabled(s, False)
                        (done if ok1 else errs).append(str(s) if ok1 else f"{s} ({msg1})")
                        await asyncio.sleep(0)
                    if done:
                        await target.reply_text("📲 GOIP: выключены входящие для слотов: " + ", ".join(done))
                    if errs:
                        await target.reply_text("⚠️ GOIP: ошибки по слотам: " + ", ".join(errs))
                else:
                    await target.reply_text("ℹ️ GOIP: подходящих слотов (1..32) не найдено.")
        except Exception as e:
            await target.reply_text(f"⚠️ GOIP: не удалось применить изменения: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)

        # 7) сводка
        parts = []
        if ok:
            parts.append("✅ Удалены маршруты: " + ", ".join(shown for shown, _, _ in ok))
        if missing:
            parts.append("↩️ Не найдены (ни DID=_simEXT, ни DID=EXT): " + ", ".join(missing))
        if failed:
            parts.append("❌ Ошибки: " + ", ".join(f"{shown} ({msg[:60]})" for shown, _, msg in failed))
        await target.reply_text("\n".join(parts) if parts else "Нечего делать.")

    except Exception as e:
        await target.reply_text(f"Ошибка /del_inbound: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)

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
    
def goip_from_session(chat_id: int) -> GoIP:
    s = GOIP_SESS.get(chat_id)
    if s:
        obj = s.get("_obj")
        if isinstance(obj, GoIP):
            return obj
        url = s.get("base_url")
        login = s.get("login")
        pwd = s.get("password")
        verify = bool(s.get("verify", False))
        if url and login and pwd:
            obj = GoIP(url, login, pwd, verify=verify, timeout=8)
            s["_obj"] = obj  # кэшируем
            return obj

    s2 = SESS.get(chat_id) or {}
    obj2 = s2.get("goip") or s2.get("GOIP")
    if isinstance(obj2, GoIP):
        return obj2

    raise RuntimeError("GOIP не подключен. Сначала /goip_connect <url> <login> <password>")

async def goip_connect_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /goip_connect <url> <login> <password>
    [--radmin <url> <login> <password>]

    Примеры:
    /goip_connect http://185.90.162.63:38017 admin admin3№
    /goip_connect http://185.90.162.63:38017 admin admin3№ --radmin http://185.90.162.63:8086 BaraGeniy BaraVH&SiP!@#
    """
    target = u.effective_message
    if len(c.args) < 3:
        await target.reply_text(
            "Форматы:\n"
            "<code>/goip_connect &lt;url&gt; &lt;login&gt; &lt;password&gt;</code>\n"
            "<code>/goip_connect &lt;url&gt; &lt;login&gt; &lt;password&gt; --radmin &lt;url&gt; &lt;login&gt; &lt;password&gt;</code>"
        )
        return

    # парсим аргументы
    raw_args = c.args
    if "--radmin" in raw_args:
        idx = raw_args.index("--radmin")
        goip_args = raw_args[:idx]
        radmin_args = raw_args[idx+1:]
    else:
        goip_args, radmin_args = raw_args, None

    if len(goip_args) < 3:
        await target.reply_text("Недостаточно аргументов для GOIP.")
        return

    raw_url, login, password = goip_args[0], goip_args[1], " ".join(goip_args[2:])
    verify = not raw_url.startswith("http://")

    try:
        if radmin_args and len(radmin_args) >= 3:
            rurl, rlogin, rpass = radmin_args[0], radmin_args[1], " ".join(radmin_args[2:])
            ok, info = GoIP.warmup_radmin(rurl, rlogin, rpass, verify=not rurl.startswith("http://"))
            await target.reply_text(("✅ " if ok else "⚠️ ") + f"Radmin warmup: {escape(info)}")

        goip = GoIP(raw_url, login, password, verify=verify)
        state, msg, code = goip.check_status()

        # сохраняем сессию
        GOIP_SESS[u.effective_chat.id] = {
            "base_url": goip.base_url,
            "login": login,
            "password": password,
            "verify": verify,
            "_obj": goip,
        }

        if state == GoipStatus.READY:
            await target.reply_text(f"✅ GOIP подключена: <code>{goip.status_url}</code>\n{msg}")
            if c.job_queue:
                job_name = f"goip_watch_{u.effective_chat.id}"
                for j in c.job_queue.jobs() or []:
                    if j.name == job_name:
                        j.schedule_removal()
                c.job_queue.run_repeating(
                    _goip_periodic_check,
                    interval=120,
                    first=0,
                    name=job_name,
                    chat_id=u.effective_chat.id
                )

        elif state == GoipStatus.UNAUTHORIZED:
            await target.reply_text(f"❌ Авторизация не удалась: {msg}")
        else:
            await target.reply_text(f"⚠️ Проверка выполнена, но есть проблемы: {msg}")

    except Exception as e:
        await target.reply_text(f"Ошибка подключения GOIP: <code>{escape(str(e))}</code>")

async def goip_ping_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message
    try:
        goip = goip_from_session(u.effective_chat.id)
        state, msg, code = goip.check_status()
        prefix = "✅" if state == GoipStatus.READY else ("❌" if state == GoipStatus.UNAUTHORIZED else "⚠️")
        await target.reply_text(f"{prefix} {msg} (HTTP {code or '—'})\nURL: <code>{goip.status_url}</code>")
    except Exception as e:
        await target.reply_text(f"Ошибка /goip_ping: <code>{escape(str(e))}</code>")

async def goip_whoami_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message
    s = GOIP_SESS.get(u.effective_chat.id)
    if not s:
        await target.reply_text("Нет активной GOIP-сессии. Используйте /goip_connect.")
        return
    await target.reply_text(
        "📟 Текущая GOIP-сессия\n"
        f"URL: <code>{s['base_url']}</code>\n"
        f"Login: <code>{escape(s['login'])}</code>\n"
        f"TLS verify: <code>{s['verify']}</code>"
    )

async def _goip_periodic_check(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    s = GOIP_SESS.get(chat_id)
    if not s:
        return

    goip = GoIP(s["base_url"], s["login"], s["password"], verify=s["verify"])
    state, msg, code = goip.check_status()

    prev = GOIP_STATE_CACHE.get(chat_id)
    if state != prev:
        GOIP_STATE_CACHE[chat_id] = state
        prefix = "✅" if state == GoipStatus.READY else ("❌" if state == GoipStatus.UNAUTHORIZED else "⚠️")
        try:
            await context.bot.sendMessage(
                chat_id=chat_id,
                text=f"{prefix} GOIP статус изменился: {msg} (HTTP {code or '—'})\nURL: <code>{goip.status_url}</code>"
            )
        except Exception:
            pass

async def goip_start_watch_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.id not in GOIP_SESS:
        await u.effective_message.reply_text("Сначала /goip_connect.")
        return
    job_name = f"goip_watch_{u.effective_chat.id}"
    for j in c.job_queue.jobs() or []:
        if j.name == job_name:
            j.schedule_removal()
    c.job_queue.run_repeating(_goip_periodic_check, interval=120, first=0, name=job_name, chat_id=u.effective_chat.id)
    await u.effective_message.reply_text("🔎 Мониторинг GOIP запущен (каждые 2 минуты).")
    
async def goip_in_on_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        await u.effective_message.reply_text("Использование: /goip_in_on <slot>")
        return
    slot = int(c.args[0])
    goip = goip_from_session(u.effective_chat.id)
    ok, msg = goip.set_incoming_enabled(slot, True)
    await u.effective_message.reply_text(("✅ " if ok else "❌ ") + msg)

async def goip_in_off_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        await u.effective_message.reply_text("Использование: /goip_in_off <slot>")
        return
    slot = int(c.args[0])
    goip = goip_from_session(u.effective_chat.id)
    ok, msg = goip.set_incoming_enabled(slot, False)
    await u.effective_message.reply_text(("✅ " if ok else "❌ ") + msg)
    
async def goip_debug_config_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    goip = goip_from_session(u.effective_chat.id)
    ok, msg = goip.fetch_config_page()
    if ok:
        await u.effective_message.reply_text(
            "✅ Удалось получить config.html:\n\n<pre>" + 
            msg[:3500].replace("<", "&lt;").replace(">", "&gt;") + 
            "</pre>",
            parse_mode="HTML"
        )
    else:
        await u.effective_message.reply_text("❌ " + msg)        

async def goip_detect_ip_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /goip_detect_ip
    Берёт SSH-хост/логин/пароль из сохранённой сессии (/connect ... ssh_login ssh_password)
    Эндпоинты по умолчанию: goip32sell / goip32sell_incoming
    """
    target = u.effective_message

    # достаём SSH из сессии
    s = SESS.get(u.effective_chat.id) or {}
    ssh = s.get("ssh")
    if not ssh:
        await target.reply_text(
            "❌ SSH-доступ не сохранён.\n"
            "Подключитесь командой:\n"
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; &lt;ssh_login&gt; &lt;ssh_password&gt;</code>"
        )
        return

    ssh_host = ssh.get("host")
    ssh_login = ssh.get("user")
    ssh_password = ssh.get("password")

    endpoint_primary = "goip32sell"
    endpoint_incoming = "goip32sell_incoming"

    try:
        await target.chat.send_action(ChatAction.TYPING)

        best_ip, ips = fetch_goip_ips_via_ssh(
            host=ssh_host,
            username=ssh_login,
            password=ssh_password,
            endpoint_primary=endpoint_primary,
            endpoint_incoming=endpoint_incoming,
        )

        if not ips:
            await target.reply_text(
                "❌ Не удалось обнаружить IP в PJSIP.\n"
                "Проверьте, что endpoints существуют и Asterisk сейчас видит контакты."
            )
            return

        lines = [f"🔎 Обнаружены IP (по порядку приоритета): {', '.join(ips)}"]
        lines.append(f"✅ Текущий (best): <b>{best_ip}</b>")
        lines.append(
            f"Endpoints: <code>{endpoint_primary}</code> / <code>{endpoint_incoming}</code>"
        )
        await target.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as e:
        await target.reply_text(f"Ошибка detect: <code>{escape(str(e))}</code>")
      
async def pjsip_endpoints_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /pjsip_endpoints <ssh_host> <ssh_login> <ssh_password> [filter]
    Примеры:
      /pjsip_endpoints 185.90.162.63 root mypass
      /pjsip_endpoints https://185.90.162.63 root mypass goip
    """
    target = u.effective_message
    if len(c.args) < 3:
        await target.reply_text(
            "Использование:\n"
            "<code>/pjsip_endpoints &lt;ssh_host&gt; &lt;ssh_login&gt; &lt;ssh_password&gt; [filter]</code>\n"
            "Пример: <code>/pjsip_endpoints 185.90.162.63 root S3cr3t goip</code>\n"
            "Поддерживается <code>http://</code> и <code>https://</code> в хосте — я их проигнорирую для SSH."
        )
        return

    ssh_host = c.args[0]
    ssh_login = c.args[1]
    ssh_password = c.args[2]
    flt = c.args[3].lower() if len(c.args) >= 4 else None

    try:
        await target.chat.send_action(ChatAction.TYPING)
        names = fetch_pjsip_endpoints_via_ssh(ssh_host, ssh_login, ssh_password)
        if flt:
            names = [n for n in names if flt in n.lower()]
        if not names:
            await target.reply_text("Не найдено ни одного endpoint’а (по заданным условиям).")
            return

        # аккуратно порежем на чанки, чтобы не превысить лимиты Telegram
        header = "📋 PJSIP endpoints:\n"
        chunk, acc = [], len(header)
        for name in names:
            line = f"- {name}\n"
            if acc + len(line) > 3500:
                await target.reply_text(header + "".join(chunk))
                chunk, acc = [], len(header)
            chunk.append(line); acc += len(line)
        if chunk:
            await target.reply_text(header + "".join(chunk))

        # подсказка следующего шага
        tip = "Чтобы посмотреть детали: <code>/pjsip_show &lt;host&gt; &lt;login&gt; &lt;pass&gt; &lt;endpoint&gt;</code>"
        await target.reply_text(tip)
    except Exception as e:
        await target.reply_text(f"Ошибка /pjsip_endpoints: <code>{escape(str(e))}</code>")

async def pjsip_show_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /pjsip_show <ssh_host> <ssh_login> <ssh_password> <endpoint>
    Пример:
      /pjsip_show 185.90.162.63 root S3cr3t goip32sell
    """
    target = u.effective_message
    if len(c.args) < 4:
        await target.reply_text(
            "Использование:\n"
            "<code>/pjsip_show &lt;ssh_host&gt; &lt;ssh_login&gt; &lt;ssh_password&gt; &lt;endpoint&gt;</code>"
        )
        return

    ssh_host = c.args[0]
    ssh_login = c.args[1]
    ssh_password = c.args[2]
    endpoint = c.args[3]

    try:
        await target.chat.send_action(ChatAction.TYPING)
        raw = fetch_endpoint_raw_via_ssh(ssh_host, ssh_login, ssh_password, endpoint)
        # обрежем до 3500 символов и экранируем
        shown = raw[:3500].replace("<", "&lt;").replace(">", "&gt;")
        suffix = "" if len(raw) <= 3500 else "\n\n…(обрезано)"
        await target.reply_text(
            f"🔎 <b>pjsip show endpoint {escape(endpoint)}</b>\n\n<pre>{shown}</pre>{suffix}",
            parse_mode=ParseMode.HTML
        )
        # маленькая подсказка, как получить IP после просмотра
        await target.reply_text(
            "Подсказка: когда найдём правильный endpoint, можно будет использовать авто-вывод IP через /goip_detect_ip."
        )
    except Exception as e:
        await target.reply_text(f"Ошибка /pjsip_show: <code>{escape(str(e))}</code>")
        
async def set_incoming_sip_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /set_incoming_sip
    Автоматически:
      1) Берёт SSH-хост/логин/пароль из сессии (/connect ... ssh_login ssh_password)
      2) Детектит актуальный IP GOIP из Asterisk (pjsip show endpoint/contacts)
      3) Обновляет pjsip.sip_server у транка goip32sell_incoming на найденный IP
      4) Делает fwconsole reload
    """
    target = u.effective_message

    # 0) Проверим SSH в сессии
    s = SESS.get(u.effective_chat.id) or {}
    ssh = s.get("ssh")
    if not ssh:
        await target.reply_text(
            "❌ SSH-доступ не сохранён. Подключитесь командой:\n"
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; &lt;ssh_login&gt; &lt;ssh_password&gt;</code>"
        )
        return

    ssh_host = ssh.get("host")
    ssh_login = ssh.get("user")
    ssh_password = ssh.get("password")

    endpoint_primary = "goip32sell"
    endpoint_incoming = "goip32sell_incoming"

    try:
        await target.chat.send_action(ChatAction.TYPING)

        # 1) Детект IP так же, как делает /goip_detect_ip
        best_ip, ips = fetch_goip_ips_via_ssh(
            host=ssh_host,
            username=ssh_login,
            password=ssh_password,
            endpoint_primary=endpoint_primary,
            endpoint_incoming=endpoint_incoming,
        )

        if not ips or not best_ip:
            await target.reply_text(
                "❌ Не удалось обнаружить IP в PJSIP.\n"
                "Убедитесь, что endpoints существуют и Asterisk сейчас видит контакты."
            )
            return

        # лёгкая валидация найденного IP
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", best_ip):
            await target.reply_text(
                f"❌ Детектирован непохожий на IPv4 адрес: <code>{escape(best_ip)}</code>"
            )
            return

        # 2) Обновляем sip_server у goip32sell_incoming
        report = set_incoming_trunk_sip_server_via_ssh(
            host=ssh_host,
            username=ssh_login,
            password=ssh_password,
            trunk_name=endpoint_incoming,
            new_ip=best_ip,
        )

        # 3) Отчёт
        lines = []
        if ips:
            lines.append("🔎 Обнаружены IP (по приоритету): " + ", ".join(ips))
        lines.append(f"✅ Выбран (best): <b>{best_ip}</b>")
        lines.append(
            "✅ Обновлено поле <b>sip_server</b> у транка "
            f"<code>{endpoint_incoming}</code>\n"
            f"ID: <code>{report.get('trunk_id')}</code>\n"
            f"Старое значение: <code>{escape(report.get('old_value', '') or '<empty>')}</code>\n"
            f"Новое значение: <code>{escape(report.get('new_value', '') or '<empty>')}</code>\n"
            "🔄 Выполнен <code>fwconsole reload</code>."
        )
        await target.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    except SSHExecError as e:
        await target.reply_text(f"❌ SSH/Bash ошибка: <code>{escape(str(e))}</code>")
    except Exception as e:
        await target.reply_text(f"❌ Ошибка: <code>{escape(str(e))}</code>")

async def set_secret_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message

    if not c.args:
        await target.reply_text(
            "Использование:\n"
            "<code>/set_secret &lt;ext|targets&gt; [fixed_pass] [--also-ext]</code>\n"
            "Примеры:\n"
            "<code>/set_secret 301</code>\n"
            "<code>/set_secret 301 MyPass --also-ext</code>\n"
            "<code>/set_secret 401-418</code>\n"
            "<code>/set_secret 401 402 410-418 MyOnePass --also-ext</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not await _ensure_connected(u):
        return

    # ---- Парсинг аргументов: цели, общий пароль (опц.), флаг also-ext
    raw = c.args[:]
    also_ext = False
    if "--also-ext" in raw:
        also_ext = True
        raw.remove("--also-ext")

    target_tokens = []
    fixed_pass = None
    for tok in raw:
        if re.fullmatch(r"\d+(-\d+)?", tok):
            target_tokens.append(tok)
        else:
            fixed_pass = tok
            # остальное игнорируем (кроме ранее снятого --also-ext)
            break

    if not target_tokens:
        await target.reply_text(
            "❗ Укажи хотя бы один EXT или диапазон. Пример: <code>/set_secret 401 402 410-418</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # Список EXT
    exts = parse_targets(" ".join(target_tokens))
    if not exts:
        await target.reply_text("❗ Не удалось распарсить цели.", parse_mode=ParseMode.HTML)
        return

    # ---- SSH из сессии
    s = SESS.get(u.effective_chat.id) or {}
    ssh = s.get("ssh")
    if not ssh:
        await target.reply_text(
            "❌ SSH-доступ не сохранён. Подключитесь:\n"
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; &lt;ssh_login&gt; &lt;ssh_password&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    ssh_host, ssh_user, ssh_pass = ssh.get("host"), ssh.get("user"), ssh.get("password")

    total = len(exts)
    notice = await target.reply_text(f"⏳ Меняю пароли… (0/{total})")
    try:
        await target.chat.send_action(ChatAction.TYPING)

        # Один проход: пишем secret без reload; reload сделаем один раз в конце
        reports = []   # для случая одного EXT — покажем подробный отчёт
        ok, failed = [], []

        async def _progress(i):
            if i % 5 == 0 or i == total:
                try:
                    await notice.edit_text(f"⏳ Меняю пароли… ({i}/{total})")
                except Exception:
                    pass

        for i, ext in enumerate(exts, 1):
            try:
                pwd = fixed_pass or _gen_secret()
                rep = set_extension_chansip_secret_via_ssh(
                    host=ssh_host, username=ssh_user, password=ssh_pass,
                    extension=ext, new_secret=pwd,
                    do_reload=False  # важн.: единый reload в конце
                )
                reports.append((rep, pwd))
                # опциональная синхронизация в GraphQL
                if also_ext:
                    try:
                        fb = fb_from_session(u.effective_chat.id)
                        fb.set_ext_password(ext, pwd)
                    except Exception:
                        pass
                ok.append(ext)
            except Exception as e:
                failed.append(f"{ext} ({str(e)[:60]})")

            await _progress(i)
            await asyncio.sleep(0)

        # ЕДИНЫЙ reload
        try:
            await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
        except Exception:
            pass
        _ssh_run(ssh_host, ssh_user, ssh_pass, "fwconsole reload", timeout=30)

        # Итог
        if total == 1 and reports:
            # подробный отчёт как раньше
            rep, _pwd = reports[0]
            tech = rep.get("tech") or "chan_sip"
            old_val = (rep.get("old_value") or "")[:70]
            new_val = rep.get("new_value") or ""
            lines = [
                f"🔐 <b>Extension {escape(rep['ext'])}</b> (<code>{escape(tech)}</code>)",
                f"Старый пароль: <code>{escape(old_val) or '&lt;empty&gt;'}</code>",
                f"Новый пароль: <code>{escape(new_val)}</code>",
                "✅ Применён <code>fwconsole reload</code>.",
            ]
            if rep.get("md5_present"):
                lines.append("⚠️ Обнаружен <code>md5_cred</code>. Клиент/GUI могут использовать MD5 вместо обычного пароля.")
            if also_ext:
                lines.append("🔁 Также обновлён <code>extPassword</code> пользователя (GraphQL).")
            await target.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        else:
            # сводка по батчу
            try:
                await notice.edit_text("✅ Конфиг применён. Формирую отчёт…", parse_mode=ParseMode.HTML)
            except Exception:
                pass
            parts = []
            if ok:
                parts.append("✅ Обновлены: " + ", ".join(ok))
            if failed:
                parts.append("❌ Ошибки: " + ", ".join(failed))
            if not parts:
                parts.append("Нечего делать.")
            if also_ext:
                parts.append("ℹ️ Также синхронизирован <code>extPassword</code> для успешных EXT.")
            await target.reply_text("\n".join(parts), parse_mode=ParseMode.HTML)

    except SSHExecError as e:
        try:
            await notice.edit_text("❌ Ошибка при смене паролей.")
        except Exception:
            pass
        await target.reply_text(f"❌ SSH/SQL ошибка: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        try:
            await notice.edit_text("❌ Ошибка при смене паролей.")
        except Exception:
            pass
        await target.reply_text(f"❌ Ошибка: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)

async def radmin_restart_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    target = u.effective_message

    s = SESS.get(u.effective_chat.id) or {}
    ssh = s.get("ssh")
    if not ssh:
        await target.reply_text(
            "❌ SSH-доступ не сохранён. Подключитесь:\n"
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; &lt;ssh_login&gt; &lt;ssh_password&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    host, user, pwd = ssh.get("host"), ssh.get("user"), ssh.get("password")

    notice = await target.reply_text("⏳ Останавливаю radmsrv…")
    try:
        await target.chat.send_action(ChatAction.TYPING)

        stop_out = ""
        try:
            stop_out = _ssh_run(host, user, pwd, "killall radmsrv || true", timeout=10)
        except Exception as e:
            stop_out = str(e)

        try:
            await notice.edit_text("⏳ Запускаю radmsrv…")
        except Exception:
            pass

        start_cmd = r"nohup /root/radmsrv/run_radmsrv >/dev/null 2>&1 & echo $!"
        pid = _ssh_run(host, user, pwd, start_cmd, timeout=10).strip()

        await asyncio.sleep(1.0)
        ps = _ssh_run(host, user, pwd, "pgrep -fa radmsrv | head -n 3", timeout=10).strip()

        txt = [
            "✅ <b>radmsrv перезапущен</b>",
            f"PID: <code>{escape(pid or '—')}</code>",
        ]
        if ps:
            txt.append("<b>Процессы:</b>\n<pre>" + escape(ps[:1200]) + "</pre>")
        await notice.edit_text("\n".join(txt), parse_mode=ParseMode.HTML)

    except SSHExecError as e:
        try:
            await notice.edit_text("❌ Ошибка SSH.")
        except Exception:
            pass
        await target.reply_text(f"❌ SSH ошибка: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        try:
            await notice.edit_text("❌ Не удалось перезапустить radmsrv.")
        except Exception:
            pass
        await target.reply_text(f"❌ Ошибка: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)

async def add_outbound_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /add_outbound <name> <prepend_range> [--cid <range>] [--trunks <name1,name2,...>] [--p1 X.] [--p2 XXXX]
    Примеры:
      /add_outbound test 001-032 --cid 001-032 --trunks goip32sell
      /add_outbound test 001-032 --trunks goip32sell,backuptrunk
      /add_outbound test 001-032 --p1 X. --p2 XXXX
    По умолчанию: callerid_range = prepend_range; p1='X.'; p2='XXXX'
    Пачка 1: prepend='NNN+' / pattern=p1
    Пачка 2: prepend='NNN'   / pattern=p2
    """
    target = u.effective_message
    if not await _ensure_connected(u):
        return

    if len(c.args) < 2:
        await target.reply_text(
            "Использование:\n"
            "<code>/add_outbound &lt;name&gt; &lt;prepend_range&gt; [--cid &lt;range&gt;] [--trunks name1,name2] [--p1 X.] [--p2 XXXX]</code>\n"
            "Пример: <code>/add_outbound test 001-032 --cid 001-032 --trunks goip32sell</code>"
        )
        return

    name = c.args[0]
    prepend_range = c.args[1]

    # дефолты
    cid_range = None
    trunks = []
    p1 = "X."
    p2 = "XXXX"

    # разбор флагов
    raw = c.args[2:]
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok == "--cid" and i + 1 < len(raw):
            cid_range = raw[i+1]; i += 2
        elif tok == "--trunks" and i + 1 < len(raw):
            trunks = [x.strip() for x in raw[i+1].split(",") if x.strip()]
            i += 2
        elif tok == "--p1" and i + 1 < len(raw):
            p1 = raw[i+1]; i += 2
        elif tok == "--p2" and i + 1 < len(raw):
            p2 = raw[i+1]; i += 2
        else:
            i += 1

    # SSH из сессии
    s = SESS.get(u.effective_chat.id) or {}
    ssh = s.get("ssh")
    if not ssh:
        await target.reply_text(
            "❌ SSH-доступ не сохранён. Подключитесь:\n"
            "<code>/connect &lt;host&gt; &lt;client_id&gt; &lt;client_secret&gt; &lt;ssh_login&gt; &lt;ssh_password&gt;</code>"
        )
        return
    ssh_host, ssh_user, ssh_pass = ssh.get("host"), ssh.get("user"), ssh.get("password")

    notice = await target.reply_text("⏳ Создаю outbound route…")
    try:
        await target.chat.send_action(ChatAction.TYPING)
        rep = create_outbound_route_with_ranges_via_ssh(
            host=ssh_host,
            username=ssh_user,
            password=ssh_pass,
            route_name=name,
            prepend_range=prepend_range,
            callerid_range=cid_range,
            pattern_first=p1,
            pattern_second=p2,
            trunk_names=trunks or None,
        )
        txt = [
            f"✅ Создан маршрут: <b>{escape(rep['route_name'])}</b> (ID: <code>{escape(rep['route_id'])}</code>)",
            f"➕ Добавлено Dial Patterns: <code>{rep['patterns_created']}</code>",
        ]
        if rep.get("trunks_bound"):
            txt.append("🔗 Привязаны транки: " + ", ".join(rep["trunks_bound"]))
        txt.append("🔄 Выполнен <code>fwconsole reload</code>.")
        await notice.edit_text("\n".join(txt), parse_mode=ParseMode.HTML)
    except SSHExecError as e:
        try: await notice.edit_text("❌ Ошибка при создании маршрута.")
        except Exception: pass
        await target.reply_text(f"SSH/SQL: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        try: await notice.edit_text("❌ Ошибка при создании маршрута.")
        except Exception: pass
        await target.reply_text(f"Ошибка: <code>{escape(str(e))}</code>", parse_mode=ParseMode.HTML)


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
            await app.bot.set_chat_menu_button()
        except Exception:
            pass
    except Exception as e:
        print(f"set_my_commands failed: {e}")