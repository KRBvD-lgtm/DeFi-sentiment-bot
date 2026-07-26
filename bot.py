#!/usr/bin/env python3
"""
Daily multi-timeframe DeFi sentiment bot.

- Pulls price + volume from CoinGecko and buckets it into 4H, Daily, and Weekly closes
- Computes % change, RSI(14), a 50-day trend confirmation, volume context, and
  support/resistance PER coin, combining all three timeframes into one confluence read
- Pulls live TVL (Total Value Locked) from DefiLlama for coins that are actual DeFi protocols
- Deletes the previous post and sends a fresh one, so the channel always shows exactly one live post

Run manually with:  python bot.py
Runs automatically via the GitHub Actions workflow in .github/workflows/daily-sentiment.yml
"""

import os
import sys
import json
import time
import random
import datetime
import requests

# ---------------------------------------------------------------------------
# CONFIG — edit this list to add/remove coins. "id" must be the coin's
# CoinGecko API id (find it on the coin's CoinGecko page, listed as "API ID").
# Ordered by market cap, largest to smallest.
# ---------------------------------------------------------------------------
COINS = [
    {"id": "ethereum",             "ticker": "ETH"},
    {"id": "hyperliquid",          "ticker": "HYPE"},
    {"id": "chainlink",            "ticker": "LINK"},
    {"id": "uniswap",              "ticker": "UNI"},
    {"id": "ondo-finance",         "ticker": "ONDO"},
    {"id": "aave",                 "ticker": "AAVE"},
    {"id": "morpho",               "ticker": "MORPHO"},
    {"id": "aerodrome-finance",    "ticker": "AERO"},
    {"id": "pendle",               "ticker": "PENDLE"},
]

# Maps ticker -> the name DefiLlama lists the protocol under (case-insensitive
# match). Base-layer tokens / oracles (ETH, HYPE, LINK) don't have a
# meaningful "protocol TVL" the way a lending/DEX/yield protocol does, so
# they're left out and simply won't show a TVL line.
DEFILLAMA_NAMES = {
    "AAVE": "aave",
    "UNI": "uniswap",
    "AERO": "aerodrome",
    "MORPHO": "morpho",
    "PENDLE": "pendle",
    "ONDO": "ondo finance",
}

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"
DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sentiment-bot/1.0)"}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Where we remember the previous post's message ID between runs, so the next
# run can delete it before posting the new one — keeps the channel to a
# single always-current post. This file gets committed back to the repo by
# the GitHub Actions workflow after each run.
STATE_FILE = "last_message.json"


