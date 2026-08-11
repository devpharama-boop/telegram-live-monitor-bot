"""SQLite Database Management"""

import sqlite3
import json
import os
from pathlib import Path
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self):
        """Create all tables if not exist"""
        with self.get_conn() as conn:
            conn.executescript("""
                -- Admin users
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- User accounts (Telegram user accounts for DM sending)
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE NOT NULL,
                    session_string TEXT,
                    is_logged_in INTEGER DEFAULT 0,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP
                );

                -- Monitored channels
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL,
                    channel_title TEXT,
                    channel_username TEXT,
                    invite_link TEXT,
                    member_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_monitored TIMESTAMP
                );

                -- DM message templates
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT DEFAULT 'default',
                    text TEXT,
                    image_path TEXT,
                    image_url TEXT,
                    media_path TEXT,
                    link_url TEXT,
                    link_text TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- DM sent log (track who received DMs)
                CREATE TABLE IF NOT EXISTS dm_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    account_phone TEXT,
                    message_id INTEGER,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel_id, user_id, account_phone)
                );

                -- Live stream sessions
                CREATE TABLE IF NOT EXISTS live_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    total_viewers INTEGER DEFAULT 0,
                    dms_sent INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                );

                -- Bot settings
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                -- Monitoring state
                CREATE TABLE IF NOT EXISTS monitoring_state (
                    channel_id TEXT PRIMARY KEY,
                    is_monitoring INTEGER DEFAULT 0,
                    last_checked TIMESTAMP
                );
            """)

            # Insert default settings if not exist
            conn.execute("""
                INSERT OR IGNORE INTO settings (key, value) VALUES
                ('dm_cooldown_hours', '24'),
                ('auto_monitor_new_channels', '1'),
                ('dm_delay_seconds', '3')
            """)

            # Insert default message template if none exists
            exists = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()
            if exists['cnt'] == 0:
                conn.execute("""
                    INSERT INTO messages (name, text, is_active)
                    VALUES ('default', 'Hello {first_name}! 👋\n\nWe noticed you are live! Check out our channel for more updates.\n\nThank you! 🎉', 1)
                """)

    # ==================== Admin Methods ====================
    def is_admin(self, user_id: int) -> bool:
        with self.get_conn() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None

    def add_admin(self, user_id: int, added_by: int) -> bool:
        with self.get_conn() as conn:
            try:
                conn.execute("INSERT INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
                return True
            except sqlite3.IntegrityError:
                return False

    def remove_admin(self, user_id: int) -> bool:
        with self.get_conn() as conn:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            return conn.total_changes > 0

    def get_all_admins(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT * FROM admins ORDER BY added_at DESC").fetchall()
            return [dict(r) for r in rows]

    # ==================== Account Methods ====================
    def add_account(self, phone: str) -> Dict:
        with self.get_conn() as conn:
            try:
                conn.execute("INSERT INTO accounts (phone) VALUES (?)", (phone,))
                return {"success": True, "message": "Account added"}
            except sqlite3.IntegrityError:
                return {"success": False, "message": "Account already exists"}

    def update_account_session(self, phone: str, session_string: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE accounts SET session_string = ?, is_logged_in = 1 WHERE phone = ?",
                (session_string, phone)
            )

    def update_account_info(self, phone: str, first_name: str = None,
                            last_name: str = None, username: str = None):
        with self.get_conn() as conn:
            fields = []
            params = []
            if first_name:
                fields.append("first_name = ?")
                params.append(first_name)
            if last_name:
                fields.append("last_name = ?")
                params.append(last_name)
            if username:
                fields.append("username = ?")
                params.append(username)
            if fields:
                params.append(phone)
                conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE phone = ?", params)

    def get_accounts(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY added_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_logged_in_accounts(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE is_logged_in = 1 ORDER BY last_used ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_account_by_phone(self, phone: str) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE phone = ?", (phone,)).fetchone()
            return dict(row) if row else None

    def delete_account(self, phone: str) -> bool:
        with self.get_conn() as conn:
            conn.execute("DELETE FROM accounts WHERE phone = ?", (phone,))
            return conn.total_changes > 0

    def mark_account_used(self, phone: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE accounts SET last_used = CURRENT_TIMESTAMP WHERE phone = ?",
                (phone,)
            )

    # ==================== Channel Methods ====================
    def add_channel(self, channel_id: str, title: str = None,
                    username: str = None, invite_link: str = None) -> Dict:
        with self.get_conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO channels (channel_id, channel_title, channel_username, invite_link)
                       VALUES (?, ?, ?, ?)""",
                    (str(channel_id), title, username, invite_link)
                )
                return {"success": True, "message": "Channel added"}
            except sqlite3.IntegrityError:
                return {"success": False, "message": "Channel already exists"}

    def get_channels(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT * FROM channels WHERE is_active = 1 ORDER BY added_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_channel(self, channel_id: str) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE channel_id = ?", (str(channel_id),)
            ).fetchone()
            return dict(row) if row else None

    def update_channel_info(self, channel_id: str, title: str = None,
                            member_count: int = None):
        with self.get_conn() as conn:
            fields = []
            params = []
            if title:
                fields.append("channel_title = ?")
                params.append(title)
            if member_count is not None:
                fields.append("member_count = ?")
                params.append(member_count)
            if fields:
                fields.append("last_monitored = CURRENT_TIMESTAMP")
                params.append(str(channel_id))
                conn.execute(
                    f"UPDATE channels SET {', '.join(fields)} WHERE channel_id = ?",
                    params
                )

    def remove_channel(self, channel_id: str) -> bool:
        with self.get_conn() as conn:
            conn.execute("UPDATE channels SET is_active = 0 WHERE channel_id = ?", (str(channel_id),))
            return conn.total_changes > 0

    # ==================== Message Methods ====================
    def save_message(self, name: str = 'default', text: str = None,
                     image_path: str = None, image_url: str = None,
                     media_path: str = None, link_url: str = None,
                     link_text: str = None, is_active: int = 1) -> Dict:
        with self.get_conn() as conn:
            # Deactivate other messages with same name
            conn.execute("UPDATE messages SET is_active = 0 WHERE name = ?", (name,))
            conn.execute("""
                INSERT INTO messages (name, text, image_path, image_url, media_path, link_url, link_text, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, text, image_path, image_url, media_path, link_url, link_text, is_active))
            return {"success": True, "message": "Message saved"}

    def get_active_message(self, name: str = 'default') -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages WHERE name = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
                (name,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_messages(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE is_active = 1 ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def reset_message(self, name: str = 'default') -> bool:
        with self.get_conn() as conn:
            conn.execute("UPDATE messages SET is_active = 0 WHERE name = ?", (name,))
            return conn.total_changes > 0

    # ==================== DM Log Methods ====================
    def log_dm_sent(self, channel_id: str, user_id: int, username: str = None,
                    first_name: str = None, account_phone: str = None,
                    message_id: int = None) -> bool:
        with self.get_conn() as conn:
            try:
                conn.execute("""
                    INSERT INTO dm_log (channel_id, user_id, username, first_name, account_phone, message_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (str(channel_id), user_id, username, first_name, account_phone, message_id))
                return True
            except sqlite3.IntegrityError:
                return False  # Already sent

    def has_received_dm(self, channel_id: str, user_id: int, account_phone: str) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM dm_log WHERE channel_id = ? AND user_id = ? AND account_phone = ?",
                (str(channel_id), user_id, account_phone)
            ).fetchone()
            return row is not None

    def get_dm_logs(self, channel_id: str = None, limit: int = 100) -> List[Dict]:
        with self.get_conn() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM dm_log WHERE channel_id = ? ORDER BY sent_at DESC LIMIT ?",
                    (str(channel_id), limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dm_log ORDER BY sent_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_dm_count_for_channel(self, channel_id: str) -> int:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM dm_log WHERE channel_id = ?",
                (str(channel_id),)
            ).fetchone()
            return row['cnt'] if row else 0

    def reset_dm_log(self, channel_id: str = None):
        with self.get_conn() as conn:
            if channel_id:
                conn.execute("DELETE FROM dm_log WHERE channel_id = ?", (str(channel_id),))
            else:
                conn.execute("DELETE FROM dm_log")

    # ==================== Live Session Methods ====================
    def start_live_session(self, channel_id: str) -> int:
        with self.get_conn() as conn:
            # End any active session for this channel
            conn.execute(
                "UPDATE live_sessions SET is_active = 0, ended_at = CURRENT_TIMESTAMP WHERE channel_id = ? AND is_active = 1",
                (str(channel_id),)
            )
            cursor = conn.execute(
                "INSERT INTO live_sessions (channel_id) VALUES (?)",
                (str(channel_id),)
            )
            return cursor.lastrowid

    def end_live_session(self, channel_id: str):
        with self.get_conn() as conn:
            conn.execute(
                """UPDATE live_sessions SET is_active = 0, ended_at = CURRENT_TIMESTAMP
                   WHERE channel_id = ? AND is_active = 1""",
                (str(channel_id),)
            )

    def increment_session_dms(self, channel_id: str, count: int = 1):
        with self.get_conn() as conn:
            conn.execute(
                """UPDATE live_sessions SET dms_sent = dms_sent + ?
                   WHERE channel_id = ? AND is_active = 1""",
                (count, str(channel_id))
            )

    def get_active_live_sessions(self) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM live_sessions WHERE is_active = 1 ORDER BY started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ==================== Settings Methods ====================
    def get_setting(self, key: str, default: str = None) -> str:
        with self.get_conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )

    # ==================== Monitoring State ====================
    def set_monitoring_state(self, channel_id: str, is_monitoring: bool):
        with self.get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO monitoring_state (channel_id, is_monitoring, last_checked)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (str(channel_id), 1 if is_monitoring else 0))

    def get_monitoring_state(self, channel_id: str) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT is_monitoring FROM monitoring_state WHERE channel_id = ?",
                (str(channel_id),)
            ).fetchone()
            return bool(row['is_monitoring']) if row else False

    # ==================== Stats Methods ====================
    def get_stats(self) -> Dict:
        with self.get_conn() as conn:
            total_accounts = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()['c']
            logged_in_accounts = conn.execute(
                "SELECT COUNT(*) as c FROM accounts WHERE is_logged_in = 1"
            ).fetchone()['c']
            total_channels = conn.execute(
                "SELECT COUNT(*) as c FROM channels WHERE is_active = 1"
            ).fetchone()['c']
            total_dms = conn.execute("SELECT COUNT(*) as c FROM dm_log").fetchone()['c']
            active_lives = conn.execute(
                "SELECT COUNT(*) as c FROM live_sessions WHERE is_active = 1"
            ).fetchone()['c']
            today_dms = conn.execute(
                "SELECT COUNT(*) as c FROM dm_log WHERE date(sent_at) = date('now')"
            ).fetchone()['c']

            return {
                "total_accounts": total_accounts,
                "logged_in_accounts": logged_in_accounts,
                "total_channels": total_channels,
                "total_dms_sent": total_dms,
                "active_live_sessions": active_lives,
                "today_dms": today_dms
            }
