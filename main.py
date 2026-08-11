# Telegram Live Stream Monitor Bot
# ==================================

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.config import Config
from core.database import Database
from core.bot_app import BotApp
from core.monitor import LiveMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / 'logs' / 'bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point"""
    try:
        # Ensure directories exist
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)

        # Load config
        config = Config()
        logger.info("✅ Config loaded")

        # Initialize database
        db = Database(config.db_path)
        db.initialize()
        logger.info("✅ Database initialized")

        # Initialize and start bot
        bot_app = BotApp(config, db)
        logger.info("🤖 Starting Telegram Live Monitor Bot...")

        await bot_app.start()

    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
