#!/usr/bin/env python3
"""
Telegram Live Stream Monitor Bot
Monitors channels for live streams and sends DMs to viewers.
Built with Telethon + Firebase Realtime Database.
"""

import os
import json
import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events, types, functions, errors
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument,
    InputPeerChannel, InputPeerUser, PeerChannel, PeerUser
)
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
import firebase_admin
from firebase_admin import credentials, db

load_dotenv()

# ==================== CONFIG ====================
API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8710003468:AAFou6EOMDf0L7tr2cId3K2dwDbR-6AfQXM")
ADMIN_IDS = [5844447576]  # Initial admin ID
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "firebase-cred.json")

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== FIREBASE ====================
firebase_app = None
firebase_ref = None


def init_firebase():
    global firebase_app, firebase_ref
    try:
        if FIREBASE_DB_URL:
            cred = credentials.Certificate(FIREBASE_CRED_PATH)
            firebase_app = firebase_admin.initialize_app(cred, {
                'databaseURL': FIREBASE_DB_URL
            })
            firebase_ref = db.reference('/')
            logger.info("Firebase initialized successfully")
        else:
            logger.warning("FIREBASE_DB_URL not set — using local JSON fallback")
    except Exception as e:
        logger.error(f"Firebase init failed: {e}")


def fb_get(path, default=None):
    """Get data from Firebase or local fallback."""
    if firebase_ref:
        return firebase_ref.child(path).get() or default
    return local_db.get(path, default)


def fb_set(path, value):
    """Set data in Firebase or local fallback."""
    if firebase_ref:
        firebase_ref.child(path).set(value)
    else:
        local_db_set(path, value)


def fb_update(path, value):
    """Update data in Firebase or local fallback."""
    if firebase_ref:
        firebase_ref.child(path).update(value)
    else:
        current = local_db.get(path, {}) or {}
        current.update(value)
        local_db_set(path, current)


def fb_push(path, value):
    """Push data to Firebase list or local fallback."""
    if firebase_ref:
        return firebase_ref.child(path).push(value).key
    else:
        lst = local_db.get(path, []) or []
        key = f"push_{len(lst)}_{int(datetime.now().timestamp())}"
        value['_key'] = key
        lst.append(value)
        local_db_set(path, lst)
        return key


def fb_delete(path):
    """Delete data from Firebase or local fallback."""
    if firebase_ref:
        firebase_ref.child(path).delete()
    else:
        if path in local_db:
            del local_db[path]
        save_local_db()


# Local JSON fallback
LOCAL_DB_PATH = Path("local_db.json")
local_db = {}


def load_local_db():
    global local_db
    if LOCAL_DB_PATH.exists():
        with open(LOCAL_DB_PATH, 'r') as f:
            local_db = json.load(f)


def save_local_db():
    with open(LOCAL_DB_PATH, 'w') as f:
        json.dump(local_db, f, indent=2, default=str)


def local_db_set(path, value):
    parts = path.strip('/').split('/')
    d = local_db
    for part in parts[:-1]:
        if part not in d:
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value
    save_local_db()


# ==================== BOT STATE ====================
class BotState:
    """Manages bot runtime state."""

    def __init__(self):
        self.client: TelegramClient = None
        self.bot_client: TelegramClient = None
        self.me = None
        self.bot_me = None
        self.monitoring_tasks: dict = {}  # channel_id -> asyncio.Task
        self.dm_sent_cache: dict = {}  # channel_id -> {user_id: timestamp}
        self.active_lives: dict = {}  # channel_id -> live_info

    def is_admin(self, user_id: int) -> bool:
        admins = fb_get('admins', []) or []
        return user_id in admins or user_id in ADMIN_IDS

    def add_admin(self, user_id: int):
        admins = fb_get('admins', []) or []
        if user_id not in admins:
            admins.append(user_id)
            fb_set('admins', admins)

    def remove_admin(self, user_id: int):
        admins = fb_get('admins', []) or []
        if user_id in admins:
            admins.remove(user_id)
            fb_set('admins', admins)


state = BotState()


