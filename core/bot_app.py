"""Main Bot Application - Telegram Live Monitor Bot"""

import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes,
    filters
)
from telegram.constants import ParseMode

from .database import Database
from .config import Config
from .monitor import LiveMonitor

logger = logging.getLogger(__name__)

(WAITING_PHONE, WAITING_OTP, WAITING_2FA,
 WAITING_CHANNEL_ID, WAITING_MESSAGE_TEXT,
 WAITING_IMAGE, WAITING_LINK, WAITING_NEW_ADMIN_ID) = range(8)


class BotApp:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.monitor = LiveMonitor(config, db)
        self.app: Optional[Application] = None
        self.user_states: Dict[int, Dict] = {}

    def run(self):
        """Sync entry point - builds, inits async, then runs polling"""
        import asyncio as _asyncio
        
        self.app = Application.builder().token(self.config.bot_token).build()
        self._register_handlers()
        
        _loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(self._async_init())
        finally:
            _loop.close()
        
        logger.info("✅ Bot initialized, starting polling...")
        
        self.app.run_polling(allowed_updates=['message', 'callback_query'])

    async def _async_init(self):
        """Async init: set commands, init accounts, start monitors"""
        await self._set_commands()
        await self.monitor.init_accounts()
        await self.monitor.start_all_monitors()
        self.db.add_admin(self.config.admin_id, self.config.admin_id)

    async def stop(self):
        await self.monitor.stop_all_monitors()
        if self.app:
            await self.app.stop()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("menu", self.cmd_menu))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_message
        ))
        self.app.add_handler(MessageHandler(
            filters.PHOTO, self.handle_photo
        ))

    async def _set_commands(self):
        commands = [
            BotCommand("start", "🚀 Start the bot / Main menu"),
            BotCommand("menu", "📋 Show main menu"),
            BotCommand("cancel", "❌ Cancel current action"),
        ]
        await self.app.bot.set_my_commands(commands)