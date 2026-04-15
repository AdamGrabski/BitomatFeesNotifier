# Bitomat USDT Monitor

This script checks Bitomat's live `USDT -> PLN` sell rate, compares it with a market reference, and sends a Telegram alert when Bitomat's commission is below your target threshold.

## What it compares

- Bitomat sell price: pulled from `https://api.bitomat.com/getRates`
- Reference price:
  - first tries Google Finance
  - falls back to CoinGecko if Google Finance changes its page markup

Commission is calculated as:

`((reference_price - bitomat_sell_price) / reference_price) * 100`

## Setup

1. Copy `.env.example` to `.env`
2. Put your Telegram bot token and chat ID into `.env`
3. Run:

```powershell
py .\bitomat_usdt_monitor.py --dry-run
```

Then run it for real:

```powershell
py .\bitomat_usdt_monitor.py
```

## Telegram bot quick setup

1. Create a bot with [@BotFather](https://t.me/BotFather)
2. Send at least one message to your bot
3. Open:

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

4. Find your `chat.id` in the response and place it into `.env`

## Schedule

The Codex automation is set to run every day at:

- 02:00 Poland time
- 10:00 Poland time
- 18:00 Poland time

That matches "every 8 hours starting at 10 AM Poland time".