# ==================== TELEGRAM CLIENT SETUP ====================
async def init_clients():
    """Initialize both user client and bot client."""
    # User client (for monitoring channels)
    state.client = TelegramClient('user_session', API_ID, API_HASH)
    await state.client.start()
    state.me = await state.client.get_me()
    logger.info(f"User client logged in as: {state.me.first_name} (@{state.me.username})")

    # Bot client (for bot-specific operations)
    state.bot_client = TelegramClient('bot_session', API_ID, API_HASH)
    await state.bot_client.start(bot_token=BOT_TOKEN)
    state.bot_me = await state.bot_client.get_me()
    logger.info(f"Bot client logged in as: @{state.bot_me.username}")


# ==================== CHANNEL MANAGEMENT ====================
async def join_channel(channel_identifier: str) -> dict:
    """Join a channel by username or invite link."""
    try:
        # Parse channel identifier
        if channel_identifier.startswith('https://t.me/'):
            # Extract from link: https://t.me/username or https://t.me/+invitehash
            channel_identifier = channel_identifier.replace('https://t.me/', '').strip()
            if channel_identifier.startswith('+'):
                # Private invite link
                try:
                    update = await state.client(
                        functions.messages.ImportChatInviteRequest(hash=channel_identifier[1:])
                    )
                    entity = update.chats[0] if update.chats else None
                except errors.InviteHashExpiredError:
                    return {"success": False, "error": "Invite link expired"}
                except errors.InviteHashInvalidError:
                    return {"success": False, "error": "Invalid invite link"}
            else:
                entity = await state.client.get_entity(channel_identifier)
        else:
            entity = await state.client.get_entity(channel_identifier)

        # Try to join
        try:
            await state.client(JoinChannelRequest(entity))
        except errors.FloodWaitError as e:
            return {"success": False, "error": f"Flood wait: {e.seconds}s"}
        except Exception as e:
            if "already" in str(e).lower():
                pass  # Already joined
            else:
                return {"success": False, "error": str(e)}

        channel_id = str(entity.id)
        channel_info = {
            "id": channel_id,
            "title": getattr(entity, 'title', channel_identifier),
            "username": getattr(entity, 'username', ''),
            "access_hash": str(getattr(entity, 'access_hash', '0')),
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True,
            "total_dm_sent": 0,
            "last_live_at": None
        }

        # Save to database
        fb_update(f'channels/{channel_id}', channel_info)

        logger.info(f"Joined channel: {entity.title}")
        return {"success": True, "channel": channel_info}

    except ValueError as e:
        return {"success": False, "error": f"Channel not found: {str(e)}"}
    except Exception as e:
        logger.error(f"Error joining channel: {e}")
        return {"success": False, "error": str(e)}


async def get_joined_channels() -> list:
    """Get all joined channels."""
    channels = fb_get('channels', {}) or {}
    return list(channels.values())


async def remove_channel(channel_id: str) -> bool:
    """Remove a channel from monitoring."""
    # Cancel monitoring task
    if channel_id in state.monitoring_tasks:
        state.monitoring_tasks[channel_id].cancel()
        del state.monitoring_tasks[channel_id]

    fb_delete(f'channels/{channel_id}')
    fb_delete(f'dm_sent/{channel_id}')
    if channel_id in state.active_lives:
        del state.active_lives[channel_id]
    return True


# ==================== LIVE STREAM DETECTION ====================
async def check_live_stream(channel_id: str, channel_info: dict) -> bool:
    """Check if a channel is currently live streaming."""
    try:
        entity = await state.client.get_entity(int(channel_id))
        messages = await state.client.get_messages(entity, limit=5)

        for msg in messages:
            if msg is None:
                continue
            # Check for live stream indicators
            text = (msg.message or '').lower()
            media = msg.media

            # Telegram voice/video chat = live stream
            if hasattr(media, 'action') or hasattr(media, 'participants'):
                return True

            # Common live stream indicators in message
            live_keywords = ['🔴 live', 'stream started', 'is live', 'live now',
                           'broadcasting', '#live', 'live stream']
            if any(kw in text for kw in live_keywords):
                return True

            # Check action types
            if hasattr(msg, 'action') and msg.action:
                action_type = str(type(msg.action).__name__).lower()
                if 'live' in action_type or 'stream' in action_type:
                    return True

        return False

    except Exception as e:
        logger.error(f"Error checking live status for {channel_id}: {e}")
        return False


