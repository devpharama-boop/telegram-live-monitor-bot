"""Telegram Live Stream Monitor Bot"""

import asyncio
import logging
import sys
import os
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE_DIR = Path(__file__).parent
(BASE_DIR / 'data').mkdir(parents=True, exist_ok=True)
(BASE_DIR / 'logs').mkdir(parents=True, exist_ok=True)

from core.config import Config
from core.database import Database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def run_health_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class H(BaseHTTPRequestHandler):
        def do_GET(s):
            s.send_response(200)
            s.send_header('Content-Type', 'application/json')
            s.end_headers()
            s.wfile.write(b'{"status":"ok"}')
        def log_message(s, f, *a):
            pass
    port = int(os.environ.get('PORT', 8080))
    HTTPServer(('0.0.0.0', port), H).serve_forever()


def main():
    try:
        config = Config()
        logger.info("Config loaded")
        db = Database(config.db_path)
        db.initialize()
        logger.info("Database initialized")

        threading.Thread(target=run_health_server, daemon=True).start()

        from core.bot_app import BotApp, LiveMonitor
        from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
        from telegram import Update, BotCommand
        from telegram.constants import ParseMode

        monitor = LiveMonitor(config, db)
        app = Application.builder().token(config.bot_token).build()
        bot_app = BotApp(config, db)
        bot_app.app = app
        bot_app.monitor = monitor
        bot_app._register_handlers()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_app._set_commands())
        loop.run_until_complete(monitor.init_accounts())
        loop.run_until_complete(monitor.start_all_monitors())
        db.add_admin(config.admin_id, config.admin_id)
        loop.close()
        
        logger.info("Bot initialized, starting polling...")
        app.run_polling(allowed_updates=['message', 'callback_query'])

    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
