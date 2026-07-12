# Virus Roulette Bot

Automates [VirusGift](https://virusgift.pro) roulette & free case for multiple Telegram accounts. Includes a live web dashboard and admin notifications via Telegram bot.

## Features

- **Multi-account** — accounts are loaded automatically from `.env` (`ACCOUNT1_*`, `ACCOUNT2_*`, …)
- **Free roulette spin** — waits for `nextFreeSpin`, handles subscription + partner click requirements
- **Daily free case** — opens FREE case when `nextCaseFreeSpin` is ready
- **Auto-claim Virus / Stars** — claims currency prizes to balance (inventory sweep on startup too)
- **Channel join / leave** — subscribes when required, unsubscribes after success
- **Partner clicks** — official mutations: `markTestSpinTaskClick` / Portal / Tonnel / Tonplay + mini-app open
- **Web dashboard** — live countdowns for roulette & case, Stars/Virus balances
- **Telegram admin bot** — `/start` status + prize notifications

## Requirements

- Python 3.11+
- Telegram API credentials ([my.telegram.org](https://my.telegram.org))
- Bot token from [@BotFather](https://t.me/BotFather)

## Setup

```bash
git clone https://github.com/th3ryks/VirusAutoRoulette.git
cd VirusAutoRoulette

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_ID=your_admin_id_here

ACCOUNT1_API_ID=your_api_id_1
ACCOUNT1_API_HASH=your_api_hash_1
ACCOUNT1_PHONE_NUMBER=+1234567890

# Optional second account
ACCOUNT2_API_ID=...
ACCOUNT2_API_HASH=...
ACCOUNT2_PHONE_NUMBER=...

# Optional dashboard (defaults shown)
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8765
```

Run:

```bash
python main.py
```

On first login you may need to enter the Telegram confirmation code (and 2FA if enabled). Session files (`account1.session`, …) are created automatically.

## Dashboard

After the bot starts, open:

**http://127.0.0.1:8765**

Per account you get:

| Field | Description |
|--------|-------------|
| Stars / Virus | Current balances |
| Roulette timer | Countdown until free spin (`READY` when available) |
| Case timer | Countdown until daily free case |
| Online | Account client + token status |

API: `GET /api/accounts` — JSON used by the UI (polls every 2s; UI ticks every 250ms).

## Telegram bot

- `/start` — status for all accounts (admin only, `ADMIN_ID`)
- Notifications on successful spin / free case / claims

## How it works

1. Authenticates each user account via Pyrogram/Kurigram session
2. Opens VirusGift mini-app (`virus_play_bot`) and gets a bearer token
3. Worker loop (~10s):
   - if free **roulette** is ready → spin (handle clicks/subs) → claim Virus/Stars → unsubscribe
   - else if free **case** is ready → open case → claim → unsubscribe
4. On startup: sweeps inventory for unclaimed Virus/Stars prizes

### Accounts in `.env`

No need to edit `main.py`. Any complete set is loaded:

```text
ACCOUNT{N}_API_ID
ACCOUNT{N}_API_HASH
ACCOUNT{N}_PHONE_NUMBER
```

Example: `ACCOUNT3_*` → session `account3.session`.

## Project structure

```text
├── main.py              # Bot, workers, GraphQL, dashboard server
├── dashboard/
│   └── index.html       # Live web UI
├── requirements.txt
├── .env.example
├── accountN.session     # Created at runtime (gitignored)
└── README.md
```

## Dependencies

- `aiohttp` — GraphQL client + dashboard HTTP server
- `aiogram` — admin Telegram bot
- `kurigram` — user MTProto clients
- `TgCrypto`, `python-dotenv`, `loguru`

## Security

- Never commit `.env` or `*.session`
- Keep API hashes and bot tokens private
- Dashboard binds to `127.0.0.1` by default (local only)

## Troubleshooting

| Issue | What to check |
|--------|----------------|
| Login / 2FA | Phone format with `+`, correct API ID/hash |
| `TEST_SPIN_*_CLICK_REQUIRED` | Should auto-mark + open mini-app; check logs |
| `INSUFFICIENT_BALANCE` | Free spin already used; wait for next timer |
| Claim failed | Logs show GraphQL code; inventory is retried on next startup/spin |
| Dashboard offline | Bot must be running; open `http://HOST:PORT` from `.env` |

---

Happy spins.
