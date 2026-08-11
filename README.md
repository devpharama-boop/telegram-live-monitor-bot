# Telegram Live Stream Monitor Bot
# ==================================
# A powerful Telegram bot for monitoring live streams
# across multiple channels and auto-sending DMs

## Features

- 🔐 **Multi-Account Support** - Login multiple Telegram accounts via OTP
- 📢 **Channel Management** - Add/remove channels to monitor
- 🔴 **Live Stream Detection** - Auto-detect when channels go live
- 📨 **Auto DM Sending** - Send custom messages to live participants
- 🖼️ **Rich Messages** - Support text, images, and links in DMs
- 📊 **Dashboard** - Real-time stats and monitoring status
- 🔐 **Admin Panel** - Manage admins and view full statistics
- 🔄 **Account Rotation** - Rotates accounts to avoid rate limits
- 🚫 **Duplicate Prevention** - Each user gets DM only once per session

## Setup

### 1. Clone & Install
```bash
git clone <repo-url>
cd telegram-live-monitor-bot
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your credentials:
```env
BOT_TOKEN=your_bot_token_from_BotFather
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
ADMIN_ID=your_telegram_user_id
```

### 3. Get API Credentials
- **Bot Token**: From [@BotFather](https://t.me/BotFather)
- **API ID & Hash**: From [my.telegram.org](https://my.telegram.org/apps)

### 4. Run
```bash
python main.py
```

## Deployment

### Railway
1. Connect your GitHub repo to Railway
2. Set environment variables in Railway dashboard
3. Deploy!

## Usage

1. **/start** - Open the main menu
2. **Add Account** - Login your Telegram account (needed for DM sending)
3. **Add Channel** - Add channel ID or invite link
4. **Set Message** - Configure the DM template
5. **Live Monitor** - Monitor live streams and view stats

### DM Template Variables
- `{first_name}` - User's first name
- `{last_name}` - User's last name  
- `{username}` - User's username
- `{channel}` - Channel ID

## Project Structure
```
telegram-live-monitor-bot/
├── main.py              # Entry point
├── core/
│   ├── config.py        # Configuration management
│   ├── database.py      # SQLite database
│   ├── bot_app.py        # Telegram bot handlers
│   └── monitor.py       # Live stream monitor
├── data/                # Database & sessions (auto-generated)
├── logs/                # Log files (auto-generated)
├── requirements.txt     # Python dependencies
├── Procfile             # Railway deployment
└── .env                 # Environment variables
```

## Tech Stack
- **python-telegram-bot** - Bot API
- **Telethon** - Telegram client for monitoring
- **SQLite** - Database
- **Railway** - Hosting
push
