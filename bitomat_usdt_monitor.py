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
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


BITOMAT_RATES_URL = "https://api.bitomat.com/getRates"
GOOGLE_FINANCE_USDT_USD_URL = "https://www.google.com/finance/quote/USDT-USD"
GOOGLE_FINANCE_USD_PLN_URL = "https://www.google.com/finance/quote/USD-PLN"
COINGECKO_USDT_PLN_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=pln"
)
DEFAULT_THRESHOLD_PERCENT = 2.0
DEFAULT_ALERT_CHANGE_PERCENT = 0.10
DEFAULT_STATE_FILE = "alert_state.json"
GOOD_ZONE = "GOOD"
BAD_ZONE = "BAD"


@dataclass
class MarketSnapshot:
    bitomat_sell_pln: float
    reference_pln: float
    commission_percent: float
    reference_source: str


@dataclass
class AlertDecision:
    should_alert: bool
    alert_type: str
    reason: str


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
        "*Bitomat USDT status*\n"
        f"Bitomat sell price: `{snapshot.bitomat_sell_pln:.4f} PLN`\n"
        f"Reference price ({snapshot.reference_source}): "
        f"`{snapshot.reference_pln:.4f} PLN`\n"
        f"Commission: `{snapshot.commission_percent:.2f}%`\n"
        f"Target threshold: `{threshold_percent:.2f}%`\n"
    )


def current_zone(snapshot: MarketSnapshot, threshold_percent: float) -> str:
    if snapshot.commission_percent < threshold_percent:
        return GOOD_ZONE
    return BAD_ZONE


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if isinstance(data, dict):
        return data
    return {}


def state_from_snapshot(
    snapshot: MarketSnapshot,
    threshold_percent: float,
    previous_state: dict[str, object],
    decision: AlertDecision,
) -> dict[str, object]:
    zone = current_zone(snapshot, threshold_percent)
    state = {
        "zone": zone,
        "commission_percent": round(snapshot.commission_percent, 4),
        "bitomat_sell_pln": round(snapshot.bitomat_sell_pln, 4),
        "reference_pln": round(snapshot.reference_pln, 4),
        "reference_source": snapshot.reference_source,
        "threshold_percent": threshold_percent,
        "last_checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if decision.should_alert:
        state["last_alert_type"] = decision.alert_type
        state["last_alert_at_utc"] = state["last_checked_at_utc"]
        state["last_alert_commission_percent"] = round(snapshot.commission_percent, 2)
    else:
        for key in (
            "last_alert_type",
            "last_alert_at_utc",
            "last_alert_commission_percent",
        ):
            if key in previous_state:
                state[key] = previous_state[key]

    return state


def save_state(path: Path, state: dict[str, object]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def decide_alert(
    snapshot: MarketSnapshot,
    threshold_percent: float,
    change_threshold_percent: float,
    previous_state: dict[str, object],
) -> AlertDecision:
    zone = current_zone(snapshot, threshold_percent)
    previous_zone = previous_state.get("zone")

    if previous_zone is None:
        if zone == GOOD_ZONE:
            return AlertDecision(
                True,
                "entered_good_zone",
                "Bitomat commission is already below your target.",
            )
        return AlertDecision(
            False,
            "initial_bad_zone",
            "Initial state saved; commission is outside your target.",
        )

    if previous_zone == BAD_ZONE and zone == GOOD_ZONE:
        return AlertDecision(
            True,
            "entered_good_zone",
            "Bitomat commission dropped below your target.",
        )

    if previous_zone == GOOD_ZONE and zone == BAD_ZONE:
        return AlertDecision(
            True,
            "left_good_zone",
            "Bitomat commission moved back above your target.",
        )

    if zone == GOOD_ZONE:
        previous_alert_commission = previous_state.get("last_alert_commission_percent")
        if isinstance(previous_alert_commission, (int, float)):
            change = abs(snapshot.commission_percent - float(previous_alert_commission))
            if change >= change_threshold_percent:
                return AlertDecision(
                    True,
                    "good_zone_changed",
                    (
                        "Bitomat commission is still below target and changed "
                        f"by at least {change_threshold_percent:.2f} percentage points."
                    ),
                )
        else:
            return AlertDecision(
                True,
                "good_zone_confirmed",
                "Bitomat commission is below your target.",
            )

    return AlertDecision(
        False,
        "no_meaningful_change",
        "No meaningful alert-state change.",
    )


def format_alert_message(
    snapshot: MarketSnapshot,
    threshold_percent: float,
    decision: AlertDecision,
) -> str:
    status_line = {
        "entered_good_zone": "Target reached: Bitomat is below your commission target.",
        "good_zone_changed": "Target still active: commission changed meaningfully.",
        "good_zone_confirmed": "Target reached: Bitomat is below your commission target.",
        "left_good_zone": "Target ended: Bitomat moved above your commission target.",
    }.get(decision.alert_type, decision.reason)

    return f"{format_report(snapshot, threshold_percent)}{status_line}"


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
        "--state-file",
        default=os.getenv("ALERT_STATE_FILE", DEFAULT_STATE_FILE),
        help=f"Path to the alert state file. Default: {DEFAULT_STATE_FILE}",
    )
    parser.add_argument(
        "--change-threshold",
        type=float,
        default=float(
            os.getenv("ALERT_CHANGE_PERCENT", DEFAULT_ALERT_CHANGE_PERCENT)
        ),
        help=(
            "Alert again inside the good zone only when commission changes by at "
            "least this many percentage points. Default: 0.10"
        ),
    )
    return parser.parse_args()


def main() -> int:
    load_env_file(Path(__file__).with_name(".env"))
    args = parse_args()

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

    state_path = Path(args.state_file)
    previous_state = load_state(state_path)
    decision = decide_alert(
        snapshot,
        args.threshold,
        args.change_threshold,
        previous_state,
    )
    next_state = state_from_snapshot(
        snapshot,
        args.threshold,
        previous_state,
        decision,
    )

    print(f"Alert decision: {decision.alert_type} - {decision.reason}")

    if args.dry_run:
        print("Dry run only, Telegram message not sent and state not saved.")
        if decision.should_alert:
            print(format_alert_message(snapshot, args.threshold, decision))
        return 0

    if decision.should_alert:
        try:
            send_telegram_message(format_alert_message(snapshot, args.threshold, decision))
        except Exception as exc:
            print(f"Telegram error: {exc}", file=sys.stderr)
            return 1
        print("Telegram alert sent.")
    else:
        print("No Telegram alert sent.")

    save_state(state_path, next_state)
    print(f"State saved to {state_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