async def get_live_viewers(channel_id: str) -> list:
    """Get current live stream viewers/participants."""
    try:
        entity = await state.client.get_entity(int(channel_id))
        full_chat = await state.client(GetFullChannelRequest(channel=entity))

        participants = []
        if hasattr(full_chat, 'full_chat'):
            fc = full_chat.full_chat
            if hasattr(fc, 'participants_count'):
                logger.info(f"Channel {channel_id} has {fc.participants_count} participants")

        # Get recent participants from last live-related messages
        messages = await state.client.get_messages(entity, limit=20)
        viewers = set()

        for msg in messages:
            if msg and msg.from_id:
                user_id = None
                if isinstance(msg.from_id, types.PeerUser):
                    user_id = msg.from_id.user_id

                if user_id and user_id > 0:
                    viewers.add(user_id)

            # Check for reactions / views on live message
            if msg and hasattr(msg, 'views') and msg.views and msg.views > 0:
                pass  # Has views, likely live content

        return list(viewers)

    except Exception as e:
        logger.error(f"Error getting live viewers for {channel_id}: {e}")
        return []


# ==================== DM SENDING LOGIC ====================
def get_dm_message():
    """Get the configured DM message."""
    return fb_get('dm_config/message', "👋 Hi! I noticed you're watching this live stream. Check out our community!")


def get_dm_media():
    """Get configured DM media info."""
    return fb_get('dm_config/media', {})


def has_received_dm(channel_id: str, user_id: int) -> bool:
    """Check if a user has already received a DM from this channel."""
    sent = fb_get(f'dm_sent/{channel_id}', {}) or {}
    return str(user_id) in sent


def mark_dm_sent(channel_id: str, user_id: int):
    """Mark that a DM was sent to a user."""
    fb_update(f'dm_sent/{channel_id}', {
        str(user_id): datetime.now(timezone.utc).isoformat()
    })
    # Increment total count
    current = fb_get(f'channels/{channel_id}/total_dm_sent', 0) or 0
    fb_set(f'channels/{channel_id}/total_dm_sent', current + 1)


async def send_dm_to_user(user_id: int, message: str, media_info: dict = None) -> bool:
    """Send a DM to a specific user."""
    try:
        entity = await state.client.get_entity(user_id)

        # Send media if configured
        if media_info and media_info.get('file_id'):
            file_path = media_info.get('file_path', '')
            if file_path and os.path.exists(file_path):
                await state.client.send_file(
                    entity,
                    file_path,
                    caption=message
                )
            else:
                await state.client.send_message(entity, message)
        else:
            await state.client.send_message(entity, message)

        return True

    except errors.PeerFloodError:
        logger.warning(f"Flood error for user {user_id}")
        return False
    except errors.UserPrivacyRestrictedError:
        logger.warning(f"Privacy restricted: {user_id}")
        return False
    except errors.UserBlockedError:
        logger.warning(f"User blocked: {user_id}")
        return False
    except Exception as e:
        logger.error(f"Error sending DM to {user_id}: {e}")
        return False


async def send_dms_to_viewers(channel_id: str, viewers: list, channel_info: dict):
    """Send DMs to all viewers who haven't received one yet."""
    message = get_dm_message()
    media = get_dm_media()
    sent_count = 0
    failed_count = 0

    for user_id in viewers:
        if user_id == state.me.id:
            continue  # Don't DM yourself
        if has_received_dm(channel_id, user_id):
            continue

        # Small delay to avoid flooding
        await asyncio.sleep(2)

        success = await send_dm_to_user(user_id, message, media)
        if success:
            mark_dm_sent(channel_id, user_id)
            sent_count += 1
            logger.info(f"DM sent to {user_id} from channel {channel_id}")
        else:
            failed_count += 1

    return {"sent": sent_count, "failed": failed_count}


