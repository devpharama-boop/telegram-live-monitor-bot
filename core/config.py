"""Configuration management"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / '.env')


class Config:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN', '')
        self.api_id = int(os.getenv('API_ID', '0'))
        self.api_hash = os.getenv('API_HASH', '')
        self.admin_id = int(os.getenv('ADMIN_ID', '0'))
        self.db_path = os.getenv('DB_PATH', 'data/bot.db')

        if not self.bot_token:
            raise ValueError("❌ BOT_TOKEN not set in .env")
        if not self.api_id:
            raise ValueError("❌ API_ID not set in .env")
        if not self.api_hash:
            raise ValueError("❌ API_HASH not set in .env")
        if not self.admin_id:
            raise ValueError("❌ ADMIN_ID not set in .env")
