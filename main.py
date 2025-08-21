# src/main.py
import os
from telegram.ext import Application, Defaults, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from dotenv import load_dotenv
from handlers.commands import list_routes_cmd



from handlers.commands import (
    start_cmd, help_cmd, connect_cmd, list_cmd, create_cmd, del_cmd,
    del_eq_cmd, del_all_cmd, add_cmd, add_inbound_cmd, reconnect_cmd,
    ping_cmd, whoami_cmd, logout_cmd, on_startup, del_inbound_cmd, gql_fields_cmd, gql_mutations_cmd
)
from handlers.callbacks import list_nav_cb, del_all_cb, noop_cb

def _get_token() -> str:
    load_dotenv()
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token or token == "PUT_YOUR_TELEGRAM_BOT_TOKEN":
        raise RuntimeError(
            "TELEGRAM_TOKEN не задан. Укажи переменную окружения TELEGRAM_TOKEN "
            "или заполни её в .env"
        )
    return token

def build_app():
    token = _get_token()
    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = Application.builder().token(token).defaults(defaults).post_init(on_startup).build()

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
    app.add_handler(CommandHandler("del_inbound", del_inbound_cmd))
    app.add_handler(CommandHandler("list_routes", list_routes_cmd))
    app.add_handler(CommandHandler("gql_fields", gql_fields_cmd))
    app.add_handler(CommandHandler("gql_mutations", gql_mutations_cmd))



    


    app.add_handler(CallbackQueryHandler(list_nav_cb, pattern=r"^list:page:"))
    app.add_handler(CallbackQueryHandler(del_all_cb, pattern=r"^delall:"))
    app.add_handler(CallbackQueryHandler(noop_cb, pattern=r"^noop$"))
    return app

if __name__ == "__main__":
    build_app().run_polling()