# ==================== MONITORING LOOP ====================
async def monitor_channel(channel_id: str):
    """Continuous monitoring loop for a single channel."""
    channel_info = fb_get(f'channels/{channel_id}', {})

    logger.info(f"Starting monitoring for channel: {channel_info.get('title', channel_id)}")

    while True:
        try:
            is_live = await check_live_stream(channel_id, channel_info)

            if is_live:
                # Channel is live
                if channel_id not in state.active_lives:
                    state.active_lives[channel_id] = {
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "viewer_count": 0,
                        "dm_sent_this_session": 0
                    }
                    fb_update(f'channels/{channel_id}', {
                        "last_live_at": datetime.now(timezone.utc).isoformat(),
                        "is_currently_live": True
                    })

                viewers = await get_live_viewers(channel_id)
                state.active_lives[channel_id]["viewer_count"] = len(viewers)

                if viewers:
                    result = await send_dms_to_viewers(channel_id, viewers, channel_info)
                    state.active_lives[channel_id]["dm_sent_this_session"] += result["sent"]
                    fb_update(f'channels/{channel_id}', {
                        "current_viewers": len(viewers),
                        "session_dm_sent": state.active_lives[channel_id]["dm_sent_this_session"]
                    })

                logger.info(f"Channel {channel_id} is LIVE — {len(viewers)} viewers, DMs sent this session: {state.active_lives[channel_id]['dm_sent_this_session']}")

            else:
                # Channel is not live
                if channel_id in state.active_lives:
                    del state.active_lives[channel_id]
                    fb_update(f'channels/{channel_id}', {"is_currently_live": False})

            # Wait before next check
            await asyncio.sleep(30)  # Check every 30 seconds

        except asyncio.CancelledError:
            logger.info(f"Monitoring cancelled for channel {channel_id}")
            break
        except Exception as e:
            logger.error(f"Error in monitor loop for {channel_id}: {e}")
            await asyncio.sleep(60)


async def start_all_monitoring():
    """Start monitoring for all active channels."""
    channels = await get_joined_channels()
    for ch in channels:
        ch_id = ch.get('id')
        if ch_id and ch_id not in state.monitoring_tasks:
            task = asyncio.create_task(monitor_channel(ch_id))
            state.monitoring_tasks[ch_id] = task
    logger.info(f"Started monitoring {len(state.monitoring_tasks)} channels")


# ==================== ACCOUNT MANAGEMENT ====================
async def login_account(phone_number: str) -> dict:
    """Start login process for a new account."""
    try:
        # Create a new client for this account
        session_name = f"account_{phone_number.replace('+', '').replace(' ', '')}"
        new_client = TelegramClient(session_name, API_ID, API_HASH)

        await new_client.connect()
        sent_code = await new_client.send_code_request(phone_number)

        # Store the phone_hash for later verification
        accounts_pending = fb_get('pending_accounts', {}) or {}
        accounts_pending[phone_number] = {
            "phone_code_hash": sent_code.phone_code_hash,
            "session_name": session_name,
            "attempted_at": datetime.now(timezone.utc).isoformat()
        }
        fb_set('pending_accounts', accounts_pending)

        return {"success": True, "message": "OTP sent successfully"}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def verify_account(phone_number: str, otp_code: str, password: str = None) -> dict:
    """Verify OTP and complete account login."""
    pending = fb_get(f'pending_accounts/{phone_number}', {})
    if not pending:
        return {"success": False, "error": "No pending login for this number"}

    try:
        session_name = pending['session_name']
        phone_code_hash = pending['phone_code_hash']

        new_client = TelegramClient(session_name, API_ID, API_HASH)
        await new_client.connect()

        try:
            await new_client.sign_in(
                phone=phone_number,
                code=otp_code,
                phone_code_hash=phone_code_hash
            )
        except errors.SessionPasswordNeededError:
            if not password:
                return {"success": False, "error": "2FA password required", "need_password": True}
            await new_client.sign_in(password=password)

        me = await new_client.get_me()

        # Save account
        account_info = {
            "phone": phone_number,
            "user_id": me.id,
            "first_name": me.first_name,
            "username": me.username or "",
            "added_at": datetime.now(timezone.utc).isoformat(),
            "session_name": session_name,
            "is_active": True
        }

        accounts = fb_get('accounts', {}) or {}
        accounts[str(me.id)] = account_info
        fb_set('accounts', accounts)

        # Clear pending
        fb_delete(f'pending_accounts/{phone_number}')

        await new_client.disconnect()
        return {"success": True, "account": account_info}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== DM MESSAGE CONFIG ====================
