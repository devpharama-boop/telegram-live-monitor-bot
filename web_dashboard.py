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
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, db

load_dotenv()

# Add parent dir to path for bot imports
sys.path.insert(0, str(Path(__file__).parent))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "telegram-live-monitor-secret-key-2024")

# ==================== CONFIG ====================
ADMIN_IDS = [5844447576]
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "firebase-cred.json")

# ==================== FIREBASE ====================
firebase_ref = None

try:
    if FIREBASE_DB_URL and os.path.exists(FIREBASE_CRED_PATH):
        cred = credentials.Certificate(FIREBASE_CRED_PATH)
        firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
        firebase_ref = db.reference('/')
        print("Firebase connected for web dashboard")
except Exception as e:
    print(f"Firebase init warning: {e}")

# Local JSON fallback
LOCAL_DB_PATH = Path("local_db.json")


def fb_get(path, default=None):
    if firebase_ref:
        return firebase_ref.child(path).get() or default
    return local_get(path, default)


def fb_set(path, value):
    if firebase_ref:
        firebase_ref.child(path).set(value)
    else:
        local_set(path, value)


def fb_update(path, value):
    if firebase_ref:
        firebase_ref.child(path).update(value)
    else:
        current = local_get(path, {}) or {}
        current.update(value)
        local_set(path, current)


def fb_delete(path):
    if firebase_ref:
        firebase_ref.child(path).delete()
    else:
        local_delete(path)


# Local JSON helpers
def _load_local():
    if LOCAL_DB_PATH.exists():
        with open(LOCAL_DB_PATH, 'r') as f:
            return json.load(f)
    return {}


