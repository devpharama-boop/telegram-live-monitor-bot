#!/usr/bin/env python3
"""
Supabase Database Helper for Telegram Live Monitor Bot
Replaces Firebase + local_db.json with Supabase REST API.
All session data persists across Railway redeploys.
"""

import os
import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# ==================== CONFIG ====================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ozzvgrcqkmrmwgpzpdfd.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im96enZncmNxa21ybXdncHpwZGZkIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MDU0MzE1MywiZXhwIjoyMDk2MTE5MTUzfQ.2otmaCif26gs8UxAJ5r5bHay_C_zV0jJByx6Psvgi6c")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im96enZncmNxa21ybXdncHpwZGZkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NDMxNTMsImV4cCI6MjA5NjExOTE1M30.eds_qJE7Ek6rmNRuDGlGTpJ3aXIHE2U_BthDgcbRI5g")

_available = False


def _req(method, path, body=None):
    """Make Supabase REST API request."""
    url = f"{SUPABASE_URL}/rest/v1{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return result
    except urllib.error.HTTPError as e:
        if e.code == 404 or e.code == 406:
            return None if method == 'GET' else []
        logger.warning(f"Supabase HTTP {e.code}: {path}")
        return None
    except Exception as e:
        logger.warning(f"Supabase error: {e}")
        return None


def init_supabase():
    """Check if Supabase is reachable."""
    global _available
    try:
        result = _req("GET", "/telegram_sessions?limit=1")
        _available = True
        logger.info("✅ Supabase connected!")
        return True
    except Exception as e:
        _available = False
        logger.warning(f"⚠️ Supabase not available: {e}")
        return False


# ==================== SESSION OPERATIONS ====================

def get_session(phone):
    """Get session_string for a phone number from Supabase."""
    try:
        # First try by phone
        encoded_phone = urllib.parse.quote(phone)
        result = _req("GET", f"/telegram_sessions?phone=eq.{encoded_phone}&active=eq.true&select=session_string,phone,session_id&limit=1")
        if result and len(result) > 0:
            row = result[0]
            if row.get("session_string"):
                return row["session_string"]
            # Fallback: try session_file_b64
            if row.get("session_file_b64"):
                return _from_session_file_b64(row["session_file_b64"])
        return None
    except Exception as e:
        logger.warning(f"get_session error: {e}")
        return None


def get_all_sessions():
    """Get all active session strings as {phone: session_string} dict."""
    try:
        result = _req("GET", "/telegram_sessions?active=eq.true&select=phone,session_string,session_file_b64")
        if not result:
            return {}
        sessions = {}
        for row in result:
            phone = row.get("phone", "")
            ss = row.get("session_string")
            if not ss and row.get("session_file_b64"):
                ss = _from_session_file_b64(row["session_file_b64"])
            if ss and phone:
                sessions[phone] = ss
        return sessions
    except Exception as e:
        logger.warning(f"get_all_sessions error: {e}")
        return {}


def save_session(phone, session_string, user_id=None, username=None, first_name=None):
    """Save or update session in Supabase."""
    try:
        meta = {"source": "telegram_live_monitor"}
        if user_id:
            meta["user_id"] = str(user_id)
        if username:
            meta["username"] = username
        if first_name:
            meta["first_name"] = first_name

        body = {
            "session_id": phone,
            "phone": phone,
            "session_string": session_string,
            "metadata": meta,
            "active": True
        }

        # UPSERT: try insert, if exists update
        encoded_phone = urllib.parse.quote(phone)
        existing = _req("GET", f"/telegram_sessions?phone=eq.{encoded_phone}&select=id&limit=1") or []
        if existing:
            row_id = existing[0]["id"]
            _req("PATCH", f"/telegram_sessions?id=eq.{row_id}", body)
            logger.info(f"🔄 Updated Supabase session for {phone}")
        else:
            _req("POST", "/telegram_sessions", body)
            logger.info(f"💾 Saved Supabase session for {phone}")
        return True
    except Exception as e:
        logger.error(f"save_session error: {e}")
        return False


def delete_session(phone):
    """Delete a session from Supabase."""
    try:
        encoded_phone = urllib.parse.quote(phone)
        _req("DELETE", f"/telegram_sessions?phone=eq.{encoded_phone}")
        logger.info(f"🗑️ Deleted session for {phone}")
        return True
    except Exception as e:
        logger.warning(f"delete_session error: {e}")
        return False


# ==================== ACCOUNT OPERATIONS ====================

def get_accounts():
    """Get all accounts from Supabase telegram_sessions."""
    try:
        result = _req("GET", "/telegram_sessions?active=eq.true&select=phone,session_string,metadata,created_at,session_id")
        if not result:
            return []
        accounts = []
        for row in result:
            meta = row.get("metadata", {})
            accounts.append({
                "phone": row.get("phone", ""),
                "user_id": meta.get("user_id", ""),
                "username": meta.get("username", ""),
                "first_name": meta.get("first_name", ""),
                "session_string": row.get("session_string", ""),
                "added_at": row.get("created_at", ""),
                "is_active": True
            })
        return accounts
    except Exception as e:
        logger.warning(f"get_accounts error: {e}")
        return []


def get_account(phone):
    """Get single account by phone."""
    try:
        encoded_phone = urllib.parse.quote(phone)
        result = _req("GET", f"/telegram_sessions?phone=eq.{encoded_phone}&limit=1")
        if result and len(result) > 0:
            row = result[0]
            meta = row.get("metadata", {})
            return {
                "phone": row.get("phone", ""),
                "user_id": meta.get("user_id", ""),
                "username": meta.get("username", ""),
                "first_name": meta.get("first_name", ""),
                "session_string": row.get("session_string", ""),
                "added_at": row.get("created_at", ""),
                "is_active": row.get("active", False)
            }
        return None
    except Exception as e:
        logger.warning(f"get_account error: {e}")
        return None


