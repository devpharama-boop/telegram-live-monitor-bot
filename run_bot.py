"""Run bot in subprocess"""
import sys, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.config import Config
from core.database import Database
from core.bot_app import BotApp
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')

config = Config()
db = Database(config.db_path)
db.initialize()
bot_app = BotApp(config, db)
asyncio.run(bot_app.start())