def _save_local(data):
    with open(LOCAL_DB_PATH, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def local_get(path, default=None):
    d = _load_local()
    parts = path.strip('/').split('/')
    for part in parts:
        if isinstance(d, dict) and part in d:
            d = d[part]
        else:
            return default
    return d


def local_set(path, value):
    d = _load_local()
    parts = path.strip('/').split('/')
    current = d
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    _save_local(d)


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
    admins = fb_get('admins', []) or []
    return int(user_id) in admins or int(user_id) in ADMIN_IDS


# ==================== MAIN DASHBOARD ====================
@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('dashboard.html')


# ==================== API ROUTES ====================
@app.route('/api/stats')
def api_stats():
    """Get bot statistics."""
    channels = fb_get('channels', {}) or {}
    accounts = fb_get('accounts', {}) or {}
    dm_config = fb_get('dm_config', {})
    active_lives = fb_get('active_lives', {})

    # Count DMs sent
    total_dm_sent = sum(
        ch.get('total_dm_sent', 0) for ch in channels.values()
    )
    currently_live = sum(
        1 for ch in channels.values() if ch.get('is_currently_live')
    )

    channel_list = []
    for ch_id, ch in channels.items():
        dm_sent_for_ch = fb_get(f'dm_sent/{ch_id}', {}) or {}
        channel_list.append({
            "id": ch_id,
            "title": ch.get('title', 'Unknown'),
            "username": ch.get('username', ''),
            "is_live": ch.get('is_currently_live', False),
            "total_dm_sent": ch.get('total_dm_sent', 0),
            "unique_dmed": len(dm_sent_for_ch),
            "joined_at": ch.get('joined_at', ''),
            "current_viewers": ch.get('current_viewers', 0)
        })

    return jsonify({
        "success": True,
        "data": {
            "total_channels": len(channels),
            "total_accounts": len(accounts),
            "active_lives": currently_live,
            "total_dm_sent": total_dm_sent,
            "dm_message": dm_config.get('message', ''),
            "has_media": bool(dm_config.get('media')),
            "channels": channel_list,
            "accounts": list(accounts.values()),
            "admins": fb_get('admins', [])
        }
    })


@app.route('/api/channels', methods=['GET', 'POST'])
def api_channels():
    """List channels or add a new one."""
    if request.method == 'GET':
        channels = fb_get('channels', {}) or {}
        return jsonify({"success": True, "channels": list(channels.values())})

    # POST - Add channel
    data = request.get_json()
    channel_id = data.get('channel_id', '').strip()

    if not channel_id:
        return jsonify({"success": False, "error": "Channel ID/username required"})

    # Import bot functions
    from bot import join_channel
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(join_channel(channel_id))
    loop.close()

    if result.get('success'):
        return jsonify({
            "success": True,
            "message": f"Added: {result['channel']['title']}",
            "channel": result['channel']
        })
    return jsonify({"success": False, "error": result.get('error', 'Unknown error')})


@app.route('/api/channels/<channel_id>', methods=['DELETE'])
def api_delete_channel(channel_id):
    """Remove a channel."""
    fb_delete(f'channels/{channel_id}')
    fb_delete(f'dm_sent/{channel_id}')
    return jsonify({"success": True, "message": "Channel removed"})


@app.route('/api/dm/config', methods=['GET', 'POST'])
def api_dm_config():
    """Get or set DM configuration."""
    if request.method == 'GET':
        config = fb_get('dm_config', {})
        return jsonify({"success": True, "config": config})

    # POST - Set DM message
    data = request.get_json()
    message = data.get('message', '')
    image_url = data.get('image_url', '')
    media_file = data.get('media_file', '')

    config = {}
    if message:
        config['message'] = message
    if image_url or media_file:
        config['media'] = {
            'type': 'image',
            'url': image_url or media_file,
            'file_path': media_file
        }

    if config:
        fb_update('dm_config', config)
        return jsonify({"success": True, "message": "DM config updated"})

    return jsonify({"success": False, "error": "No changes provided"})


@app.route('/api/dm/reset', methods=['POST'])
def api_reset_dm():
    """Reset DM records."""
    data = request.get_json() or {}
    channel_id = data.get('channel_id')

    if channel_id:
        fb_delete(f'dm_sent/{channel_id}')
        fb_set(f'channels/{channel_id}/total_dm_sent', 0)
    else:
        fb_delete('dm_sent')

    return jsonify({"success": True, "message": "DM records reset"})


@app.route('/api/dm/reset-config', methods=['POST'])
def api_reset_dm_config():
    """Reset DM message config to default."""
    fb_set('dm_config', {
        'message': "👋 Hi! I noticed you're watching this live stream. Check out our community!"
    })
    return jsonify({"success": True, "message": "DM config reset to default"})


@app.route('/api/accounts', methods=['GET'])
def api_accounts():
    """Get connected accounts."""
    accounts = fb_get('accounts', {}) or {}
    return jsonify({"success": True, "accounts": list(accounts.values())})


@app.route('/api/accounts/login', methods=['POST'])
def api_accounts_login():
    """Login a new account with phone number."""
    data = request.get_json()
    phone = data.get('phone', '').strip()

    if not phone:
        return jsonify({"success": False, "error": "Phone number required"})

    from bot import login_account
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(login_account(phone))
    loop.close()

    return jsonify(result)


@app.route('/api/accounts/verify', methods=['POST'])
def api_accounts_verify():
    """Verify OTP for account login."""
    data = request.get_json()
    phone = data.get('phone', '')
    otp = data.get('otp', '')
    password = data.get('password', '')

    if not phone or not otp:
        return jsonify({"success": False, "error": "Phone and OTP required"})

    from bot import verify_account
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(verify_account(phone, otp, password))
    loop.close()

    return jsonify(result)


@app.route('/api/admins', methods=['GET', 'POST'])
def api_admins():
    """Get admins or add a new admin."""
    if request.method == 'GET':
        admins = fb_get('admins', []) or []
        return jsonify({"success": True, "admins": admins + ADMIN_IDS})

    # POST - Add admin
    data = request.get_json()
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({"success": False, "error": "User ID required"})

    admins = fb_get('admins', []) or []
    if int(user_id) not in admins and int(user_id) not in ADMIN_IDS:
        admins.append(int(user_id))
        fb_set('admins', admins)
        return jsonify({"success": True, "message": f"Admin {user_id} added"})

    return jsonify({"success": False, "error": "Already an admin"})


@app.route('/api/admins/<int:user_id>', methods=['DELETE'])
def api_remove_admin(user_id):
    """Remove an admin."""
    if user_id in ADMIN_IDS:
        return jsonify({"success": False, "error": "Cannot remove primary admin"})

    admins = fb_get('admins', []) or []
    if user_id in admins:
        admins.remove(user_id)
        fb_set('admins', admins)
        return jsonify({"success": True, "message": f"Admin {user_id} removed"})

    return jsonify({"success": False, "error": "Not an admin"})


@app.route('/api/user/check/<int:user_id>')
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
