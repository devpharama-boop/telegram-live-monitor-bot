"""Telegram Live Monitor Bot - Health + Monkey-patched Bot"""
import asyncio, logging, os, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
Path("data").mkdir(exist_ok=True); Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# --- Health server ---
def health():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class H(BaseHTTPRequestHandler):
        def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b"ok")
        def log_message(*a): pass
    HTTPServer(('', int(os.environ.get('PORT', 8080))), H).serve_forever()

# --- Monkey-patch run_polling ---
from telegram.ext import Application
_orig = Application.run_polling
def patched(self, **kw):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(self.initialize())
    loop.run_until_complete(self.start())
    loop.run_forever()
Application.run_polling = patched

# --- Bot start ---
async def start_bot():
    from core.config import Config
    from core.database import Database
    from core.bot_app import BotApp
    config = Config()
    db = Database(config.db_path)
    db.initialize()
    bot = BotApp(config, db)
    await bot.start()

if __name__ == "__main__":
    threading.Thread(target=health, daemon=True).start()
    log.info("Health server on :8080")
    asyncio.run(start_bot())
