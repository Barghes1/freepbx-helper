from html import escape
from typing import List, Tuple

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ChatAction

from .commands import (
    _ensure_connected,
    fb_from_session,
    _slice_pairs,
)
from ui.texts import _list_nav_kb, _list_page_text  # рендер страницы и нав.клавиатуры
from utils.common import clean_url


async def list_nav_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик пагинации списка EXT.
    callback_data: "list:page:<N>"
    """
    q = u.callback_query
    data = q.data
    if not data.startswith("list:page:"):
        await q.answer()
        return

    try:
        page = int(data.split(":")[-1])

        # Берём кешированный список, если есть
        pairs: List[Tuple[str, str]] = c.user_data.get("__last_pairs")
        fb = fb_from_session(u.effective_chat.id)
        if pairs is None:
            pairs = fb.fetch_all_extensions()
            c.user_data["__last_pairs"] = pairs

        pairs_page, page, pages = _slice_pairs(pairs, page=page)
        await q.message.edit_text(
            _list_page_text(clean_url(fb.base_url), pairs_page),
            reply_markup=_list_nav_kb(page, pages)
        )
        await q.answer()
    except Exception:
        await q.answer("Ошибка")


async def del_all_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """
    Подтверждение полного удаления всех линий.
    callback_data: "delall:yes" | "delall:no"
    """
    if not await _ensure_connected(u):
        return

    q = u.callback_query
    if not q.data.startswith("delall:"):
        await q.answer()
        return

    answer = q.data.split(":")[1]
    if answer == "no":
        await q.edit_message_text("Отменено.")
        await q.answer("Отмена")
        return

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

        # Apply Config при наличии удалений
        if total:
            try:
                try:
                    await q.edit_message_text("🔄 Применяю конфиг (Apply Config)…")
                except Exception:
                    pass
                fb.apply_config()
            except Exception as e:
                try:
                    await q.edit_message_text(
                        f"Удаление выполнено, но Apply Config не удалось: <code>{escape(str(e))}</code>"
                    )
                except Exception:
                    pass
                await q.answer("Ошибка Apply Config")
                return

        await q.edit_message_text(f"{fb.base_url}\n\n(всё удалено)")
        await q.answer("Готово")
    except Exception as e:
        await q.edit_message_text(f"Ошибка удаления: <code>{escape(str(e))}</code>")
        await q.answer("Ошибка")


async def noop_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Пустой обработчик для 'noop'."""
    await u.callback_query.answer()
    

