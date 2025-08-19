import os, re, time, secrets, logging, requests, asyncio
from typing import Dict, List, Tuple, Optional
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, Defaults, CallbackQueryHandler
from telegram.constants import ParseMode, ChatAction
from html import escape
from urllib.parse import urlparse

# ==== SETTINGS ====
TELEGRAM_TOKEN = "8495279314:AAHKzXlhMfj_nojN0f5jTNtU9KGf9rHEU0A"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger(__name__)

SESS: Dict[int, dict] = {}
PAGE_SIZE = 50
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
    "  <code>/add_inbound &lt;ext&gt;</code> или <code>/add_inbound &lt;start-end&gt;</code>\n"
    "  • Создаёт маршрут DID→EXT, Description=simEXT\n\n"

)

class FreePBX:
    def __init__(self, base_url: str, client_id: str, client_secret: str, verify: bool = True):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.verify = verify
        self.token = None
        self.token_exp = 0

    @property
    def token_url(self):
        return f"{self.base_url}/admin/api/api/token"

    @property
    def gql_url(self):
        return f"{self.base_url}/admin/api/api/gql"

    def ensure_token(self):
        now = time.time()
        if self.token and now < self.token_exp - 30:
            return
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "gql gql:core",
        }
        r = requests.post(self.token_url, data=data, timeout=25, verify=self.verify)
        r.raise_for_status()
        j = r.json()
        self.token = j["access_token"]
        self.token_exp = now + int(j.get("expires_in", 3600))

    def gql(self, query: str, variables: Optional[dict] = None) -> dict:
        self.ensure_token()
        h = {"Authorization": f"Bearer {self.token}"}
        r = requests.post(
            self.gql_url,
            json={"query": query, "variables": variables or {}},
            timeout=35,
            verify=self.verify,
            headers=h,
        )
        r.raise_for_status()
        js = r.json()
        if "errors" in js:
            raise RuntimeError(js["errors"])
        return js["data"]

    def fetch_all_extensions(self) -> List[Tuple[str, str]]:
        q_full = """
        query {
          fetchAllExtensions {
            extension {
              extensionId
              tech
              pjsip { secret }
              user { password extPassword }
            }
          }
        }
        """
        q_fallback = """
        query {
          fetchAllExtensions {
            extension {
              extensionId
              user { password extPassword }
            }
          }
        }
        """
        try:
            data = self.gql(q_full)
            exts = data["fetchAllExtensions"]["extension"]
        except Exception:
            data = self.gql(q_fallback)
            exts = data["fetchAllExtensions"]["extension"]

        out = []
        for e in exts:
            ext = str(e["extensionId"])
            u = e.get("user") or {}
            pw = u.get("extPassword") or (e.get("pjsip", {}) or {}).get("secret") or u.get("password") or ""
            out.append((ext, pw))
        out.sort(key=lambda x: int(re.sub(r"\D", "", x[0]) or 0))
        return out

    def fetch_ext_index(self):
        queries = [
            """
            query {
              fetchAllExtensions {
                extension {
                  extensionId
                  user { extPassword name displayname }
                }
              }
            }
            """,
            """
            query {
              fetchAllExtensions {
                extension {
                  extensionId
                  user { password }
                }
              }
            }
            """,
            """
            query {
              fetchAllExtensions {
                extension { extensionId }
              }
            }
            """,
        ]

        def pick_name(e: dict) -> str:
            u = e.get("user") or {}
            for k in ("name", "displayname", "username"):
                v = u.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return ""

        for q in queries:
            try:
                data = self.gql(q)
                exts = data["fetchAllExtensions"]["extension"]
                by_ext, name_set = {}, set()
                for e in exts:
                    ext = str(e["extensionId"])
                    name = pick_name(e)
                    by_ext[ext] = {"name": name, "pw": ""}  
                    if name:
                        name_set.add(name.lower())
                return by_ext, name_set, bool(name_set)  
            except Exception:
                continue

        return {}, set(), False

    def delete_extension(self, extension: str) -> None:
        ext_str = str(extension)
        variants = [
            ("ID",     "id",           "input"),
            ("String", "id",           "input"),
            ("ID",     "extensionId",  "input"),
            ("String", "extensionId",  "input"),
            ("ID",     "extension",    "input"),
            ("String", "extension",    "input"),
            ("ID",     "extId",        "input"),
            ("String", "extId",        "input"),
            ("ID",     "id",           "direct"),
            ("String", "id",           "direct"),
            ("ID",     "extensionId",  "direct"),
            ("String", "extensionId",  "direct"),
            ("ID",     "extension",    "direct"),
            ("String", "extension",    "direct"),
            ("ID",     "extId",        "direct"),
            ("String", "extId",        "direct"),
        ]
        last_err = None
        for typ, field, mode in variants:
            if mode == "input":
                m = f"""
                mutation($ext: {typ}!) {{
                  deleteExtension(input: {{ {field}: $ext }}) {{ status message }}
                }}
                """
            else:
                m = f"""
                mutation($ext: {typ}!) {{
                  deleteExtension({field}: $ext) {{ status message }}
                }}
                """
            try:
                self.gql(m, {"ext": ext_str})
                return
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"deleteExtension failed (all variants): {last_err}")

    def create_one(self, ext: int, name: Optional[str] = None) -> None:
        m = """
        mutation($start: ID!, $name: String!, $email: String!) {
          createRangeofExtension(input:{
            startExtension: $start,
            numberOfExtensions: 1,
            tech: "pjsip",
            name: $name,
            email: $email,
            vmEnable: true,
            umEnable: true
          }) {
            status
            message
          }
        }
        """
        nm = str(name).strip() if (name and str(name).strip()) else str(ext)
        vars = {
            "start": str(ext),
            "name": nm,
            "email": f"{ext}@local",
        }
        self.gql(m, vars)

    def set_ext_password(self, extension: str, secret: str) -> None:
        m_id = """
        mutation($extId: ID!, $name: String!, $pwd: String!) {
          updateExtension(input: {
            extensionId: $extId,
            name: $name,
            extPassword: $pwd
          }) { status message }
        }
        """
        m_str = """
        mutation($extId: String!, $name: String!, $pwd: String!) {
          updateExtension(input: {
            extensionId: $extId,
            name: $name,
            extPassword: $pwd
          }) { status message }
        }
        """
        vars_id = {"extId": str(extension), "name": str(extension), "pwd": secret}
        try:
            self.gql(m_id, vars_id)
        except Exception as e1:
            vars_str = {"extId": str(extension), "name": str(extension), "pwd": secret}
            try:
                self.gql(m_str, vars_str)
            except Exception as e2:
                raise RuntimeError(f"updateExtension failed: ID! -> {e1}; String! -> {e2}")

    # === НОВОЕ: Apply Config (перезагрузка конфигурации через веб-интерфейс) ===
    def apply_config(self) -> dict:
        gql_mutation = """
        mutation {
            doreload(input: {}) {
            status
            message
            transaction_id
            }
        }
        """
        try:
            data = self.gql(gql_mutation)
            return data.get("doreload") or {"status": True, "message": "doreload ok"}
        except Exception as e1:
            url = f"{self.base_url}/admin/ajax.php"
            try:
                r = requests.get(url, params={"command": "reload"}, timeout=25, verify=self.verify)
                r.raise_for_status()
                try:
                    return {"status": True, "message": str(r.json())[:400]}
                except ValueError:
                    return {"status": True, "message": r.text[:400]}
            except Exception as e2:
                raise RuntimeError(f"Apply Config failed: GraphQL doreload -> {e1}; ajax reload -> {e2}")
            
    def create_inbound_route(self, did: str, description: str, ext: str) -> None:

        did = str(did).strip()
        description = str(description).strip()
        ext = str(ext).strip()

        mutations = [
            # Основной вариант: from-did-direct
            ("""
            mutation($did:String!, $desc:String!, $dest:String!) {
              addInboundRoute(input:{
                extension: $did,
                description: $desc,
                destination: $dest
              }) {
                status
                message
                inboundRoute { id }
              }
            }""", {"did": did, "desc": description, "dest": f"from-did-direct,{ext},1"}),

            # Альтернатива: ext-local (на некоторых системах назначение расширения через ext-local)
            ("""
            mutation($did:String!, $desc:String!, $dest:String!) {
              addInboundRoute(input:{
                extension: $did,
                description: $desc,
                destination: $dest
              }) {
                status
                message
                inboundRoute { id }
              }
            }""", {"did": did, "desc": description, "dest": f"ext-local,{ext},1"}),
        ]

        last_err = None
        for m, vars_ in mutations:
            try:
                self.gql(m, vars_)
                return
            except Exception as e:
                last_err = e
                continue

        # Делаем сообщение более понятным, если у инстанса нет самой мутации
        msg = str(last_err)
        if "Cannot query field" in msg and "addInboundRoute" in msg:
            raise RuntimeError(
                "На этой версии FreePBX отсутствует мутация addInboundRoute. "
                "Обнови модули framework/core/api до последних версий (edge) и повтори попытку."
            )
        raise RuntimeError(f"create_inbound_route failed: {last_err}")

