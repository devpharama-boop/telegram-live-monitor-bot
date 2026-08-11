"""Telegram Live Stream Monitor Bot"""

import logging
import sys
import os
import threading
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE_DIR = Path(__file__).parent
(BASE_DIR / 'data').mkdir(parents=True, exist_ok=True)
(BASE_DIR / 'logs').mkdir(parents=True, exist_ok=True)

from core.config import Config
from core.database import Database
from core.bot_app import BotApp

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
    config = Config()
    logger.info("✅ Config loaded")
    db = Database(config.db_path)
    db.initialize()
    logger.info("✅ Database initialized")

    threading.Thread(target=run_health_server, daemon=True).start()

    bot_app = BotApp(config, db)
    logger.info("🤖 Starting Telegram Live Monitor Bot...")

    # Monkey-patch run_polling to prevent asyncio.run() nesting
    from telegram.ext import Application
    orig_run_polling = Application.run_polling
    def patched_run_polling(self, allowed_updates=None, **kwargs):
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(self.initialize())
            loop.run_until_complete(self.start())
            loop.run_forever()
        finally:
            loop.run_until_complete(self.stop())
            loop.run_until_complete(self.shutdown())
    Application.run_polling = patched_run_polling

    try:
        asyncio.run(bot_app.start())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped")
    except Exception as e:
        logger.error(f"❌ Fatal: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
