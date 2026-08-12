#!/usr/bin/env python3
"""
Web Dashboard for Telegram Live Stream Monitor Bot
Flask-based web UI for managing the bot.
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from functools import wraps
from dotenv import load_dotenv
from supabase_db import (
    init_supabase, get_session, get_all_sessions, save_session, delete_session,
    get_accounts, get_account, save_pending_login, get_pending_login, delete_pending_login,
    get_channels, save_channels, get_dm_config, save_dm_config, get_admins, save_admins, get_stats
)

load_dotenv()

# Add parent dir to path for bot imports
sys.path.insert(0, str(Path(__file__).parent))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "telegram-live-monitor-secret-key-2024")

# ==================== PASSWORD PROTECTION ====================
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "tinesh")

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == DASHBOARD_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error='❌ Wrong password!')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

def require_auth(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({"success": False, "error": "Authentication required", "auth_required": True}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ==================== CONFIG ====================
ADMIN_IDS = [5844447576]

# ==================== SUPABASE ====================
# Supabase replaces Firebase — all data persists across Railway redeploys
# The supabase_db.py module handles all the REST API calls

print("🔌 Connecting to Supabase...")
_supa_ok = init_supabase()


def local_delete(path):
    d = _load_local()
    parts = path.strip('/').split('/')
    current = d
    for part in parts[:-1]:
        if part not in current:
            return
        current = current[part]
    if parts[-1] in current:
        del current[parts[-1]]
    _save_local(d)


# ==================== AUTH ====================
def is_admin(user_id):
    admins = get_admins() or []
    return int(user_id) in admins or int(user_id) in ADMIN_IDS


# ==================== MAIN DASHBOARD ====================
@app.route('/')
@require_auth
def index():
    """Main dashboard page."""
    return render_template('dashboard.html')


# ==================== API ROUTES ====================
@app.route('/api/stats')
@require_auth
def api_stats():
    """Get bot statistics."""
    stats = get_stats()
    accounts = stats.get("accounts", [])
    channels = stats.get("channels", [])

    # Count DMs sent
    total_dm_sent = 0
    currently_live = 0

    channel_list = []
    for ch in channels:
        channel_list.append({
            "id": ch.get("channel_id", ch.get("id", "")),
            "title": ch.get('title', 'Unknown'),
            "username": ch.get('username', ''),
            "is_live": ch.get('is_currently_live', False),
            "total_dm_sent": ch.get('total_dm_sent', 0),
            "session_dm_sent": ch.get('session_dm_sent', 0),
            "joined_at": ch.get('added_at', ''),
            "current_viewers": ch.get('current_viewers', 0),
        })
        if ch.get('is_currently_live'):
            currently_live += 1
        total_dm_sent += ch.get('total_dm_sent', 0)

    dm_config = get_dm_config() or {}

    return jsonify({
        "success": True,
        "data": {
            "total_channels": len(channels),
            "total_accounts": len(accounts),
            "active_lives": currently_live,
            "total_dm_sent": total_dm_sent,
            "dm_configured": bool(dm_config.get('message', '').strip()),
            "dm_message": dm_config.get('message', ''),
            "has_media": bool(dm_config.get('media')),
            "channels": channel_list,
            "accounts": accounts,
            "admins": get_admins()
        }
    })


@app.route('/api/channels', methods=['GET', 'POST'])
@require_auth
def api_channels():
    """List channels or add a new one."""
    if request.method == 'GET':
        channels = get_channels() or []
        return jsonify({"success": True, "channels": list(channels.values())})

    # POST - Add channel (full flow: check + join all accounts + save)
    data = request.get_json()
    channel_input = data.get('channel_input', '').strip()
    invite_link = data.get('invite_link', '').strip()
    is_invite = data.get('is_invite', False)

    identifier = invite_link if is_invite and invite_link else channel_input
    if not identifier:
        return jsonify({"success": False, "error": "Channel username, ID, or invite link required"})

    # Detect if it's a numeric channel ID (e.g., -1004368116984)
    is_channel_id = identifier.lstrip('-').isdigit()

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _add_channel():
        # Load all accounts
        accounts = get_accounts() or []
        if not accounts:
            return {"success": False, "error": "No accounts connected. Add an account first."}

        client_pool = {}
        for uid_str, acc in accounts.items():
            try:
                session_name = acc.get('session_name', f'account_{uid_str}')
                client = TelegramClient(session_name, API_ID, API_HASH)
                await client.start()
                client_pool[int(uid_str)] = client
            except Exception as e:
                pass

        if not client_pool:
            return {"success": False, "error": "Could not load any accounts"}

        # Step 1: Check if already joined
        entity = None
        joined_already = False
        try:
            if is_channel_id:
                from telethon.tl.types import PeerChannel
                entity = await list(client_pool.values())[0].get_entity(PeerChannel(int(identifier)))
                joined_already = True
            else:
                entity = await list(client_pool.values())[0].get_entity(identifier)
                joined_already = True
        except Exception:
            joined_already = False

        # Step 2: Join with ALL accounts
        join_results = {}
        success_count = 0
        for uid, client in client_pool.items():
            try:
                if is_invite:
                    hash_part = identifier.split('/')[-1].replace('+', '')
                    try:
                        update = await client(ImportChatInviteRequest(hash=hash_part))
                        entity = update.chats[0] if update.chats else entity
                    except errors.InviteHashExpiredError:
                        join_results[str(uid)] = {"success": False, "error": "Invite expired"}
                        continue
                    except errors.InviteHashInvalidError:
                        join_results[str(uid)] = {"success": False, "error": "Invalid invite"}
                        continue
                elif is_channel_id:
                    from telethon.tl.types import PeerChannel
                    entity = await client.get_entity(PeerChannel(int(identifier)))
                else:
                    entity = await client.get_entity(identifier)

                try:
                    await client(JoinChannelRequest(entity))
                    join_results[str(uid)] = {"success": True, "message": "Joined"}
                    success_count += 1
                except Exception as e:
                    if "already" in str(e).lower():
                        join_results[str(uid)] = {"success": True, "message": "Already joined"}
                        success_count += 1
                    else:
                        join_results[str(uid)] = {"success": False, "error": str(e)}

            except Exception as e:
                join_results[str(uid)] = {"success": False, "error": str(e)}

            await asyncio.sleep(1.5)

        # Disconnect all
        for client in client_pool.values():
            await client.disconnect()

        # Step 3: Save channel info
        ch_id = str(getattr(entity, 'id', '')) if entity else identifier
        title = getattr(entity, 'title', identifier) if entity else identifier
        username = getattr(entity, 'username', '') if entity else ''

        channel_info = {
            "id": ch_id,
            "title": title,
            "username": username,
            "identifier": identifier,
            "is_invite": is_invite,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "is_active": True,
            "total_dm_sent": 0,
            "session_dm_sent": 0,
            "is_currently_live": False,
            "current_viewers": 0,
            "total_accounts_at_join": len(accounts),
            "last_live_at": None
        }
        save_channels(channels)  # will append

        return {
            "success": True,
            "was_already_joined": joined_already,
            "accounts_joined": success_count,
            "total_accounts": len(accounts),
            "channel": channel_info,
            "join_details": join_results
        }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_add_channel())
    loop.close()
    return jsonify(result)


@app.route('/api/channels/<channel_id>', methods=['DELETE'])
@require_auth
def api_delete_channel(channel_id):
    """Remove a channel."""
    all_ch = get_channels() or []
    all_ch = [c for c in all_ch if str(c.get('id', '')) != str(channel_id) and str(c.get('channel_id', '')) != str(channel_id)]
    save_channels(all_ch)
    return jsonify({"success": True, "message": "Channel removed"})


@app.route('/api/dm/config', methods=['GET', 'POST'])
@require_auth
def api_dm_config():
    """Get or set DM configuration."""
    if request.method == 'GET':
        config = get_dm_config() or {}
        return jsonify({"success": True, "config": config})

    # POST - Set DM message (full save, not merge)
    data = request.get_json()
    message = data.get('message', '')
    image_url = data.get('image_url', '')
    media_file = data.get('media_file', '')

    # Get existing config to preserve other fields
    existing = get_dm_config() or {}

    # Build new config
    new_config = dict(existing)  # preserve existing fields
    if message:
        new_config['message'] = message
    if image_url or media_file:
        new_config['media'] = {
            'type': 'image',
            'url': image_url or media_file,
            'file_path': media_file
        }

    # Force full save (not merge) to ensure all fields persist
    save_dm_config(new_config)
    return jsonify({"success": True, "message": "DM config updated", "config": new_config})


@app.route('/api/dm/reset', methods=['POST'])
@require_auth
def api_reset_dm():
    """Reset DM records."""
    data = request.get_json() or {}
    channel_id = data.get('channel_id')

    if channel_id:
        all_ch = get_channels() or []
        for c in all_ch:
            if str(c.get('id', '')) == str(channel_id) or str(c.get('channel_id', '')) == str(channel_id):
                c['total_dm_sent'] = 0
        save_channels(all_ch)
    else:
        pass  # reset all — not needed with Supabase

    return jsonify({"success": True, "message": "DM records reset"})


@app.route('/api/dm/reset-config', methods=['POST'])
@require_auth
def api_reset_dm_config():
    """Reset DM message config to default."""
    save_dm_config({'message': "👋 Hi! I noticed you're watching this live stream. Check out our community!"})
    return jsonify({"success": True, "message": "DM config reset to default"})


@app.route('/api/accounts', methods=['GET'])
@require_auth
def api_accounts():
    """Get connected accounts."""
    accounts = get_accounts() or []
    return jsonify({"success": True, "accounts": accounts})


# ==================== ACCOUNT LOGIN (Built-in, no bot.py import) ====================
# We handle login directly here to avoid cross-module database conflicts
import asyncio
from telethon import TelegramClient, errors as telethon_errors

# In-memory pending logins (avoids file I/O race condition)
_pending_logins: dict = {}


@app.route('/api/accounts/login', methods=['POST'])
@require_auth
def api_accounts_login():
    """Login a new account with phone number."""
    data = request.get_json()
    phone = data.get('phone', '').strip()

    if not phone:
        return jsonify({"success": False, "error": "Phone number required"})

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _login():
        from telethon.sessions import StringSession
        from telethon import errors as telethon_errors
        # CRITICAL: Create session file to persist auth key between send_code & sign_in
        # phone_code_hash is tied to the auth key — must use SAME session!
        session_file = f"login_{phone.replace('+', '')}"
        client = TelegramClient(session_file, API_ID, API_HASH)
        try:
            await client.connect()
            sent_code = await client.send_code_request(phone, force_sms=True)
            # Store pending login with session file name (NOT StringSession)
            _pending_logins[phone] = {
                "phone_code_hash": sent_code.phone_code_hash,
                "session_file": session_file,
                "attempted_at": datetime.now(timezone.utc).isoformat()
            }
            save_pending_login(phone, _pending_logins[phone])
            await client.disconnect()
            return {"success": True, "message": "OTP sent successfully", "phone_code_hash": sent_code.phone_code_hash}
        except telethon_errors.FloodWaitError as e:
            await client.disconnect()
            return {"success": False, "error": f"⚠️ Too many attempts! Wait {e.seconds // 60} minutes ({e.seconds}s). Please use a DIFFERENT phone number.", "flood_wait": e.seconds}
        except Exception as e:
            await client.disconnect()
            return {"success": False, "error": str(e)}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_login())
    loop.close()
    return jsonify(result)


@app.route('/api/accounts/verify', methods=['POST'])
@require_auth
def api_accounts_verify():
    """Verify OTP for account login."""
    data = request.get_json()
    phone = data.get('phone', '').strip()
    otp = data.get('otp', '').strip()
    password = data.get('password', '').strip()

    if not phone or not otp:
        return jsonify({"success": False, "error": "Phone and OTP required"})

    # Get pending login data — check in-memory first, then DB
    pending = _pending_logins.get(phone)
    if not pending:
        pending = get_pending_login(phone)
    if not pending:
        return jsonify({
            "success": False,
            "error": "No pending login for this number. Please send OTP again first."
        })

    API_ID = int(os.getenv("TELEGRAM_API_ID", "35812449"))
    API_HASH = os.getenv("TELEGRAM_API_HASH", "099cfed535a5b2dcd8e43f157d30e3ce")

    async def _verify():
        from telethon.sessions import StringSession
        phone_code_hash = pending['phone_code_hash']
        # REUSE the same session file from send_code_request!
        # phone_code_hash is tied to the auth key — must match.
        session_file = pending.get('session_file', f"login_{phone.replace('+', '')}")
        client = TelegramClient(session_file, API_ID, API_HASH)
        try:
            await client.connect()
            authed = False
            try:
                await client.sign_in(
                    phone=phone,
                    code=otp,
                    phone_code_hash=phone_code_hash
                )
                authed = True
            except telethon_errors.SessionPasswordNeededError:
                if not password:
                    await client.disconnect()
                    return {"success": False, "error": "2FA password required", "need_password": True}
                await client.sign_in(password=password)
                authed = True

            if authed:
                me = await client.get_me()
                # Save session as StringSession for future use
                session_str = StringSession.save(client.session)
                # Clean up the temporary login session file
                import glob as _glob
                for _sf in _glob.glob(f"login_{phone.replace('+', '')}.session"):
                    try: os.remove(_sf)
                    except: pass
                SESSION_FILE = "session_string.txt"
                with open(SESSION_FILE, 'w') as f:
                    f.write(session_str)
                # Also save to local_db.json for persistence
                try:
                    with open('local_db.json', 'r') as f:
                        local = json.load(f)
                except:
                    local = {}
                local['main_session_string'] = session_str
                with open('local_db.json', 'w') as f:
                    json.dump(local, f)
                os.environ["MAIN_SESSION_STRING"] = session_str
                print(f"🔑 Session saved to {SESSION_FILE} + local_db.json for {me.first_name} (ID={me.id})")
                
                account_info = {
                    "phone": phone,
                    "user_id": me.id,
                    "first_name": me.first_name or "Unknown",
                    "username": me.username or "",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "session_string": session_str,
                    "is_active": True
                }
                # Save to Supabase
                save_session(phone, session_str, user_id=me.id, username=me.username or "", first_name=me.first_name or "Unknown")
                # Clear pending
                _pending_logins.pop(phone, None)
                delete_pending_login(phone)
                await client.disconnect()
                return {"success": True, "account": account_info}
            else:
                await client.disconnect()
                return {"success": False, "error": "Login failed"}

        except telethon_errors.PhoneCodeInvalidError:
            await client.disconnect()
            return {"success": False, "error": "Invalid OTP code. Please try again."}
        except telethon_errors.PhoneCodeExpiredError:
            await client.disconnect()
            _pending_logins.pop(phone, None)
            delete_pending_login(phone)
            return {"success": False, "error": "OTP expired. Please send a new OTP."}
        except telethon_errors.PasswordHashInvalidError:
            await client.disconnect()
            return {"success": False, "error": "Incorrect 2FA password."}
        except telethon_errors.FloodWaitError as e:
            await client.disconnect()
            return {"success": False, "error": f"Too many attempts. Wait {e.seconds} seconds."}
        except Exception as e:
            await client.disconnect()
            return {"success": False, "error": f"Verification failed: {str(e)}"}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(_verify())
    loop.close()
    return jsonify(result)


@app.route('/api/admins', methods=['GET', 'POST'])
@require_auth
def api_admins():
    """Get admins or add a new admin."""
    if request.method == 'GET':
        admins = get_admins() or []
        return jsonify({"success": True, "admins": admins + ADMIN_IDS})

    # POST - Add admin
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "error": "User ID required"})

    admins = get_admins() or []
    if int(user_id) not in admins and int(user_id) not in ADMIN_IDS:
        admins.append(int(user_id))
        save_admins(admins)
        return jsonify({"success": True, "message": f"Admin {user_id} added"})

    return jsonify({"success": False, "error": "Already an admin"})


@app.route('/api/admins/<int:user_id>', methods=['DELETE'])
@require_auth
def api_remove_admin(user_id):
    """Remove an admin."""
    if user_id in ADMIN_IDS:
        return jsonify({"success": False, "error": "Cannot remove primary admin"})

    admins = get_admins() or []
    if user_id in admins:
        admins.remove(user_id)
        save_admins(admins)
        return jsonify({"success": True, "message": f"Admin {user_id} removed"})

    return jsonify({"success": False, "error": "Not an admin"})


@app.route('/api/user/check/<int:user_id>')
@require_auth
def api_check_user(user_id):
    """Check if a user is admin."""
    return jsonify({
        "success": True,
        "is_admin": is_admin(user_id),
        "user_id": user_id
    })


# ==================== RUN ====================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