# ===== Helpers =====
def equip_start(eq: int) -> int:
    # 1->101, 2->201, 3->301, 4->401, 10->1001
    return eq * 100 + 1

def parse_targets(s: str) -> List[str]:
    # "401 402 410-418"
    out = []
    for tok in s.strip().split():
        if "-" in tok:
            a, b = tok.split("-", 1)
            for n in range(int(a), int(b) + 1):
                out.append(str(n))
        else:
            out.append(tok)
    return sorted(set(out), key=lambda x: int(x))

def next_free(existing: List[str], start: int, count: int) -> List[str]:
    taken = set(map(int, existing))
    res, cur = [], start
    while len(res) < count:
        if cur not in taken:
            res.append(str(cur))
        cur += 1
    return res

def format_list(ip: str, pairs: List[Tuple[str, str]]) -> str:
    if not pairs:
        return f"{ip}\n\n(пусто)"
    lines = [ip, ""]
    lines += [f"{ext} {pw}" for ext, pw in pairs]
    return "\n".join(lines)

def fb_from_session(chat_id: int) -> FreePBX:
    s = SESS.get(chat_id)
    if not s:
        raise RuntimeError("Сначала /connect <ip> <login> <password>")
    fb = FreePBX(s["base_url"], s["client_id"], s["client_secret"], verify=s["verify"])
    fb.token = s.get("token"); fb.token_exp = s.get("token_exp", 0)
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