# ==================== CHANNEL OPERATIONS ====================

def get_channels():
    """Get monitored channels. Stored in a simple JSON key in metadata."""
    # We'll use a dedicated row in telegram_sessions with session_id='channels_config'
    try:
        result = _req("GET", "/telegram_sessions?session_id=eq.channels_config&select=metadata&limit=1")
        if result and len(result) > 0:
            meta = result[0].get("metadata", {})
            return meta.get("channels", [])
        return []
    except Exception:
        return []


def save_channels(channels):
    """Save monitored channels list."""
    try:
        existing = _req("GET", "/telegram_sessions?session_id=eq.channels_config&select=id&limit=1") or []
        body = {
            "session_id": "channels_config",
            "phone": "config",
            "metadata": {"channels": channels},
            "active": True
        }
        if existing:
            row_id = existing[0]["id"]
            _req("PATCH", f"/telegram_sessions?id=eq.{row_id}", body)
        else:
            _req("POST", "/telegram_sessions", body)
        return True
    except Exception as e:
        logger.warning(f"save_channels error: {e}")
        return False


# ==================== DM CONFIG ====================

def get_dm_config():
    """Get DM message config."""
    try:
        result = _req("GET", "/telegram_sessions?session_id=eq.dm_config&select=metadata&limit=1")
        if result and len(result) > 0:
            return result[0].get("metadata", {})
        return {"message": "", "image_url": "", "enabled": False}
    except Exception:
        return {"message": "", "image_url": "", "enabled": False}


def save_dm_config(config):
    """Save DM message config."""
    try:
        existing = _req("GET", "/telegram_sessions?session_id=eq.dm_config&select=id&limit=1") or []
        body = {
            "session_id": "dm_config",
            "phone": "config",
            "metadata": config,
            "active": True
        }
        if existing:
            row_id = existing[0]["id"]
            _req("PATCH", f"/telegram_sessions?id=eq.{row_id}", body)
        else:
            _req("POST", "/telegram_sessions", body)
        return True
    except Exception as e:
        logger.warning(f"save_dm_config error: {e}")
        return False


# ==================== PENDING LOGIN ====================

_pending_logins: dict = {}

def save_pending_login(phone, data):
    """Save pending login OTP hash to Supabase."""
    try:
        existing = _req("GET", f"/telegram_sessions?session_id=eq.pending_{urllib.parse.quote(phone)}&select=id&limit=1") or []
        body = {
            "session_id": f"pending_{phone}",
            "phone": phone,
            "metadata": data,
            "active": True
        }
        if existing:
            _req("PATCH", f"/telegram_sessions?id=eq.{existing[0]['id']}", body)
        else:
            _req("POST", "/telegram_sessions", body)
        _pending_logins[phone] = data
        return True
    except Exception as e:
        logger.warning(f"save_pending_login error: {e}")
        _pending_logins[phone] = data
        return False


def get_pending_login(phone):
    """Get pending login data from Supabase."""
    if phone in _pending_logins:
        return _pending_logins[phone]
    try:
        result = _req("GET", f"/telegram_sessions?session_id=eq.pending_{urllib.parse.quote(phone)}&select=metadata&limit=1")
        if result and len(result) > 0:
            return result[0].get("metadata", {})
        return None
    except Exception:
        return None


def delete_pending_login(phone):
    """Delete pending login data."""
    _pending_logins.pop(phone, None)
    try:
        _req("DELETE", f"/telegram_sessions?session_id=eq.pending_{urllib.parse.quote(phone)}")
    except:
        pass


# ==================== ADMINS ====================

def get_admins():
    """Get admin user IDs."""
    try:
        result = _req("GET", "/telegram_sessions?session_id=eq.admins_config&select=metadata&limit=1")
        if result and len(result) > 0:
            meta = result[0].get("metadata", {})
            return meta.get("admins", [5844447576])
        return [5844447576]
    except Exception:
        return [5844447576]


def save_admins(admin_ids):
    """Save admin user IDs."""
    try:
        existing = _req("GET", "/telegram_sessions?session_id=eq.admins_config&select=id&limit=1") or []
        body = {
            "session_id": "admins_config",
            "phone": "config",
            "metadata": {"admins": admin_ids},
            "active": True
        }
        if existing:
            _req("PATCH", f"/telegram_sessions?id=eq.{existing[0]['id']}", body)
        else:
            _req("POST", "/telegram_sessions", body)
        return True
    except Exception as e:
        logger.warning(f"save_admins error: {e}")
        return False


# ==================== HELPERS ====================

def get_stats():
    """Get combined stats."""
    accounts = get_accounts()
    channels = get_channels()
    dm_config = get_dm_config()
    return {
        "total_accounts": len(accounts),
        "total_channels": len(channels),
        "monitoring_count": 0,
        "dm_sent": 0,
        "dm_configured": bool(dm_config.get("message", "")),
        "accounts": accounts,
        "channels": channels
    }


def _from_session_file_b64(b64_str):
    """Convert base64 .session file back to StringSession string."""
    import base64
    try:
        session_bytes = base64.b64decode(b64_str)
        from telethon.sessions import StringSession
        # Telethon uses SQLite session format; need to extract auth key
        # For now, return None — StringSession save is preferred
        return None
    except:
        return None
