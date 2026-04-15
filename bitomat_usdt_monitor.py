#!/usr/bin/env python3
"""Monitor Bitomat's USDT sell commission and alert on Telegram."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


BITOMAT_RATES_URL = "https://api.bitomat.com/getRates"
GOOGLE_FINANCE_USDT_USD_URL = "https://www.google.com/finance/quote/USDT-USD"
GOOGLE_FINANCE_USD_PLN_URL = "https://www.google.com/finance/quote/USD-PLN"
COINGECKO_USDT_PLN_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=pln"
)
DEFAULT_THRESHOLD_PERCENT = 2.0
WARSAW_SCHEDULE_HOURS = {2, 10, 18}


@dataclass
class MarketSnapshot:
    bitomat_sell_pln: float
    reference_pln: float
    commission_percent: float
    reference_source: str


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_request(url: str, *, accept: str | None = None) -> urllib.request.Request:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def fetch_text(url: str, *, accept: str | None = None) -> str:
    request = build_request(url, accept=accept)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def fetch_json(url: str, *, accept: str | None = None) -> object:
    return json.loads(fetch_text(url, accept=accept))


def find_first(iterable: Iterable[float]) -> float | None:
    for item in iterable:
        return item
    return None


def fetch_bitomat_sell_rate_pln() -> float:
    data = fetch_json(BITOMAT_RATES_URL, accept="application/json")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Bitomat rates payload.")

    for entry in data:
        if not isinstance(entry, dict):
            continue
        from_currency = entry.get("fromCurrency") or {}
        if (
            isinstance(from_currency, dict)
            and from_currency.get("name") == "USDT"
            and entry.get("toCurrency") == "PLN"
        ):
            rate_bid = entry.get("rateBid")
            if isinstance(rate_bid, (int, float)):
                return float(rate_bid)

    raise RuntimeError("Could not find USDT/PLN sell rate in Bitomat response.")


def _extract_google_finance_price(html: str) -> float | None:
    patterns = [
        r'"price"\s*:\s*"([0-9]+(?:\.[0-9]+)?)"',
        r'"priceAmount"\s*:\s*"([0-9]+(?:\.[0-9]+)?)"',
        r'<div class="YMlKec fxKbKc">\$?([0-9]+(?:\.[0-9]+)?)<',
        r'<div class="YMlKec fxKbKc">([0-9]+(?:\.[0-9]+)?)<',
        r'aria-label="Price[^"]*\$([0-9]+(?:\.[0-9]+)?)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return float(match.group(1))

    # Google sometimes leaves the visible price as a bare "$1" in the source.
    candidates = [
        float(value)
        for value in re.findall(r"\$([0-9]+(?:\.[0-9]+)?)", html)
        if 0.8 <= float(value) <= 1.2
    ]
    return find_first(candidates)


def fetch_google_reference_pln() -> tuple[float, str]:
    usdt_usd_html = fetch_text(GOOGLE_FINANCE_USDT_USD_URL)
    usd_pln_html = fetch_text(GOOGLE_FINANCE_USD_PLN_URL)

    usdt_usd = _extract_google_finance_price(usdt_usd_html)
    usd_pln = _extract_google_finance_price(usd_pln_html)

    if usdt_usd is None or usd_pln is None:
        raise RuntimeError("Could not parse Google Finance quote.")

    return usdt_usd * usd_pln, "Google Finance"


def fetch_coingecko_reference_pln() -> tuple[float, str]:
    data = fetch_json(COINGECKO_USDT_PLN_URL, accept="application/json")
    try:
        return float(data["tether"]["pln"]), "CoinGecko fallback"
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Could not parse CoinGecko quote.") from exc


def fetch_reference_pln() -> tuple[float, str]:
    try:
        return fetch_google_reference_pln()
    except Exception:
        return fetch_coingecko_reference_pln()


def build_snapshot() -> MarketSnapshot:
    bitomat_sell_pln = fetch_bitomat_sell_rate_pln()
    reference_pln, reference_source = fetch_reference_pln()
    commission_percent = ((reference_pln - bitomat_sell_pln) / reference_pln) * 100
    return MarketSnapshot(
        bitomat_sell_pln=bitomat_sell_pln,
        reference_pln=reference_pln,
        commission_percent=commission_percent,
        reference_source=reference_source,
    )


def telegram_config() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in the environment."
        )
    return token, chat_id


def should_run_now() -> bool:
    now = datetime.now(ZoneInfo("Europe/Warsaw"))
    return now.minute == 0 and now.hour in WARSAW_SCHEDULE_HOURS


def send_telegram_message(message: str) -> None:
    token, chat_id = telegram_config()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")


def format_report(snapshot: MarketSnapshot, threshold_percent: float) -> str:
    return (
        "*Bitomat USDT alert*\n"
        f"Bitomat sell price: `{snapshot.bitomat_sell_pln:.4f} PLN`\n"
        f"Reference price ({snapshot.reference_source}): "
        f"`{snapshot.reference_pln:.4f} PLN`\n"
        f"Commission: `{snapshot.commission_percent:.2f}%`\n"
        f"Threshold: `{threshold_percent:.2f}%`\n"
        "Condition met: Bitomat commission is below your target."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check Bitomat's USDT sell rate against a market reference and send "
            "a Telegram alert when the commission drops below the configured threshold."
        )
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.getenv("COMMISSION_THRESHOLD_PERCENT", DEFAULT_THRESHOLD_PERCENT)),
        help="Alert threshold in percent. Default: 2.0",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the computed values without sending Telegram messages.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run immediately even if the current Poland time is outside the schedule.",
    )
    return parser.parse_args()


def main() -> int:
    load_env_file(Path(__file__).with_name(".env"))
    args = parse_args()

    if not args.dry_run and not args.force and os.getenv("GITHUB_ACTIONS") and not should_run_now():
        now = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"Skipping run outside Poland schedule window. Current Warsaw time: {now}")
        return 0

    try:
        snapshot = build_snapshot()
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        "Bitomat USDT sell price: "
        f"{snapshot.bitomat_sell_pln:.4f} PLN | "
        f"Reference ({snapshot.reference_source}): "
        f"{snapshot.reference_pln:.4f} PLN | "
        f"Commission: {snapshot.commission_percent:.2f}%"
    )

    if snapshot.commission_percent >= args.threshold:
        print(
            f"No alert sent because {snapshot.commission_percent:.2f}% "
            f"is not below {args.threshold:.2f}%."
        )
        return 0

    if args.dry_run:
        print("Dry run only, Telegram message not sent.")
        print(format_report(snapshot, args.threshold))
        return 0

    try:
        send_telegram_message(format_report(snapshot, args.threshold))
    except Exception as exc:
        print(f"Telegram error: {exc}", file=sys.stderr)
        return 1

    print("Telegram alert sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