def _list_page_text(ip: str, pairs_page):
    if not pairs_page:
        return f"{ip}\n\n(пусто)"
    lines = [ip, ""]
    lines += [f"{ext} {pw}" for ext, pw in pairs_page]
    return "\n".join(lines)

def _list_nav_kb(page: int, pages: int):
    prev_btn = InlineKeyboardButton("⬅️", callback_data=f"list:page:{page-1}")
    next_btn = InlineKeyboardButton("➡️", callback_data=f"list:page:{page+1}")
    nums = InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop")
    row = []
    if page > 0: row.append(prev_btn)
    row.append(nums)
    if page < pages-1: row.append(next_btn)
    return InlineKeyboardMarkup([row]) if pages > 1 else None

# ===== Telegram Handlers =====
async def start_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(
        [
            ["/help", "/connect"],
            ["/list", "/create 4 10"],
            ["/del_eq 4", "/del_all"]
        ],
        resize_keyboard=True
    )
    await u.message.reply_text(
        "👋 Привет! Я помогу управлять FreePBX: подключение, список SIP, создание и удаление.\n"
        "Набери /help для инструкции.",
        reply_markup=kb
    )
    
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
                try: await notice.edit_text(f"⏳ Добавляю линии… ({processed}/{total})")
                except Exception: pass
            await asyncio.sleep(0)

        parts = []
        if created:     parts.append("✅ Создано: " + ", ".join(created))
        if skipped_ext: parts.append("↩️ Уже существуют EXT: " + ", ".join(skipped_ext))
        if skipped_name:parts.append("🔁 Дубли имён: " + ", ".join(skipped_name))
        if name_check_warn: parts.append("ℹ️ Имя проверить не удалось (сервер не отдаёт имена).")
        if not parts:   parts.append("Нечего делать.")
        await u.message.reply_text("\n".join(parts))

        # === НОВОЕ: применяем конфиг, если реально что-то создали ===
        if created:
            try:
                try: await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception: pass
                fb.apply_config()
                try: await notice.edit_text("✅ Конфиг применён. Обновляю список…")
                except Exception: pass
            except Exception as e:
                await u.message.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        # Обновление списка
        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        page_items, page, pages = _slice_pairs(pairs, page=0)
        await u.message.reply_text(_list_page_text(fb.base_url, page_items), reply_markup=_list_nav_kb(page, pages))
    except Exception as e:
        await u.message.reply_text(f"Ошибка /add: <code>{escape(str(e))}</code>")

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

        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        text = _list_page_text(fb.base_url, pairs_page)
        kb = _list_nav_kb(page, pages)

        await u.message.reply_text(
            f"✅ Подключено к <code>{escape(fb.base_url)}</code>",
        )
        await u.message.reply_text(text, reply_markup=kb)

    except Exception as e:
        await u.message.reply_text(
            f"Ошибка подключения: <code>{escape(str(e))}</code>"
        )

