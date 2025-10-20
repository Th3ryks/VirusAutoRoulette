# 🎰 Virus Roulette Bot

## 📋 Overview

This bot automates interactions with Telegram virus roulette bot, managing multiple accounts simultaneously. It provides automated prize claiming, channel subscription management, and balance monitoring with notifications.

## ✨ Features

### 🎯 Core Functionality
- **Multi-Account Management**: Support for unlimited Telegram accounts
- **Prize Auto-Claiming**: Automatically claims all types of prizes
- **Channel Management**: Auto-subscribe to required channels and unsubscribe after claiming
- **Balance Monitoring**: Real-time balance tracking with goal notifications
- **Smart Notifications**: Telegram notifications for important events

### 🔔 Notification System
- Prize claiming status updates
- Account status monitoring
- Error and success notifications

### 🛡️ Safety Features
- Automatic retry mechanisms
- Error handling and logging
- Rate limiting and delays
- Session management

## 🚀 Installation

### Prerequisites
- Python 3.11.0 or higher
- Telegram API credentials
- Bot token from @BotFather

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/th3ryks/VirusAutoRoulette.git
   cd VirusAutoRoulette
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` file with your credentials:
   ```env
   BOT_TOKEN=your_bot_token_here
   ADMIN_ID=your_admin_id_here
   
   ACCOUNT1_API_ID=your_api_id_1
   ACCOUNT1_API_HASH=your_api_hash_1
   ACCOUNT1_PHONE_NUMBER=your_phone_number_1
   
   ACCOUNT2_API_ID=your_api_id_2
   ACCOUNT2_API_HASH=your_api_hash_2
   ACCOUNT2_PHONE_NUMBER=your_phone_number_2
   ```

4. **Run the bot**
   ```bash
   python3 main.py
   ```

## ⚙️ Configuration

### 📱 Adding Accounts

The bot supports unlimited accounts. To add more accounts:

1. **Add new environment variables** to your `.env` file:
   ```env
   ACCOUNT3_API_ID=your_api_id_3
   ACCOUNT3_API_HASH=your_api_hash_3
   ACCOUNT3_PHONE_NUMBER=your_phone_number_3
   ```

2. **Add corresponding configuration** in `main.py` (lines 48-53):
   ```python
   "account3": {
       "api_id": os.getenv("ACCOUNT3_API_ID"),
       "api_hash": os.getenv("ACCOUNT3_API_HASH"),
       "phone_number": os.getenv("ACCOUNT3_PHONE_NUMBER"),
       "session_name": "account3"
   },
   ```

The bot will automatically:
- Create `account3.session` file
- Include the account in monitoring
- Start automated processes

## 🎮 Usage

### 📊 Telegram Commands

- `/start` - Initialize bot and show main menu

### 🔄 Automated Operations

The bot automatically:
1. **Monitors account balances**
2. **Spins roulettes**
3. **Claims prizes**
4. **Unsubscribes from channels that had to be subscribed to in order to spin the roulette.**
5. **Sends a message if the roulette has been spun successfully.**

### 📈 Balance Notifications

- **Balance Updates**: Regular balance monitoring
- **Prize Notifications**: Alerts for claimed prizes

## 🏗️ Architecture

### 📁 Project Structure
```
├── main.py              # Main application file
├── requirements.txt     # Python dependencies
├── .env                 # Environment variables
├── .env.example         # Environment template
├── .gitignore           # Git ignore rules
├── LICENSE              # License file
└── README.md            # This file

```

### 🔧 Core Components

- **AccountManager**: Handles multiple Telegram accounts
- **AccountData**: Stores account information and settings
- **Notification System**: Manages Telegram notifications
- **Roulette Handler**: Automates game participation
- **Balance Monitor**: Tracks account balances

## 🛠️ Development

### 📋 Requirements

- **Python**: 3.11.0+
- **asyncio**: Asynchronous programming
- **aiogram**: Telegram Bot API
- **kurigram**: Telegram MTproto API
- **loguru**: Logging system

### 🧪 Code Quality

The project uses:
- **Ruff**: Code linting and formatting
- **PEP8**: Python style guide
- **Type hints**: For better code documentation
- **Async/await**: Full asynchronous implementation

### 🔍 Logging

Comprehensive logging with:
- **Colored output**: Easy-to-read console logs
- **Structured format**: Timestamp, level, and message
- **Multiple levels**: DEBUG, INFO, SUCCESS, WARNING, ERROR

## 🚨 Important Notes

### ⚠️ Security
- Never share your `.env` file
- Keep API credentials secure
- Use strong passwords for accounts
- Monitor bot activity regularly

### 📱 Telegram Limits
- Respect Telegram's rate limits
- Avoid excessive API calls
- Monitor account restrictions
- Use delays between operations

### 🔄 Session Management
- Session files are created automatically
- Keep session files secure
- Don't delete active session files
- Backup important sessions

## 🆘 Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Verify API credentials
   - Check phone number format
   - Ensure 2FA is properly configured

2. **Connection Issues**
   - Check internet connection
   - Verify Telegram API status
   - Review firewall settings

3. **Session Problems**
   - Delete corrupted session files
   - Re-authenticate accounts
   - Check file permissions

### 📞 Support

For issues and questions:
- Check logs for error messages
- Verify configuration settings
- Review Telegram API documentation
- Ensure all dependencies are installed

---

**⚡ Happy Roulette Spins!**
