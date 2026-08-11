"""Telegram Live Monitor - Core module for monitoring live streams using Telethon"""

import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional, Set
from telethon import TelegramClient
from telethon.tl.types import (
    User, Channel, Chat, MessageMediaPhoto, MessageMediaDocument,
    InputPeerChannel, InputPeerUser
)
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, UserDeactivatedBanError,
    PeerFloodError, ChatAdminRequiredError
)
from telethon.tl.functions.channels import JoinChannelRequest, GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsRecent
from telethon.tl.functions.messages import GetHistoryRequest

logger = logging.getLogger(__name__)


class LiveMonitor:
    """Monitors Telegram channels for live streams and sends DMs"""

    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.clients: Dict[str, TelegramClient] = {}  # phone -> client
        self.active_monitors: Dict[str, asyncio.Task] = {}  # channel_id -> task
        self.running = False

    async def init_accounts(self):
        """Initialize all logged-in accounts"""
        accounts = self.db.get_logged_in_accounts()
        for account in accounts:
            await self._init_client(account)
        logger.info(f"📱 Initialized {len(self.clients)} account clients")

    async def _init_client(self, account: Dict):
        """Create or restore a Telethon client for an account"""
        phone = account['phone']
        if phone in self.clients:
            return self.clients[phone]

        session_file = f"data/{phone.replace('+', '')}"
        client = TelegramClient(
            session_file,
            self.config.api_id,
            self.config.api_hash
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                if account.get('session_string'):
                    # Try to restore from session string if available
                    pass
                else:
                    logger.warning(f"⚠️ Account {phone} not authorized")
                    await client.disconnect()
                    return

            self.clients[phone] = client
            logger.info(f"✅ Account {phone} connected")

        except Exception as e:
            logger.error(f"❌ Failed to connect {phone}: {e}")
            try:
                await client.disconnect()
            except:
                pass

    async def login_account(self, phone: str) -> Dict:
        """Login a Telegram account using phone number - returns phone_code_hash for OTP"""
        session_file = f"data/{phone.replace('+', '')}"
        client = TelegramClient(session_file, self.config.api_id, self.config.api_hash)

        try:
            await client.connect()

            if await client.is_user_authorized():
                me = await client.get_me()
                session_str = client.session.save() if hasattr(client.session, 'save') else ''
                self.db.update_account_session(phone, str(session_str))
                self.db.update_account_info(
                    phone,
                    first_name=getattr(me, 'first_name', ''),
                    last_name=getattr(me, 'last_name', ''),
                    username=getattr(me, 'username', '')
                )
                self.clients[phone] = client
                await client.disconnect()
                return {
                    "success": True,
                    "already_logged_in": True,
                    "first_name": getattr(me, 'first_name', '')
                }

            # Send OTP
            sent = await client.send_code_request(phone)
            return {
                "success": True,
                "needs_otp": True,
                "phone_code_hash": sent.phone_code_hash
            }

        except Exception as e:
            try:
                await client.disconnect()
            except:
                pass
            return {"success": False, "error": str(e)}

    async def verify_otp(self, phone: str, code: str, phone_code_hash: str) -> Dict:
        """Verify OTP and complete login"""
        session_file = f"data/{phone.replace('+', '')}"
        client = TelegramClient(session_file, self.config.api_id, self.config.api_hash)

        try:
            await client.connect()
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)

            me = await client.get_me()
            self.db.update_account_session(phone, str(session_file))
            self.db.update_account_info(
                phone,
                first_name=getattr(me, 'first_name', ''),
                last_name=getattr(me, 'last_name', ''),
                username=getattr(me, 'username', '')
            )
            self.clients[phone] = client
            await client.disconnect()

            return {
                "success": True,
                "first_name": getattr(me, 'first_name', '')
            }

        except Exception as e:
            try:
                await client.disconnect()
            except:
                pass
            return {"success": False, "error": str(e)}

    async def verify_2fa_password(self, phone: str, password: str) -> Dict:
        """Verify 2FA password"""
        session_file = f"data/{phone.replace('+', '')}"
        client = TelegramClient(session_file, self.config.api_id, self.config.api_hash)

        try:
            await client.connect()
            await client.sign_in(password=password)

            me = await client.get_me()
            self.db.update_account_session(phone, str(session_file))
            self.db.update_account_info(
                phone,
                first_name=getattr(me, 'first_name', ''),
                last_name=getattr(me, 'last_name', ''),
                username=getattr(me, 'username', '')
            )
            self.clients[phone] = client
            await client.disconnect()

            return {"success": True, "first_name": getattr(me, 'first_name', '')}

        except Exception as e:
            try:
                await client.disconnect()
            except:
                pass
            return {"success": False, "error": str(e)}

    async def join_channel(self, phone: str, channel_identifier: str) -> Dict:
        """Join a channel using an account"""
        client = self.clients.get(phone)
        if not client:
            # Try to connect
            accounts = self.db.get_logged_in_accounts()
            acct = next((a for a in accounts if a['phone'] == phone), None)
            if not acct:
                return {"success": False, "error": "Account not found"}
            await self._init_client(acct)
            client = self.clients.get(phone)
            if not client:
                return {"success": False, "error": "Failed to connect account"}

        try:
            if not client.is_connected():
                await client.connect()

            # Resolve the channel entity
            try:
                entity = await client.get_entity(channel_identifier)
            except ValueError:
                # Try joining by invite link
                if 't.me/' in channel_identifier or 'telegram.me/' in channel_identifier:
                    entity = await client.get_entity(channel_identifier)
                else:
                    return {"success": False, "error": "Channel not found. Check the ID or link."}

            # Join the channel
            await client(JoinChannelRequest(entity))

            channel_id = str(entity.id)
            title = getattr(entity, 'title', 'Unknown')
            username = getattr(entity, 'username', None)

            # Save to DB
            result = self.db.add_channel(
                channel_id=channel_id,
                title=title,
                username=username,
                invite_link=channel_identifier if 't.me/' in str(channel_identifier) else None
            )

            if result['success']:
                self.db.set_monitoring_state(channel_id, True)

            return {
                "success": True,
                "channel_id": channel_id,
                "title": title,
                "username": username
            }

        except FloodWaitError as e:
            return {"success": False, "error": f"Flood wait: {e.seconds}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def check_channel(self, phone: str, channel_identifier: str) -> Dict:
        """Check if bot can access a channel without joining"""
        client = self.clients.get(phone)
        if not client:
            accounts = self.db.get_logged_in_accounts()
            acct = next((a for a in accounts if a['phone'] == phone), None)
            if not acct:
                return {"success": False, "error": "Account not found"}
            await self._init_client(acct)
            client = self.clients.get(phone)
            if not client:
                return {"success": False, "error": "Failed to connect"}

        try:
            if not client.is_connected():
                await client.connect()

            entity = await client.get_entity(channel_identifier)
            return {
                "success": True,
                "channel_id": str(entity.id),
                "title": getattr(entity, 'title', 'Unknown'),
                "username": getattr(entity, 'username', None)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_live_participants(self, phone: str, channel_id: str) -> Dict:
        """Get active live stream participants from a channel"""
        client = self.clients.get(phone)
        if not client:
            accounts = self.db.get_logged_in_accounts()
            acct = next((a for a in accounts if a['phone'] == phone), None)
            if not acct:
                return {"success": False, "error": "Account not found", "participants": []}
            await self._init_client(acct)
            client = self.clients.get(phone)
            if not client:
                return {"success": False, "error": "Failed to connect", "participants": []}

        try:
            if not client.is_connected():
                await client.connect()

            entity = await client.get_entity(int(channel_id))

            # Get recent messages to detect live streams
            messages = await client.get_messages(entity, limit=10)

            live_users = []
            live_detected = False
            active_users_count = 0

            # Check for live stream indicators in recent messages
            for msg in messages:
                if msg and msg.action:
                    # Check for live stream actions
                    action_type = type(msg.action).__name__
                    if 'live' in action_type.lower() or 'stream' in action_type.lower():
                        live_detected = True

            # If we detect a live stream, get recent participants
            if live_detected or True:  # Always check - the flag might be missed
                try:
                    participants = await client.get_participants(entity, limit=200)
                    for p in participants:
                        if not p.bot and not p.deleted:
                            user_info = {
                                "user_id": p.id,
                                "first_name": getattr(p, 'first_name', ''),
                                "last_name": getattr(p, 'last_name', ''),
                                "username": getattr(p, 'username', ''),
                                "is_premium": getattr(p, 'premium', False),
                            }
                            live_users.append(user_info)
                    active_users_count = len(live_users)
                except Exception as e:
                    logger.warning(f"Could not get participants: {e}")

            return {
                "success": True,
                "live_detected": live_detected,
                "active_users": active_users_count,
                "participants": live_users,
                "channel_title": getattr(entity, 'title', 'Unknown')
            }

        except FloodWaitError as e:
            return {"success": False, "error": f"Flood wait: {e.seconds}s", "participants": []}
        except Exception as e:
            return {"success": False, "error": str(e), "participants": []}

    async def send_dm(self, phone: str, user_id: int, message_text: str,
                      image_path: str = None, link_url: str = None) -> Dict:
        """Send DM to a user from a specific account"""
        client = self.clients.get(phone)
        if not client:
            return {"success": False, "error": "Client not connected"}

        try:
            if not client.is_connected():
                await client.connect()

            recipient = await client.get_entity(int(user_id))

            # Send message
            sent_msg = None

            # If there's an image, send it first
            if image_path and Path(image_path).exists():
                sent_msg = await client.send_file(
                    recipient,
                    image_path,
                    caption=message_text
                )
            else:
                sent_msg = await client.send_message(recipient, message_text)

            # If there's a link, send it as a follow-up
            if link_url and sent_msg:
                await client.send_message(recipient, link_url)

            delay = int(self.db.get_setting('dm_delay_seconds', '3'))
            await asyncio.sleep(delay)

            return {
                "success": True,
                "message_id": sent_msg.id if sent_msg else None
            }

        except UserPrivacyRestrictedError:
            return {"success": False, "error": "User privacy restricted"}
        except UserDeactivatedBanError:
            return {"success": False, "error": "User deactivated"}
        except PeerFloodError:
            return {"success": False, "error": "Peer flood - too many messages"}
        except FloodWaitError as e:
            return {"success": False, "error": f"Flood wait: {e.seconds}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def start_monitoring_channel(self, channel_id: str):
        """Start monitoring a channel for live streams"""
        if channel_id in self.active_monitors:
            logger.info(f"Already monitoring {channel_id}")
            return

        task = asyncio.create_task(self._monitor_loop(channel_id))
        self.active_monitors[channel_id] = task
        self.db.set_monitoring_state(channel_id, True)
        logger.info(f"🔍 Started monitoring channel {channel_id}")

    async def stop_monitoring_channel(self, channel_id: str):
        """Stop monitoring a channel"""
        if channel_id in self.active_monitors:
            self.active_monitors[channel_id].cancel()
            del self.active_monitors[channel_id]
            self.db.set_monitoring_state(channel_id, False)
            logger.info(f"⏹️ Stopped monitoring channel {channel_id}")

    async def _monitor_loop(self, channel_id: str):
        """Main monitoring loop for a channel"""
        logger.info(f"🔄 Monitor loop started for {channel_id}")

        while self.running:
            try:
                accounts = self.db.get_logged_in_accounts()
                if not accounts:
                    await asyncio.sleep(30)
                    continue

                # Rotate accounts for checking
                account = accounts[0]  # Use first available account

                result = await self.get_live_participants(account['phone'], channel_id)

                if result.get('success') and result.get('live_detected'):
                    participants = result.get('participants', [])
                    if participants:
                        # Start live session if not already active
                        active_sessions = self.db.get_active_live_sessions()
                        channel_active = any(
                            s['channel_id'] == channel_id for s in active_sessions
                        )
                        if not channel_active:
                            self.db.start_live_session(channel_id)

                        # Send DMs to new participants
                        await self._send_dms_to_participants(
                            channel_id, participants, accounts
                        )
                else:
                    # No live detected, end any active session
                    self.db.end_live_session(channel_id)

                # Wait before next check (30 seconds)
                await asyncio.sleep(30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error for {channel_id}: {e}")
                await asyncio.sleep(30)

    async def _send_dms_to_participants(self, channel_id: str,
                                         participants: List[Dict],
                                         accounts: List[Dict]):
        """Send DMs to live participants using available accounts"""
        message_template = self.db.get_active_message('default')
        if not message_template:
            logger.warning("No active message template found")
            return

        text = message_template.get('text', 'Hello!')
        image_path = message_template.get('image_path')
        link_url = message_template.get('link_url')

        account_index = 0
        sent_count = 0

        for user in participants:
            if account_index >= len(accounts):
                account_index = 0

            account = accounts[account_index]
            phone = account['phone']

            # Check if already sent DM to this user from this account
            if self.db.has_received_dm(channel_id, user['user_id'], phone):
                continue

            # Format message
            formatted_text = text.format(
                first_name=user.get('first_name', 'User'),
                last_name=user.get('last_name', ''),
                username=user.get('username', ''),
                channel=channel_id
            )

            # Send DM
            result = await self.send_dm(
                phone, user['user_id'], formatted_text,
                image_path=image_path, link_url=link_url
            )

            if result.get('success'):
                self.db.log_dm_sent(
                    channel_id=channel_id,
                    user_id=user['user_id'],
                    username=user.get('username'),
                    first_name=user.get('first_name'),
                    account_phone=phone,
                    message_id=result.get('message_id')
                )
                self.db.mark_account_used(phone)
                self.db.increment_session_dms(channel_id, 1)
                sent_count += 1

            account_index += 1

        if sent_count > 0:
            logger.info(f"📨 Sent {sent_count} DMs in channel {channel_id}")

    async def start_all_monitors(self):
        """Start monitoring all active channels"""
        self.running = True
        channels = self.db.get_channels()
        for channel in channels:
            await self.start_monitoring_channel(channel['channel_id'])
        logger.info(f"🚀 Started monitoring {len(channels)} channels")

    async def stop_all_monitors(self):
        """Stop all monitoring"""
        self.running = False
        for channel_id in list(self.active_monitors.keys()):
            await self.stop_monitoring_channel(channel_id)
        # Disconnect all clients
        for client in self.clients.values():
            try:
                await client.disconnect()
            except:
                pass
        self.clients.clear()
        logger.info("🛑 All monitors stopped")

    def get_monitor_status(self, channel_id: str) -> bool:
        """Check if a channel is being monitored"""
        return channel_id in self.active_monitors
