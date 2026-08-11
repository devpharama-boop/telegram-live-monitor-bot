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

    async def start(self):
        self.app = Application.builder().token(self.config.bot_token).build()
        self._register_handlers()
        await self._set_commands()
        await self.monitor.init_accounts()
        await self.monitor.start_all_monitors()
        self.db.add_admin(self.config.admin_id, self.config.admin_id)
        logger.info("✅ Bot is running!")
        await self.app.run_polling(allowed_updates=Update.ALL_TYPES)

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

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_id = user.id
        stats = self.db.get_stats()
        channels = self.db.get_channels()
        monitoring_count = len([c for c in channels if self.monitor.get_monitor_status(c['channel_id'])])

        welcome_text = (
            f"👤 **Welcome, {user.first_name}!**\\n\\n"
            f"📡 **Live Stream Monitor Bot**\\n\\n"
            f"📊 **Dashboard Overview**\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
            f"👤 Accounts: **{stats['total_accounts']}** (Logged in: {stats['logged_in_accounts']})\\n"
            f"📢 Channels Joined: **{stats['total_channels']}**\\n"
            f"🔍 Monitoring: **{monitoring_count}**\\n"
            f"📨 DMs Sent Today: **{stats['today_dms']}**\\n"
            f"📨 Total DMs: **{stats['total_dms_sent']}**\\n"
            f"🔴 Active Live Sessions: **{stats['active_live_sessions']}**\\n"
            f"━━━━━━━━━━━━━━━━━━\\n\\n"
            f"👇 **Use buttons below:**\\n"
        )

        keyboard = self._build_main_menu(user_id)
        await update.message.reply_text(
            welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        keyboard = self._build_main_menu(user_id)
        stats = self.db.get_stats()
        channels = self.db.get_channels()
        monitoring_count = len([c for c in channels if self.monitor.get_monitor_status(c['channel_id'])])

        menu_text = (
            f"📋 **Main Menu**\\n\\n"
            f"👤 Accounts: **{stats['total_accounts']}** | 📢 Channels: **{stats['total_channels']}**\\n"
            f"🔍 Monitoring: **{monitoring_count}** | 📨 Today: **{stats['today_dms']}**\\n\\n"
            f"👇 **Select an option:**"
        )
        await update.message.reply_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        self.user_states.pop(user_id, None)
        await update.message.reply_text("❌ Action cancelled. Use /menu for options.")

    def _build_main_menu(self, user_id: int) -> InlineKeyboardMarkup:
        is_admin = self.db.is_admin(user_id)
        keyboard = [
            [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
            [InlineKeyboardButton("🔴 Live Monitor", callback_data="live_monitor")],
            [InlineKeyboardButton("✉️ Set Message", callback_data="set_message")],
            [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
            [InlineKeyboardButton("👤 Account", callback_data="account")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        if is_admin:
            keyboard.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin_panel")])
        return InlineKeyboardMarkup(keyboard)

    # ==================== CALLBACK HANDLER ====================

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user_id = query.from_user.id

        parts = data.split('|', 1)
        action = parts[0]
        param = parts[1] if len(parts) > 1 else None

        handlers = {
            "add_channel": self._cb_add_channel,
            "live_monitor": self._cb_live_monitor,
            "set_message": self._cb_set_message,
            "dashboard": self._cb_dashboard,
            "account": self._cb_account,
            "help": self._cb_help,
            "admin_panel": self._cb_admin_panel,
            "back_menu": self._cb_back_menu,
            "check_channel": self._cb_check_channel,
            "join_channel": self._cb_join_channel,
            "remove_channel": self._cb_remove_channel,
            "view_channel_live": self._cb_view_channel_live,
            "start_monitor": self._cb_start_monitor,
            "stop_monitor": self._cb_stop_monitor,
            "edit_text": self._cb_edit_text,
            "set_image": self._cb_set_image,
            "set_link": self._cb_set_link,
            "reset_message": self._cb_reset_message,
            "reset_dm": self._cb_reset_dm,
            "view_message": self._cb_view_message,
            "add_account": self._cb_add_account,
            "list_accounts": self._cb_list_accounts,
            "delete_account": self._cb_delete_account,
            "add_admin": self._cb_add_admin,
            "remove_admin": self._cb_remove_admin,
            "list_admins": self._cb_list_admins,
            "bot_stats": self._cb_bot_stats,
            "reset_all_dms": self._cb_reset_all_dms,
            "confirm_remove_ch": self._cb_confirm_remove_channel,
        }

        handler = handlers.get(action)
        if handler:
            await handler(query, param, user_id)
        else:
            await query.edit_message_text(
                f"❓ Unknown action: {action}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="back_menu")
                ]])
            )

    # ==================== MAIN MENU HANDLERS ====================

    async def _cb_add_channel(self, query, param, user_id):
        channels = self.db.get_channels()
        accounts = self.db.get_logged_in_accounts()

        if not accounts:
            text = (
                "⚠️ **No accounts logged in!**\\n\\n"
                "You need to add a Telegram account first.\\n"
                "Go to **Account → Add Account** to login."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Go to Account", callback_data="account")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
            ])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            return

        channel_list = ""
        if channels:
            for i, ch in enumerate(channels, 1):
                monitoring = "🟢" if self.monitor.get_monitor_status(ch['channel_id']) else "⚪"
                channel_list += f"{i}. {monitoring} **{ch.get('channel_title', 'Unknown')}**\\n"
                channel_list += f"   `{ch['channel_id']}`\\n"
        else:
            channel_list = "No channels added yet.\\n"

        text = (
            f"📢 **Channel Management**\\n\\n"
            f"**Added Channels ({len(channels)}):**\\n{channel_list}\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
            f"📝 Enter channel ID/invite link to add new channel."
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add New Channel", callback_data="check_channel")],
            [InlineKeyboardButton("🗑️ Remove Channel", callback_data="remove_channel")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_live_monitor(self, query, param, user_id):
        channels = self.db.get_channels()
        active_sessions = self.db.get_active_live_sessions()
        active_channel_ids = {s['channel_id'] for s in active_sessions}

        if not channels:
            text = "🔴 **Live Monitor**\\n\\nNo channels added yet.\\nAdd channels first from **Add Channel** menu."
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
            ])
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            return

        text = "🔴 **Live Stream Monitor**\\n\\n"
        keyboard_buttons = []

        for ch in channels:
            is_monitoring = self.monitor.get_monitor_status(ch['channel_id'])
            is_live = ch['channel_id'] in active_channel_ids
            dm_count = self.db.get_dm_count_for_channel(ch['channel_id'])

            status_icon = "🟢 LIVE" if is_live else ("🔵 ON" if is_monitoring else "⚪ OFF")
            text += f"{status_icon} **{ch.get('channel_title', 'Unknown')}**\\n"
            text += f"   📨 DMs sent: {dm_count}\\n"
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"📋 {ch.get('channel_title', 'Channel')[:20]}",
                    callback_data=f"view_channel_live|{ch['channel_id']}"
                )
            ])

        keyboard_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_buttons), parse_mode=ParseMode.MARKDOWN)

    async def _cb_set_message(self, query, param, user_id):
        message = self.db.get_active_message('default')
        msg_preview = "No message set."
        if message:
            txt = message.get('text', '')
            msg_preview = txt[:200] + ('...' if len(txt) > 200 else '')
            if message.get('image_path') or message.get('image_url'):
                msg_preview += "\\n🖼️ Image: ✅"
            if message.get('link_url'):
                msg_preview += f"\\n🔗 Link: {message.get('link_url')}"

        text = (
            f"✉️ **Message Settings**\\n\\n"
            f"**Current Message Preview:**\\n```\\n{msg_preview}\\n```\\n\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
            f"📌 **Variables:** {{first_name}}, {{username}}, {{channel}}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit Text", callback_data="edit_text")],
            [InlineKeyboardButton("🖼️ Set Image", callback_data="set_image")],
            [InlineKeyboardButton("🔗 Set Link", callback_data="set_link")],
            [InlineKeyboardButton("👁️ View Full Message", callback_data="view_message")],
            [InlineKeyboardButton("🔄 Reset Message", callback_data="reset_message")],
            [InlineKeyboardButton("🗑️ Reset All DMs Log", callback_data="reset_dm")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_dashboard(self, query, param, user_id):
        stats = self.db.get_stats()
        channels = self.db.get_channels()
        accounts = self.db.get_logged_in_accounts()
        monitoring_count = len([c for c in channels if self.monitor.get_monitor_status(c['channel_id'])])

        text = (
            f"📊 **Dashboard**\\n━━━━━━━━━━━━━━━━━━\\n\\n"
            f"👤 **Accounts**\\n   Total: **{stats['total_accounts']}**\\n   Logged In: **{stats['logged_in_accounts']}**\\n\\n"
            f"📢 **Channels**\\n   Joined: **{stats['total_channels']}**\\n   Monitoring: **{monitoring_count}**\\n\\n"
            f"🔴 **Live Sessions**\\n   Active: **{stats['active_live_sessions']}**\\n\\n"
            f"📨 **DM Statistics**\\n   Today: **{stats['today_dms']}**\\n   Total: **{stats['total_dms_sent']}**\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
        )
        if accounts:
            text += "\\n📱 **Logged In Accounts:**\\n"
            for acc in accounts:
                text += f"   • {acc.get('first_name', 'N/A')} ({acc['phone']})\\n"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="dashboard")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_account(self, query, param, user_id):
        accounts = self.db.get_all_accounts()
        text = "👤 **Account Management**\\n\\n"
        if accounts:
            for acc in accounts:
                status = "🟢" if acc['is_logged_in'] else "🔴"
                name = acc.get('first_name') or acc['phone']
                text += f"{status} {name}\\n"
        else:
            text += "No accounts added.\\n"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
            [InlineKeyboardButton("📋 List Accounts", callback_data="list_accounts")],
            [InlineKeyboardButton("🗑️ Delete Account", callback_data="delete_account")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_help(self, query, param, user_id):
        text = (
            f"❓ **Help & Guide**\\n━━━━━━━━━━━━━━━━━━\\n\\n"
            f"1️⃣ **Add Account** - Login Telegram account\\n"
            f"2️⃣ **Add Channel** - Add channel to monitor\\n"
            f"3️⃣ **Set Message** - Configure DM template\\n"
            f"4️⃣ **Live Monitor** - View live status\\n"
            f"5️⃣ **Auto DM** - Bot sends DMs automatically\\n\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
            f"📌 One user receives DM only once per session.\\n"
            f"📌 Bot rotates accounts to avoid limits."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_admin_panel(self, query, param, user_id):
        if not self.db.is_admin(user_id):
            await query.answer("⛔ Admin access only!", show_alert=True)
            return
        admins = self.db.get_all_admins()
        stats = self.db.get_stats()
        admin_list = "\\n".join([f"• `{a['user_id']}`" for a in admins])
        text = (
            f"🔐 **Admin Panel**\\n━━━━━━━━━━━━━━━━━━\\n\\n"
            f"**Bot Stats:**\\n• Accounts: {stats['total_accounts']}\\n"
            f"• Channels: {stats['total_channels']}\\n• Total DMs: {stats['total_dms_sent']}\\n\\n"
            f"**Admin List:**\\n{admin_list}\\n"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
            [InlineKeyboardButton("📋 Admin List", callback_data="list_admins")],
            [InlineKeyboardButton("📊 Full Stats", callback_data="bot_stats")],
            [InlineKeyboardButton("🗑️ Reset All DMs", callback_data="reset_all_dms")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_back_menu(self, query, param, user_id):
        keyboard = self._build_main_menu(user_id)
        stats = self.db.get_stats()
        channels = self.db.get_channels()
        monitoring_count = len([c for c in channels if self.monitor.get_monitor_status(c['channel_id'])])
        text = (
            f"📋 **Main Menu**\\n\\n"
            f"👤 Accounts: **{stats['total_accounts']}** | 📢 Channels: **{stats['total_channels']}**\\n"
            f"🔍 Monitoring: **{monitoring_count}** | 📨 Today: **{stats['today_dms']}**\\n\\n"
            f"👇 **Select an option:**"
        )
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    # ==================== CHANNEL HANDLERS ====================

    async def _cb_check_channel(self, query, param, user_id):
        self.user_states[user_id] = {'action': 'waiting_channel_id'}
        text = (
            "📢 **Add New Channel**\\n\\n"
            "Please send the **Channel ID** or **Invite Link**:\\n\\n"
            "Examples:\\n• `-1001234567890` (Channel ID)\\n"
            "• `https://t.me/channelname` (Invite link)\\n"
            "• `@channelusername` (Username)\\n\\n"
            "Type /cancel to abort."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Cancel", callback_data="add_channel")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_join_channel(self, query, param, user_id):
        if not param:
            await query.answer("⚠️ No channel specified", show_alert=True)
            return
        accounts = self.db.get_logged_in_accounts()
        if not accounts:
            await query.answer("⚠️ No accounts logged in!", show_alert=True)
            return
        await query.edit_message_text("⏳ Joining channel, please wait...", reply_markup=None)
        result = await self.monitor.join_channel(accounts[0]['phone'], param)
        if result.get('success'):
            text = (
                f"✅ **Channel Joined!**\\n\\n"
                f"📢 **{result.get('title', 'Unknown')}**\\n"
                f"🆔 `{result.get('channel_id', 'N/A')}`\\n"
                f"👤 @{result.get('username', 'N/A')}\\n\\n🔍 Monitoring started!"
            )
            await self.monitor.start_monitoring_channel(result['channel_id'])
        else:
            text = f"❌ **Failed!**\\n\\nError: {result.get('error', 'Unknown')}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Another", callback_data="add_channel")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_remove_channel(self, query, param, user_id):
        channels = self.db.get_channels()
        if not channels:
            await query.answer("No channels to remove", show_alert=True)
            return
        keyboard_buttons = []
        for ch in channels:
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"🗑️ {ch.get('channel_title', 'Unknown')[:30]}",
                    callback_data=f"confirm_remove_ch|{ch['channel_id']}"
                )
            ])
        keyboard_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="add_channel")])
        await query.edit_message_text(
            "🗑️ **Select channel to remove:**",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_confirm_remove_channel(self, query, param, user_id):
        if param:
            await self.monitor.stop_monitoring_channel(param)
            self.db.remove_channel(param)
            await query.answer("✅ Channel removed!", show_alert=True)
            await self._cb_add_channel(query, None, user_id)

    async def _cb_view_channel_live(self, query, param, user_id):
        if not param:
            await query.answer("No channel specified", show_alert=True)
            return
        channel = self.db.get_channel(param)
        if not channel:
            await query.answer("Channel not found", show_alert=True)
            return

        dm_count = self.db.get_dm_count_for_channel(param)
        is_monitoring = self.monitor.get_monitor_status(param)
        active_sessions = self.db.get_active_live_sessions()
        is_live = any(s['channel_id'] == param for s in active_sessions)

        text = (
            f"📋 **Channel Details**\\n━━━━━━━━━━━━━━━━━━\\n"
            f"📢 **{channel.get('channel_title', 'Unknown')}**\\n"
            f"🆔 `{channel['channel_id']}`\\n"
            f"👤 @{channel.get('channel_username', 'N/A')}\\n"
            f"━━━━━━━━━━━━━━━━━━\\n"
            f"🔴 Live Status: **{'🟢 LIVE' if is_live else '⚪ Not Live'}**\\n"
            f"🔍 Monitoring: **{'🟢 Active' if is_monitoring else '⚪ Inactive'}**\\n"
            f"📨 Total DMs Sent: **{dm_count}**\\n"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⏹️ Stop Monitor" if is_monitoring else "▶️ Start Monitor",
                    callback_data=f"stop_monitor|{param}" if is_monitoring else f"start_monitor|{param}"
                )
            ],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"view_channel_live|{param}")],
            [InlineKeyboardButton("🗑️ Remove Channel", callback_data=f"confirm_remove_ch|{param}")],
            [InlineKeyboardButton("🔙 Back to Monitor", callback_data="live_monitor")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    async def _cb_start_monitor(self, query, param, user_id):
        if param:
            await self.monitor.start_monitoring_channel(param)
            await query.answer("✅ Monitoring started!", show_alert=True)
            await self._cb_view_channel_live(query, param, user_id)

    async def _cb_stop_monitor(self, query, param, user_id):
        if param:
            await self.monitor.stop_monitoring_channel(param)
            await query.answer("⏹️ Monitoring stopped!", show_alert=True)
            await self._cb_view_channel_live(query, param, user_id)

    # ==================== MESSAGE HANDLERS ====================

    async def _cb_edit_text(self, query, param, user_id):
        self.user_states[user_id] = {'action': 'waiting_message_text'}
        current = self.db.get_active_message('default')
        current_text = current.get('text', 'Not set') if current else 'Not set'
        text = (
            f"✏️ **Edit Message Text**\\n\\n"
            f"**Current text:**\\n```\\n{current_text[:300]}\\n```\\n\\n"
            f"Send the new message text.\\n"
            f"Variables: {{first_name}}, {{username}}, {{channel}}\\n\\n"
            f"Type /cancel to abort."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="set_message")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_set_image(self, query, param, user_id):
        self.user_states[user_id] = {'action': 'waiting_image'}
        text = "🖼️ **Set Image**\\n\\nSend me an image or an image URL.\\n\\nType /cancel to abort."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="set_message")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_set_link(self, query, param, user_id):
        self.user_states[user_id] = {'action': 'waiting_link'}
        current = self.db.get_active_message('default')
        current_link = current.get('link_url', 'Not set') if current else 'Not set'
        text = f"🔗 **Set Link**\\n\\nCurrent: {current_link}\\n\\nSend the link URL.\\n\\nType /cancel to abort."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="set_message")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_reset_message(self, query, param, user_id):
        self.db.reset_message('default')
        await query.answer("✅ Message reset!", show_alert=True)
        await self._cb_set_message(query, param, user_id)

    async def _cb_reset_dm(self, query, param, user_id):
        self.db.reset_dm_log()
        await query.answer("✅ DM log reset!", show_alert=True)
        await self._cb_set_message(query, param, user_id)

    async def _cb_view_message(self, query, param, user_id):
        message = self.db.get_active_message('default')
        if not message:
            text = "No message set."
        else:
            text = (
                f"✉️ **Full Message Preview**\\n\\n"
                f"**Text:**\\n{message.get('text', 'N/A')}\\n\\n"
                f"**Image:** {'✅' if message.get('image_path') or message.get('image_url') else '❌'}\\n"
                f"**Link:** {message.get('link_url', '❌')}\\n"
            )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="set_message")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    # ==================== ACCOUNT HANDLERS ====================

    async def _cb_add_account(self, query, param, user_id):
        self.user_states[user_id] = {'action': 'waiting_phone'}
        text = (
            "👤 **Add Telegram Account**\\n\\n"
            "Please enter the phone number with country code:\\n"
            "Example: `+919876543210`\\n\\n"
            "Type /cancel to abort."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="account")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_list_accounts(self, query, param, user_id):
        accounts = self.db.get_all_accounts()
        if not accounts:
            text = "No accounts found."
        else:
            text = "📋 **Account List:**\\n\\n"
            for acc in accounts:
                status = "🟢 Logged In" if acc['is_logged_in'] else "🔴 Not Logged In"
                name = f"{acc.get('first_name', '')} {acc.get('last_name', '')}".strip() or 'N/A'
                text += f"📱 {acc['phone']}\\n   Name: {name}\\n   Status: {status}\\n\\n"
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="account")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_delete_account(self, query, param, user_id):
        accounts = self.db.get_all_accounts()
        if not accounts:
            await query.answer("No accounts to delete", show_alert=True)
            return
        keyboard_buttons = []
        for acc in accounts:
            name = acc.get('first_name') or acc['phone']
            keyboard_buttons.append([
                InlineKeyboardButton(f"🗑️ {name} ({acc['phone']})", callback_data=f"confirm_delete_acc|{acc['phone']}")
            ])
        keyboard_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="account")])
        await query.edit_message_text(
            "🗑️ **Select account to delete:**",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons),
            parse_mode=ParseMode.MARKDOWN
        )

    # ==================== ADMIN HANDLERS ====================

    async def _cb_add_admin(self, query, param, user_id):
        if not self.db.is_admin(user_id):
            await query.answer("⛔ Not authorized!", show_alert=True)
            return
        self.user_states[user_id] = {'action': 'waiting_new_admin'}
        text = "➕ **Add New Admin**\\n\\nSend the Telegram User ID of the new admin.\\n\\nType /cancel to abort."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_remove_admin(self, query, param, user_id):
        if not self.db.is_admin(user_id):
            await query.answer("⛔ Not authorized!", show_alert=True)
            return
        admins = self.db.get_all_admins()
        keyboard_buttons = []
        for a in admins:
            if a['user_id'] != self.config.admin_id:  # Can't remove super admin
                keyboard_buttons.append([
                    InlineKeyboardButton(f"🗑️ {a['user_id']}", callback_data=f"confirm_remove_admin|{a['user_id']}")
                ])
        keyboard_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(
            "🗑️ **Select admin to remove:**",
            reply_markup=InlineKeyboardMarkup(keyboard_buttons),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_list_admins(self, query, param, user_id):
        admins = self.db.get_all_admins()
        text = "📋 **Admin List:**\\n\\n"
        for a in admins:
            text += f"• `{a['user_id']}` (Added: {a.get('added_at', 'N/A')})\\n"
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_bot_stats(self, query, param, user_id):
        if not self.db.is_admin(user_id):
            await query.answer("⛔ Not authorized!", show_alert=True)
            return
        stats = self.db.get_stats()
        channels = self.db.get_channels()
        text = (
            f"📊 **Full Bot Statistics**\\n━━━━━━━━━━━━━━━━━━\\n\\n"
            f"👤 Accounts: {stats['total_accounts']} (Online: {stats['logged_in_accounts']})\\n"
            f"📢 Channels: {stats['total_channels']}\\n"
            f"🔴 Active Lives: {stats['active_live_sessions']}\\n"
            f"📨 Today DMs: {stats['today_dms']}\\n"
            f"📨 Total DMs: {stats['total_dms_sent']}\\n\\n"
            f"**Channels:**\\n"
        )
        for ch in channels:
            dm_count = self.db.get_dm_count_for_channel(ch['channel_id'])
            text += f"• {ch.get('channel_title', 'N/A')}: {dm_count} DMs\\n"

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    async def _cb_reset_all_dms(self, query, param, user_id):
        if not self.db.is_admin(user_id):
            await query.answer("⛔ Not authorized!", show_alert=True)
            return
        self.db.reset_dm_log()
        await query.answer("✅ All DM logs reset!", show_alert=True)
        await self._cb_admin_panel(query, param, user_id)

    # ==================== MESSAGE HANDLERS ====================

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        if text == '/cancel':
            self.user_states.pop(user_id, None)
            await update.message.reply_text("❌ Action cancelled.")
            return

        state = self.user_states.get(user_id, {})
        if not state:
            if self.db.is_admin(user_id):
                keyboard = self._build_main_menu(user_id)
                await update.message.reply_text("📋 What would you like to do?", reply_markup=keyboard)
            return

        action = state.get('action')

        if action == 'waiting_channel_id':
            await self._handle_channel_id_input(update, user_id, text)
        elif action == 'waiting_phone':
            await self._handle_phone_input(update, user_id, text)
        elif action == 'waiting_otp':
            await self._handle_otp_input(update, user_id, text)
        elif action == 'waiting_2fa':
            await self._handle_2fa_input(update, user_id, text)
        elif action == 'waiting_message_text':
            await self._handle_message_text_input(update, user_id, text)
        elif action == 'waiting_link':
            await self._handle_link_input(update, user_id, text)
        elif action == 'waiting_new_admin':
            await self._handle_new_admin_input(update, user_id, text)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo upload for message image"""
        user_id = update.effective_user.id
        state = self.user_states.get(user_id, {})
        if state.get('action') == 'waiting_image':
            self.user_states.pop(user_id, None)
            photo_file = await update.message.photo[-1].get_file()
            photo_path = Path(f"data/images/msg_image_{user_id}.jpg")
            photo_path.parent.mkdir(parents=True, exist_ok=True)
            await photo_file.download_to_drive(str(photo_path))
            self.db.save_message(name='default', text=self._get_current_msg_text(), image_path=str(photo_path))
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Messages", callback_data="set_message")
            ]])
            await update.message.reply_text("✅ **Image saved for DM!**", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

    def _get_current_msg_text(self):
        msg = self.db.get_active_message('default')
        return msg.get('text', 'Hello {first_name}!') if msg else 'Hello {first_name}!'

    # ==================== INPUT HANDLERS ====================

    async def _handle_channel_id_input(self, update, user_id, channel_input):
        self.user_states.pop(user_id, None)
        status_msg = await update.message.reply_text("⏳ Processing channel...")
        accounts = self.db.get_logged_in_accounts()
        if not accounts:
            await status_msg.edit_text(
                "⚠️ No accounts logged in! Add account first.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Go to Account", callback_data="account")
                ]])
            )
            return

        result = await self.monitor.join_channel(accounts[0]['phone'], channel_input)
        if result.get('success'):
            await status_msg.edit_text(
                f"✅ **Channel Joined!**\\n\\n📢 **{result.get('title', 'Unknown')}**\\n"
                f"🆔 `{result.get('channel_id', 'N/A')}`\\n\\n🔍 Monitoring started!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add Another", callback_data="add_channel")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")],
                ])
            )
        else:
            await status_msg.edit_text(
                f"❌ **Failed!**\\n\\nError: {result.get('error', 'Unknown')}\\n\\n"
                f"Make sure:\\n• Channel ID/link is correct\\n• Account can join channels",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="add_channel")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="back_menu")],
                ])
            )

    async def _handle_phone_input(self, update, user_id, phone):
        self.db.add_account(phone)
        status_msg = await update.message.reply_text("⏳ Sending OTP...")
        result = await self.monitor.login_account(phone)
        if result.get('already_logged_in'):
            self.user_states.pop(user_id, None)
            await status_msg.edit_text(
                f"✅ Already logged in as **{result.get('first_name', 'User')}**!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="account")
                ]])
            )
        elif result.get('needs_otp'):
            self.user_states[user_id] = {
                'action': 'waiting_otp',
                'phone': phone,
                'phone_code_hash': result['phone_code_hash']
            }
            await status_msg.edit_text(
                "📱 **OTP sent!**\\n\\nPlease enter the verification code you received.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            self.user_states.pop(user_id, None)
            await status_msg.edit_text(
                f"❌ Failed: {result.get('error', 'Unknown error')}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try Again", callback_data="add_account")
                ]])
            )

    async def _handle_otp_input(self, update, user_id, code):
        state = self.user_states.get(user_id, {})
        phone = state.get('phone', '')
        phone_code_hash = state.get('phone_code_hash', '')
        status_msg = await update.message.reply_text("⏳ Verifying OTP...")
        result = await self.monitor.verify_otp(phone, code, phone_code_hash)

        if result.get('success'):
            self.user_states.pop(user_id, None)
            await status_msg.edit_text(
                f"✅ **Login successful!**\\n\\nWelcome, **{result.get('first_name', 'User')}**!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="account")
                ]])
            )
        elif '2FA' in str(result.get('error', '')) or 'password' in str(result.get('error', '')).lower():
            self.user_states[user_id] = {
                'action': 'waiting_2fa',
                'phone': phone
            }
            await status_msg.edit_text(
                "🔐 **2FA Required!**\\n\\nPlease enter your 2FA password.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            self.user_states.pop(user_id, None)
            await status_msg.edit_text(
                f"❌ Failed: {result.get('error', 'Unknown')}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try Again", callback_data="add_account")
                ]])
            )

    async def _handle_2fa_input(self, update, user_id, password):
        state = self.user_states.get(user_id, {})
        phone = state.get('phone', '')
        status_msg = await update.message.reply_text("⏳ Verifying password...")
        result = await self.monitor.verify_2fa_password(phone, password)
        self.user_states.pop(user_id, None)

        if result.get('success'):
            await status_msg.edit_text(
                f"✅ **Login successful!**\\n\\nWelcome, **{result.get('first_name', 'User')}**!",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="account")
                ]])
            )
        else:
            await status_msg.edit_text(
                f"❌ Failed: {result.get('error', 'Unknown')}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try Again", callback_data="add_account")
                ]])
            )

    async def _handle_message_text_input(self, update, user_id, text):
        self.user_states.pop(user_id, None)
        self.db.save_message(name='default', text=text)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Messages", callback_data="set_message")
        ]])
        await update.message.reply_text(
            "✅ **Message text saved!**",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    async def _handle_link_input(self, update, user_id, link):
        self.user_states.pop(user_id, None)
        self.db.save_message(
            name='default',
            text=self._get_current_msg_text(),
            link_url=link
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Messages", callback_data="set_message")
        ]])
        await update.message.reply_text(
            f"✅ **Link saved!**\\n🔗 {link}",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )

    async def _handle_new_admin_input(self, update, user_id, new_admin_id_text):
        self.user_states.pop(user_id, None)
        try:
            new_admin_id = int(new_admin_id_text.strip())
            if self.db.add_admin(new_admin_id, user_id):
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_panel")
                ]])
                await update.message.reply_text(
                    f"✅ **Admin added!**\\n\\nUser ID: `{new_admin_id}`",
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text(
                    "⚠️ User is already an admin!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
                    ]])
                )
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid User ID! Please send a numeric ID.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
                ]])
            )