async def list_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        fb = fb_from_session(u.effective_chat.id)
        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        await u.message.reply_text(_list_page_text(fb.base_url, pairs_page), reply_markup=_list_nav_kb(page, pages))
    except Exception as e:
        await u.message.reply_text(f"Ошибка: <code>{escape(str(e))}</code>")

async def list_nav_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data
    if not data.startswith("list:page:"):
        await q.answer()
        return
    try:
        page = int(data.split(":")[-1])
        pairs = c.user_data.get("__last_pairs")
        if pairs is None:
            fb = fb_from_session(u.effective_chat.id)
            pairs = fb.fetch_all_extensions()
            c.user_data["__last_pairs"] = pairs
        pairs_page, page, pages = _slice_pairs(pairs, page=page)
        fb = fb_from_session(u.effective_chat.id)
        await q.message.edit_text(
            _list_page_text(fb.base_url, pairs_page),
            reply_markup=_list_nav_kb(page, pages)
        )
        await q.answer()
    except Exception as e:
        await q.answer("Ошибка")

async def create_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    # Подробная подсказка при пустых аргументах
    if len(c.args) < 2:
        await u.message.reply_text(
            "❗ Формат команды:\n"
            "<code>/create &lt;оборудование&gt; &lt;кол-во&gt;</code>\n\n"
            "Где <b>оборудование</b> — номер базы (старт EXT):\n"
            "• 1 → 101…\n"
            "• 2 → 201…\n"
            "• 3 → 301…\n" 
            "• 4 → 401…\n"
            "• 10 → 1001…\n\n"
            "Пример:\n"
            "<code>/create 4 10</code> — создаст 10 линий, начиная с 401.\n",
        )
        return
    if not await _ensure_connected(u):
        return

    try:
        eq = int(c.args[0])
        cnt = int(c.args[1])
    except Exception:
        await u.message.reply_text(
            "❗ Некорректные аргументы.\n"
            "Используй: <code>/create &lt;оборудование&gt; &lt;кол-во&gt;</code>\n"
            "Например: <code>/create 4 10</code>"
        )
        return

    fb = fb_from_session(u.effective_chat.id)
    try:
        notice = await u.message.reply_text(f"⏳ Создаю {cnt} линий… (0/{cnt})")
        await u.message.chat.send_action(ChatAction.TYPING)

        all_pairs = fb.fetch_all_extensions()
        existing = [ext for ext, _ in all_pairs]
        start = equip_start(eq)
        targets = next_free(existing, start, cnt)

        for i, ext in enumerate(targets, 1):
            fb.create_one(int(ext))
            secret = secrets.token_hex(16)
            fb.set_ext_password(ext, secret)
            if i % 5 == 0 or i == cnt:
                try: await notice.edit_text(f"⏳ Создаю {cnt} линий… ({i}/{cnt})")
                except Exception: pass
            await asyncio.sleep(0)

        # === НОВОЕ: Apply Config один раз по завершении ===
        if targets:
            try:
                try: await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception: pass
                fb.apply_config()
                try: await notice.edit_text("✅ Конфиг применён. Обновляю список…")
                except Exception: pass
            except Exception as e:
                await u.message.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        pairs = fb.fetch_all_extensions()
        pairs_page, page, pages = _slice_pairs(pairs, page=0)
        c.user_data["__last_pairs"] = pairs
        await u.message.reply_text(_list_page_text(fb.base_url, pairs_page), reply_markup=_list_nav_kb(page, pages))
    except Exception as e:
        await u.message.reply_text(f"Ошибка создания: <code>{escape(str(e))}</code>")

