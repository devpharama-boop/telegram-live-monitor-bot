"""Run bot in subprocess - with proper event loop handling"""
import sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

from core.config import Config
from core.database import Database
from core.bot_app import BotApp
from telegram.ext import Application

# Patch run_polling to use already-running loop instead of creating new one
_original = Application.run_polling

def patched_run_polling(self, **kwargs):
    """Use the current event loop instead of creating a new one with asyncio.run()"""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(self.initialize())
    loop.run_until_complete(self.start())
    loop.run_forever()
    try:
        loop.run_until_complete(self.stop())
        loop.run_until_complete(self.shutdown())
    except:
        pass

Application.run_polling = patched_run_polling

async def run_bot():
    config = Config()
    db = Database(config.db_path)
    db.initialize()
    bot_app = BotApp(config, db)
    await bot_app.start()

if __name__ == "__main__":
    log.info("Bot starting...")
    asyncio.run(run_bot())