def set_dm_message(text: str):
    """Set the DM message text."""
    current = fb_get('dm_config', {}) or {}
    current['message'] = text
    fb_set('dm_config', current)


def set_dm_image(file_id: str, file_path: str):
    """Set DM media image."""
    current = fb_get('dm_config', {}) or {}
    current['media'] = {
        'type': 'image',
        'file_id': file_id,
        'file_path': file_path
    }
    fb_set('dm_config', current)


def reset_dm_config():
    """Reset DM message and media to defaults."""
    fb_set('dm_config', {
        'message': "👋 Hi! I noticed you're watching this live stream. Check out our community!"
    })


def reset_dm_sent(channel_id: str = None):
    """Reset DM sent records — so users can receive DMs again."""
    if channel_id:
        fb_delete(f'dm_sent/{channel_id}')
        fb_set(f'channels/{channel_id}/total_dm_sent', 0)
    else:
        fb_delete('dm_sent')


# ==================== STATS ====================
def get_stats() -> dict:
    """Get overall bot statistics."""
    channels = fb_get('channels', {}) or {}
    accounts = fb_get('accounts', {}) or {}
    dm_config = fb_get('dm_config', {})

    total_channels = len(channels)
    total_accounts = len(accounts)
    active_lives = len(state.active_lives)
    total_dm_sent = sum(
        ch.get('total_dm_sent', 0) for ch in channels.values()
    )

    # Channel-level stats
    channel_stats = []
    for ch_id, ch in channels.items():
        dm_sent_for_ch = fb_get(f'dm_sent/{ch_id}', {}) or {}
        channel_stats.append({
            "id": ch_id,
            "title": ch.get('title', 'Unknown'),
            "username": ch.get('username', ''),
            "is_live": ch_id in state.active_lives,
            "total_dm_sent": ch.get('total_dm_sent', 0),
            "unique_users_dmed": len(dm_sent_for_ch),
            "joined_at": ch.get('joined_at', '')
        })

    return {
        "total_channels": total_channels,
        "total_accounts": total_accounts,
        "active_lives": active_lives,
        "total_dm_sent": total_dm_sent,
        "dm_message": dm_config.get('message', 'Not set'),
        "has_media": bool(dm_config.get('media')),
        "channels": channel_stats,
        "admins": fb_get('admins', []),
        "accounts_list": list(accounts.values())
    }


