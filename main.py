"""Telegram Live Monitor Bot"""
import asyncio, logging, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
Path("data").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

from core.config import Config
from core.database import Database
from core.bot_app import BotApp
from telegram.ext import Application

# Monkey-patch: don't create new event loop
_orig = Application.run_polling
def patched(self, **kw):
    import asyncio as a
    loop = a.get_event_loop()
    loop.run_until_complete(self.initialize())
    loop.run_until_complete(self.start())
    loop.run_forever()
Application.run_polling = patched

async def run():
    c = Config(); d = Database(c.db_path); d.initialize()
    b = BotApp(c, d)
    await b.start()

log.info("Starting bot...")
asyncio.run(run())