async def del_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        await u.message.reply_text("Формат: /del 401 402 410-418")
        return
    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)
    requested = parse_targets(" ".join(c.args))
    try:
        await u.message.chat.send_action(ChatAction.TYPING)
        by_ext, _, _ = fb.fetch_ext_index()
        existing = set(by_ext.keys())

        targets = [x for x in requested if x in existing]
        missing = [x for x in requested if x not in existing]

        total = len(targets)
        notice = await u.message.reply_text(f"⏳ Удаляю линии… (0/{total})") if total else None

        ok, failed = [], []
        for i, ext in enumerate(targets, 1):
            try:
                fb.delete_extension(ext); ok.append(ext)
            except Exception:
                failed.append(ext)
            if notice and (i % 10 == 0 or i == total):
                try: await notice.edit_text(f"⏳ Удаляю линии… ({i}/{total})")
                except Exception: pass
            await asyncio.sleep(0)

        parts = []
        if ok:      parts.append("🗑️ Удалено: " + ", ".join(ok))
        if missing: parts.append("↩️ Пропущено (нет такой линии): " + ", ".join(missing))
        if failed:  parts.append("❌ Ошибка удаления: " + ", ".join(failed))
        if not parts: parts.append("Нечего удалять.")
        await u.message.reply_text("\n".join(parts))

        # === НОВОЕ: Apply Config, если реально что-то удалили ===
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
                await u.message.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

        # Обновление списка
        pairs = fb.fetch_all_extensions()
        c.user_data["__last_pairs"] = pairs
        page_items, page, pages = _slice_pairs(pairs, page=0)
        await u.message.reply_text(_list_page_text(fb.base_url, page_items), reply_markup=_list_nav_kb(page, pages))
    except Exception as e:
        await u.message.reply_text(f"Ошибка удаления: <code>{escape(str(e))}</code>")

async def del_eq_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not c.args:
        await u.message.reply_text("Формат: /del_eq <оборудование>")
        return
    if not await _ensure_connected(u):
        return

    eq = int(c.args[0])
    start = equip_start(eq); end = start + 99
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