# ==================== BOT COMMAND HANDLERS ====================
async def setup_bot_handlers():
    """Setup bot command handlers for admin operations via Telegram."""

    @state.bot_client.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        user_id = event.sender_id
        welcome = (
            f"🤖 **Live Stream Monitor Bot**\n\n"
            f"Welcome {event.sender.first_name}!\n\n"
            f"📊 /stats - View bot statistics\n"
            f"📺 /channels - List monitored channels\n"
            f"👤 /accounts - List connected accounts\n"
            f"💬 /setmsg <text> - Set DM message\n"
            f"🔄 /resetdm - Reset DM records\n"
            f"➕ /addchannel <link/username> - Add channel\n"
            f"ℹ️ /help - Show help\n\n"
            f"Admins can manage via the web dashboard."
        )
        await event.respond(welcome)

    @state.bot_client.on(events.NewMessage(pattern='/stats'))
    async def stats_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        stats = get_stats()
        msg = (
            f"📊 **Bot Statistics**\n\n"
            f"📺 Channels: {stats['total_channels']}\n"
            f"👤 Accounts: {stats['total_accounts']}\n"
            f"🔴 Active Lives: {stats['active_lives']}\n"
            f"✉️ Total DMs Sent: {stats['total_dm_sent']}\n"
            f"💬 DM Message: {stats['dm_message'][:100]}..."
        )
        await event.respond(msg)

    @state.bot_client.on(events.NewMessage(pattern='/channels'))
    async def channels_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        stats = get_stats()
        if not stats['channels']:
            await event.respond("No channels added yet.")
            return
        msg = "📺 **Monitored Channels:**\n\n"
        for ch in stats['channels']:
            status = "🔴 LIVE" if ch['is_live'] else "⚫ Offline"
            msg += f"• {ch['title']} ({status}) — {ch['total_dm_sent']} DMs sent\n"
        await event.respond(msg)

    @state.bot_client.on(events.NewMessage(pattern=r'/setmsg (.+)'))
    async def setmsg_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        text = event.pattern_match.group(1)
        set_dm_message(text)
        await event.respond(f"✅ DM message set to:\n{text}")

    @state.bot_client.on(events.NewMessage(pattern='/resetdm'))
    async def resetdm_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        reset_dm_sent()
        await event.respond("✅ DM records reset. Users can receive DMs again.")

    @state.bot_client.on(events.NewMessage(pattern=r'/addchannel (.+)'))
    async def addchannel_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        identifier = event.pattern_match.group(1)
        await event.respond(f"⏳ Joining channel: {identifier}...")
        result = await join_channel(identifier)
        if result['success']:
            ch_id = result['channel']['id']
            task = asyncio.create_task(monitor_channel(ch_id))
            state.monitoring_tasks[ch_id] = task
            await event.respond(f"✅ Joined and monitoring: {result['channel']['title']}")
        else:
            await event.respond(f"❌ Failed: {result['error']}")

    @state.bot_client.on(events.NewMessage(pattern='/accounts'))
    async def accounts_handler(event):
        if not state.is_admin(event.sender_id):
            await event.respond("❌ Admin only")
            return
        accounts = fb_get('accounts', {}) or {}
        if not accounts:
            await event.respond("No accounts connected.")
            return
        msg = "👤 **Connected Accounts:**\n\n"
        for acc_id, acc in accounts.items():
            msg += f"• {acc.get('first_name', 'Unknown')} ({acc.get('phone', 'N/A')})\n"
        await event.respond(msg)

    @state.bot_client.on(events.NewMessage(pattern='/help'))
    async def help_handler(event):
        help_text = (
            "📖 **Live Stream Monitor Bot Help**\n\n"
            "**How it works:**\n"
            "1. Add channels you want to monitor\n"
            "2. Set your DM message with /setmsg\n"
            "3. When someone goes live in a monitored channel,\n"
            "   the bot automatically sends DMs to viewers\n"
            "4. Each user gets DM only once per session\n\n"
            "**Commands:**\n"
            "/start - Welcome message\n"
            "/stats - Bot statistics\n"
            "/channels - List channels\n"
            "/accounts - List accounts\n"
            "/setmsg <text> - Set DM message\n"
            "/resetdm - Reset DM records\n"
            "/addchannel <link> - Add channel\n"
            "/help - This help\n\n"
            "🌐 Use the Web Dashboard for full control!"
        )
        await event.respond(help_text)

    logger.info("Bot command handlers registered")


# ==================== MAIN ====================
async def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("Telegram Live Stream Monitor Bot Starting...")
    logger.info("=" * 50)

    # Initialize
    load_local_db()
    init_firebase()

    # Start Telegram clients
    await init_clients()

    # Setup bot handlers
    await setup_bot_handlers()

    # Start monitoring all channels
    await start_all_monitoring()

    logger.info("✅ Bot is fully operational!")
    logger.info(f"User: {state.me.first_name}")
    logger.info(f"Bot: @{state.bot_me.username}")
    logger.info(f"Monitoring {len(state.monitoring_tasks)} channels")

    # Keep running
    await state.client.run_until_disconnected()


def run_bot():
    """Run the bot."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")


if __name__ == "__main__":
    run_bot()
