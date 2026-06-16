# Bitomat Fee Monitor

This script checks Bitomat's live `USDT -> PLN` and `BTC -> PLN` sell rates, compares them with market references, and sends a Telegram alert when either token's commission is below your target threshold.

## What it compares

- Bitomat sell prices: pulled from `https://api.bitomat.com/getRates`
- USDT reference price:
  - first tries Google Finance
  - rejects unrealistic Google Finance values
  - falls back to CoinGecko if Google Finance changes its page markup or returns an invalid quote
- BTC reference price:
  - uses CoinGecko

Commission is calculated as:

`((reference_price - bitomat_sell_price) / reference_price) * 100`

## Setup

1. For local runs, copy `.env.example` to `.env`
2. Put your Telegram bot token and chat ID into `.env`
3. Run:

```powershell
py .\bitomat_usdt_monitor.py --dry-run
```

Run the unit tests:

```powershell
py -m unittest -v
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

## GitHub Schedule

This repo also includes a GitHub Actions workflow in `.github/workflows/bitomat-monitor.yml`.

GitHub Actions runs the monitor roughly three times per day. GitHub does not guarantee exact start times, so the script does not depend on the clock anymore.

Instead, the script checks Bitomat whenever GitHub starts it and uses `alert_state.json` to decide whether Telegram should be notified.

## Alert Logic

The target is controlled by `COMMISSION_THRESHOLD_PERCENT`.

Telegram is notified separately for USDT and BTC when:

- commission enters the target zone
- commission leaves the target zone
- commission is still in the target zone but changes meaningfully

By default, "meaningfully" means at least `0.10` percentage points. You can change that with the optional `ALERT_CHANGE_PERCENT` secret.

## GitHub Secrets

If you run this from GitHub Actions, create these repository secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `COMMISSION_THRESHOLD_PERCENT`

The workflow reads them automatically, so you do not need to commit a `.env` file.

Optional:

- `ALERT_CHANGE_PERCENT`

## Manual GitHub Test

To test immediately in GitHub:

1. Open `Actions`
2. Open `Bitomat Monitor`
3. Click `Run workflow`

The manual run uses the same state logic as the automatic runs.
