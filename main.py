"""Telegram Live Stream Monitor Bot"""

import logging
import sys
import os
import threading
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Ensure required directories exist
BASE_DIR = Path(__file__).parent
(BASE_DIR / 'data').mkdir(parents=True, exist_ok=True)
(BASE_DIR / 'logs').mkdir(parents=True, exist_ok=True)

from core.config import Config
from core.database import Database
from core.bot_app import BotApp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def run_health_server():
    """Simple HTTP health check server for Railway port binding"""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"telegram-live-monitor-bot"}')

        def log_message(self, format, *args):
            pass  # suppress HTTP logs

    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"✅ Health server started on port {port}")
    server.serve_forever()


def main():
    """Main entry point"""
    try:
        # Load config
        config = Config()
        logger.info("✅ Config loaded")

        # Initialize database
        db = Database(config.db_path)
        db.initialize()
        logger.info("✅ Database initialized")

        # Start health server in a separate thread (required by Railway)
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()

        # Initialize bot app
        bot_app = BotApp(config, db)

        # Build the application
        bot_app.build()

        # Initialize accounts and monitors (sync wrapper for async calls)
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_app.initialize())

        logger.info("🤖 Starting Telegram Live Monitor Bot...")

        # Run polling - this creates its own event loop (avoiding nested loop issue)
        bot_app.app.run_polling(allowed_updates=['message', 'callback_query'])

    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