# ---------------------------------------------------------------------------
# DATA FETCH — generic fetch + bucket, reused for 4H, daily, and weekly
# ---------------------------------------------------------------------------
def fetch_and_bucket(coin_id: str, days: int, bucket_seconds: int, retries: int = 5, min_buckets: int = 15):
    """
    Fetch price + volume points from CoinGecko over `days` of history, then
    bucket them into windows of `bucket_seconds` each. Returns (closes, volumes)
    — parallel lists, oldest to newest, closed buckets only (the still-forming
    current bucket is dropped). Retries on transient errors / rate limits.
    """
    url = COINGECKO_URL.format(id=coin_id)
    params = {"vs_currency": "usd", "days": days}

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            raw = resp.json()
            price_points = raw.get("prices", [])
            volume_points = raw.get("total_volumes", [])

            price_buckets = {}
            for ts_ms, price in price_points:
                bucket_key = int(ts_ms // 1000) // bucket_seconds
                price_buckets[bucket_key] = price  # last price in bucket wins (close)

            volume_buckets = {}
            for ts_ms, vol in volume_points:
                bucket_key = int(ts_ms // 1000) // bucket_seconds
                volume_buckets[bucket_key] = volume_buckets.get(bucket_key, 0) + vol

            now_bucket = int(datetime.datetime.utcnow().timestamp()) // bucket_seconds
            closed_keys = sorted(k for k in price_buckets if k < now_bucket)

            closes = [price_buckets[k] for k in closed_keys]
            volumes = [volume_buckets.get(k, 0) for k in closed_keys]

            if len(closes) < min_buckets:
                raise ValueError(f"only got {len(closes)} closed buckets, need at least {min_buckets}")
            return closes, volumes
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(20)
    raise last_error


def fetch_timeframes(coin_id: str):
    """
    Two API calls per coin:
    - 14 days of hourly data -> bucketed into 4H closes/volumes
    - 250 days of (auto daily-granularity) data -> bucketed into Daily closes/volumes
      AND separately into Weekly closes/volumes from that same call
    Returns a dict with closes_4h, volumes_4h, closes_daily, volumes_daily,
    closes_weekly, volumes_weekly.
    """
    closes_4h, volumes_4h = fetch_and_bucket(coin_id, days=14, bucket_seconds=4 * 3600, min_buckets=15)

    # CoinGecko auto-switches to daily granularity for days > 90, so this
    # single call gives us enough history for both Daily and Weekly buckets.
    closes_daily, volumes_daily = fetch_and_bucket(coin_id, days=250, bucket_seconds=86400, min_buckets=20)
    closes_weekly, volumes_weekly = fetch_and_bucket(coin_id, days=250, bucket_seconds=7 * 86400, min_buckets=15)

    return {
        "closes_4h": closes_4h, "volumes_4h": volumes_4h,
        "closes_daily": closes_daily, "volumes_daily": volumes_daily,
        "closes_weekly": closes_weekly, "volumes_weekly": volumes_weekly,
    }


def fetch_tvl_index(retries: int = 3):
    """
    One call for ALL protocols at once (not per coin). Returns a dict of
    {lowercase protocol name: protocol data} for easy lookup. Returns None
    if the fetch fails after retries — TVL notes are skipped gracefully
    rather than breaking the whole run.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(DEFILLAMA_PROTOCOLS_URL, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            protocols = resp.json()
            return {p["name"].lower(): p for p in protocols if "name" in p}
        except Exception as e:
            print(f"TVL fetch failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(10)
    return None


def find_protocol(tvl_index, target_name):
    if tvl_index is None:
        return None
    target = target_name.lower()
    if target in tvl_index:
        return tvl_index[target]
    for name_lower, proto in tvl_index.items():
        if target in name_lower:
            return proto
    return None


# ---------------------------------------------------------------------------
# INDICATORS
# ---------------------------------------------------------------------------
def pct_change(closes):
    prev, latest = closes[-2], closes[-1]
    return (latest - prev) / prev * 100


def format_price(p):
    if p >= 100:
        return f"{p:,.2f}"
    elif p >= 1:
        return f"{p:.3f}"
    else:
        return f"{p:.5f}"


def format_tvl(v):
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    elif v >= 1e6:
        return f"{v / 1e6:.1f}M"
    else:
        return f"{v:,.0f}"


def compute_sma(closes, period=50):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def trend_bucket(pct):
    if pct >= 5:
        return "strong_bullish"
    elif pct >= 1.5:
        return "bullish"
    elif pct <= -5:
        return "strong_bearish"
    elif pct <= -1.5:
        return "bearish"
    else:
        return "neutral"


def label_for(bucket):
    return {
        "strong_bullish": "Bullish", "bullish": "Bullish",
        "neutral": "Neutral",
        "bearish": "Bearish", "strong_bearish": "Bearish",
    }[bucket]


def rsi_bucket(rsi):
    if rsi is None:
        return "unknown"
    if rsi >= 70:
        return "overbought"
    elif rsi <= 30:
        return "oversold"
    else:
        return "neutral"


def trend_confirmation_note(closes, period=50, label="multi-day"):
    sma = compute_sma(closes, period=period)
    if sma is None:
        return None
    latest = closes[-1]
    if latest > sma * 1.01:
        return f"Price is above its {period}-period {label} average — broader trend still up."
    elif latest < sma * 0.99:
        return f"Price is below its {period}-period {label} average — broader trend still down."
    else:
        return f"Price is hovering right around its {period}-period {label} average."


def volume_note(volumes, label="daily"):
    if len(volumes) < 21:
        return None
    latest_vol = volumes[-1]
    prior_avg = sum(volumes[-21:-1]) / 20
    if prior_avg <= 0:
        return None
    ratio = latest_vol / prior_avg
    if ratio >= 1.4:
        return f"Volume ({label}) is well above average — real interest behind this move."
    elif ratio <= 0.6:
        return f"Volume ({label}) is thin here — low conviction behind this move."
    else:
        return None


def support_resistance_note(closes, lookback=30, label="daily"):
    window = closes[-lookback:] if len(closes) >= lookback else closes
    support = min(window)
    resistance = max(window)
    return (
        f"Nearby <b>support ~${format_price(support)}</b>, "
        f"<b>resistance ~${format_price(resistance)}</b> ({label}, last ~{len(window)}{label[0]})."
    )


def confluence_note(daily_label, weekly_label):
    if daily_label == weekly_label and daily_label != "Neutral":
        return f"Daily and weekly are both {daily_label.lower()} — trend confirmed across timeframes."
    elif daily_label != weekly_label and "Neutral" not in (daily_label, weekly_label):
        return "Daily and weekly are pulling in different directions — mixed signal, worth waiting for confirmation."
    else:
        return "Broader timeframes are fairly neutral right now."


def tvl_note(protocol):
    if protocol is None:
        return None
    tvl = protocol.get("tvl")
    change_1d = protocol.get("change_1d")
    if tvl is None:
        return None
    tvl_str = format_tvl(tvl)
    if change_1d is None:
        return f"<b>TVL ${tvl_str}</b>."
    direction = "up" if change_1d >= 0 else "down"
    return f"<b>TVL ${tvl_str}</b>, {direction} <b>{abs(change_1d):.1f}%</b> over 24h."


# ---------------------------------------------------------------------------
# TEMPLATES + EMOJI
# ---------------------------------------------------------------------------
TREND_EMOJI = {"Bullish": "🚀", "Neutral": "😐", "Bearish": "🐻"}

RSI_NOTES = {
    "overbought": ["RSI stretched, watch for a pullback.", "RSI into overbought territory."],
    "oversold": ["RSI stretched to the downside, watch for a bounce.", "RSI into oversold territory."],
    "neutral": ["RSI has room to move either direction.", "RSI shows no extreme yet."],
    "unknown": [""],
}


def overall_emoji(daily_label, weekly_label, h4_label):
    from collections import Counter
    counts = Counter([daily_label, weekly_label, h4_label])
    winner = counts.most_common(1)[0][0]
    return TREND_EMOJI[winner]


def build_message(ticker, tf, daily_rsi, protocol):
    pct_4h = pct_change(tf["closes_4h"])
    pct_daily = pct_change(tf["closes_daily"])
    pct_weekly = pct_change(tf["closes_weekly"])

    label_4h = label_for(trend_bucket(pct_4h))
    label_daily = label_for(trend_bucket(pct_daily))
    label_weekly = label_for(trend_bucket(pct_weekly))

    emoji = overall_emoji(label_daily, label_weekly, label_4h)

    def fmt_pct(p):
        return f"<b>{p:+.1f}%</b>"

    title_line = f"{emoji} <b>${ticker}</b>"
    price_line = f"Price: <b>${format_price(tf['closes_daily'][-1])}</b>"
    line_4h = f"4H: {fmt_pct(pct_4h)} ({label_4h})"
    line_daily = f"Daily: {fmt_pct(pct_daily)} ({label_daily})"
    line_weekly = f"Weekly: {fmt_pct(pct_weekly)} ({label_weekly})"

    conf_note = confluence_note(label_daily, label_weekly)

    rsi_bucket_label = rsi_bucket(daily_rsi)
    rsi_display = f"{daily_rsi:.0f}" if daily_rsi is not None else "N/A"
    rsi_line = random.choice(RSI_NOTES[rsi_bucket_label])
    if rsi_line:
        rsi_line = f"<b>RSI</b> (Daily) <b>{rsi_display}</b> — {rsi_line[4:] if rsi_line.startswith('RSI ') else rsi_line}"

    vol_note = volume_note(tf["volumes_daily"], label="daily")
    sr_note = support_resistance_note(tf["closes_daily"], lookback=30, label="daily")
    tvl_line = tvl_note(protocol)

    parts = [title_line, price_line, line_4h, line_daily, line_weekly, conf_note, rsi_line, vol_note, sr_note, tvl_line]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# STATE — remember the last posted message so we can delete it next run
# ---------------------------------------------------------------------------
def load_last_message_id():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data.get("message_id")
    except Exception as e:
        print(f"Couldn't read state file, treating as no previous message: {e}", file=sys.stderr)
        return None


def save_last_message_id(message_id):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"message_id": message_id}, f)
    except Exception as e:
        print(f"Couldn't write state file: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# TELEGRAM DELIVERY
# ---------------------------------------------------------------------------
def delete_telegram_message(message_id):
    """
    Deletes the previous post so the channel only ever shows one live post.
    Fails silently (just logs) if the message is already gone, too old, or
    the bot lacks delete rights — a failed delete should never stop the new
    post from going out.
    """
    if not message_id or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        if not resp.ok:
            print(f"Couldn't delete previous message (non-fatal): {resp.text}", file=sys.stderr)
    except Exception as e:
        print(f"Error deleting previous message (non-fatal): {e}", file=sys.stderr)


def send_telegram_message(text: str):
    """Sends the message and returns the new message_id, or None if not sent."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing instead of sending.\n")
        print(text)
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    return result.get("result", {}).get("message_id")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Daily Signal — {today}", ""]

    tvl_index = fetch_tvl_index()

    for i, coin in enumerate(COINS):
        if i > 0:
            time.sleep(8)
        try:
            tf = fetch_timeframes(coin["id"])
            daily_rsi = compute_rsi(tf["closes_daily"])
            protocol = None
            if coin["ticker"] in DEFILLAMA_NAMES:
                protocol = find_protocol(tvl_index, DEFILLAMA_NAMES[coin["ticker"]])
            message = build_message(coin["ticker"], tf, daily_rsi, protocol)
            lines.append(message)
            lines.append("")
        except Exception as e:
            print(f"Error processing {coin['id']}: {e}", file=sys.stderr)
            lines.append(f"— ${coin['ticker']} —")
            lines.append("⚠️ Couldn't fetch data this run, skipped.")
            lines.append("")

    full_message = "\n".join(lines).strip()

    last_id = load_last_message_id()
    delete_telegram_message(last_id)

    new_id = send_telegram_message(full_message)
    if new_id:
        save_last_message_id(new_id)

    print("Done.")


if __name__ == "__main__":
    main()