async def del_all_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    q = u.callback_query
    if not q.data.startswith("delall:"):
        await q.answer(); return
    answer = q.data.split(":")[1]
    if answer == "no":
        await q.edit_message_text("Отменено."); await q.answer("Отмена"); return
    try:
        fb = fb_from_session(u.effective_chat.id)
        await q.edit_message_text("⏳ Удаляю все линии… (подготовка)")
        pairs = fb.fetch_all_extensions()
        total = len(pairs)
        done = 0
        await q.message.chat.send_action(ChatAction.TYPING)
        for ext, _ in pairs:
            try:
                fb.delete_extension(ext)
            except Exception:
                pass
            done += 1
            if done % 25 == 0 or done == total:
                try:
                    await q.edit_message_text(f"⏳ Удаляю все линии… ({done}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        if total:
            try:
                try: await q.edit_message_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception: pass
                fb.apply_config()
            except Exception as e:
                try:
                    await q.edit_message_text(f"Удаление выполнено, но Apply Config не удалось: <code>{escape(str(e))}</code>")
                except Exception:
                    pass
                await q.answer("Ошибка Apply Config")
                return

        await q.edit_message_text(f"{fb.base_url}\n\n(всё удалено)")
        await q.answer("Готово")
    except Exception as e:
        await q.edit_message_text(f"Ошибка удаления: <code>{escape(str(e))}</code>")
        await q.answer("Ошибка")

async def reconnect_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        s = SESS.get(u.effective_chat.id)
        fb = FreePBX(s["base_url"], s["client_id"], s["client_secret"], verify=s["verify"])
        fb.ensure_token()
        s["token"] = fb.token; s["token_exp"] = fb.token_exp
        await u.message.reply_text("🔁 Переподключение выполнено.")
    except Exception as e:
        await u.message.reply_text(f"Ошибка reconnect: <code>{escape(str(e))}</code>")

async def ping_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    try:
        fb = fb_from_session(u.effective_chat.id)
        fb.ensure_token()
        _ = fb.gql("query { fetchAllExtensions { extension { extensionId } } }")
        await u.message.reply_text("✅ OK")
    except Exception as e:
        await u.message.reply_text(f"❌ Unauthorized / ошибка: <code>{escape(str(e))}</code>")

async def whoami_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_connected(u):
        return
    s = SESS.get(u.effective_chat.id)
    ttl = max(0, int(s.get("token_exp", 0) - time.time()))
    await u.message.reply_text(
        "👤 Текущая сессия\n"
        f"URL: <code>{s['base_url']}</code>\n"
        f"Client ID: <code>{s['client_id']}</code>\n"
        f"TLS verify: <code>{s['verify']}</code>\n"
        f"Токен жив ещё: <code>{ttl} сек</code>"
    )

async def logout_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    SESS.pop(u.effective_chat.id, None)
    c.user_data.clear()
    await u.message.reply_text("🚪 Сессия сброшена. Используйте /connect.")
    
async def add_inbound_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    /add_inbound 414
    /add_inbound 414-420
    Для каждого существующего EXT создаём inbound route:
    Description=sim{ext}, DID={ext}, Destination -> Extension {ext}
    """
    if not c.args:
        await u.message.reply_text(
            "Форматы:\n"
            "<code>/add_inbound &lt;ext&gt;</code>\n"
            "<code>/add_inbound &lt;start-end&gt;</code>\n"
            "Примеры:\n"
            "<code>/add_inbound 414</code>\n"
            "<code>/add_inbound 401-418</code>\n"
            "Маршрут создаётся только если EXT существует."
        )
        return

    if not await _ensure_connected(u):
        return

    fb = fb_from_session(u.effective_chat.id)

    arg0 = " ".join(c.args)
    targets = parse_targets(arg0)

    try:
        await u.message.chat.send_action(ChatAction.TYPING)

        by_ext, _, _ = fb.fetch_ext_index()
        existing_exts = set(by_ext.keys())

        todo = [x for x in targets if x in existing_exts]
        missing = [x for x in targets if x not in existing_exts]

        total = len(todo)
        if total == 0:
            msg = ["Нечего создавать."]
            if missing:
                msg.append("↩️ Пропущено (нет таких EXT): " + ", ".join(missing))
            await u.message.reply_text("\n".join(msg))
            return

        notice = await u.message.reply_text(f"⏳ Добавляю Inbound Routes… (0/{total})")

        ok, failed = [], []
        for i, ext in enumerate(todo, 1):
            try:
                fb.create_inbound_route(did=ext, description=f"sim{ext}", ext=ext)
                ok.append(ext)
            except Exception as e:
                failed.append(f"{ext} ({str(e)[:80]})")

            if i % 5 == 0 or i == total:
                try:
                    await notice.edit_text(f"⏳ Добавляю Inbound Routes… ({i}/{total})")
                except Exception:
                    pass
            await asyncio.sleep(0)

        parts = []
        if ok:      parts.append("✅ Создано маршрутов: " + ", ".join(ok))
        if missing: parts.append("↩️ Пропущено (нет таких EXT): " + ", ".join(missing))
        if failed:  parts.append("❌ Ошибки: " + ", ".join(failed))
        await u.message.reply_text("\n".join(parts) if parts else "Нечего делать.")

        if ok:
            try:
                try: await notice.edit_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception: pass
                fb.apply_config()
                try: await notice.edit_text("✅ Конфиг применён.")
                except Exception: pass
            except Exception as e:
                await u.message.reply_text(f"⚠️ Apply Config не удалось: <code>{escape(str(e))}</code>")

    except Exception as e:
        await u.message.reply_text(f"Ошибка /add_inbound: <code>{escape(str(e))}</code>")
    
# ===== Lifecycle =====
async def on_startup(app: Application):
    print("✅ Бот запущен и слушает обновления. Команда помощи: /help")
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(int(ADMIN_CHAT_ID), "✅ Бот запущен и готов к работе. Напишите /help.")
        except Exception as e:
            log.warning(f"Не удалось отправить сообщение админу: {e}")

def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN":
        raise RuntimeError("TG_TOKEN не задан. Укажи переменную окружения TG_TOKEN или впиши токен в код.")

    defaults = Defaults(parse_mode=ParseMode.HTML)  # ✅ HTML везде
    app = (
        Application
        .builder()
        .token(TELEGRAM_TOKEN)
        .defaults(defaults)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("connect", connect_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("create", create_cmd))
    app.add_handler(CommandHandler("del", del_cmd))
    app.add_handler(CommandHandler("del_eq", del_eq_cmd))
    app.add_handler(CommandHandler("del_all", del_all_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("add_inbound", add_inbound_cmd))


    app.add_handler(CommandHandler("reconnect", reconnect_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))
    app.add_handler(CommandHandler("logout", logout_cmd))

    app.add_handler(CallbackQueryHandler(list_nav_cb, pattern=r"^list:page:"))
    app.add_handler(CallbackQueryHandler(del_all_cb, pattern=r"^delall:"))
    app.add_handler(CallbackQueryHandler(lambda u,c: u.callback_query.answer(), pattern=r"^noop$"))

    app.run_polling()


if __name__ == "__main__":
    main()
