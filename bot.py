#!/usr/bin/env python3
"""
Daily crypto 4H sentiment bot.

- Pulls hourly prices + volume from CoinGecko's public API and buckets them into 4H closes
- Computes % change, RSI(14), a 50-period trend confirmation, and a volume-context note
- Buckets that into a sentiment label
- Fills in a template tweet (no paid AI calls, fully free)
- Sends the daily digest to a Telegram chat/channel, where you review and post manually

Run manually with:  python bot.py
Runs automatically via the GitHub Actions workflow in .github/workflows/daily-sentiment.yml
"""

import os
import sys
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

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sentiment-bot/1.0)"}
BUCKET_HOURS = 4  # matches "4H" candles
DAYS_HISTORY = 14  # gives hourly granularity with enough history for RSI(14) and SMA(50)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------
def fetch_candles(coin_id: str, retries: int = 5):
    """
    Fetch hourly price + volume points from CoinGecko and bucket them into 4H
    closes/volumes. CoinGecko doesn't apply the regional blocking that Binance's
    API does, so this works reliably from GitHub Actions runners. Retries
    several times on transient errors (timeouts, brief rate limits).
    Returns (closes, volumes) — parallel lists, oldest to newest, closed buckets only.
    """
    url = COINGECKO_URL.format(id=coin_id)
    params = {"vs_currency": "usd", "days": DAYS_HISTORY}

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            raw = resp.json()
            price_points = raw.get("prices", [])          # [timestamp_ms, price]
            volume_points = raw.get("total_volumes", [])  # [timestamp_ms, volume]

            bucket_seconds = BUCKET_HOURS * 3600

            price_buckets = {}
            for ts_ms, price in price_points:
                bucket_key = int(ts_ms // 1000) // bucket_seconds
                price_buckets[bucket_key] = price  # last price in bucket wins (close)

            volume_buckets = {}
            for ts_ms, vol in volume_points:
                bucket_key = int(ts_ms // 1000) // bucket_seconds
                volume_buckets[bucket_key] = volume_buckets.get(bucket_key, 0) + vol  # sum volume in bucket

            now_bucket = int(datetime.datetime.utcnow().timestamp()) // bucket_seconds
            closed_keys = sorted(k for k in price_buckets if k < now_bucket)  # drop still-forming bucket

            closes = [price_buckets[k] for k in closed_keys]
            volumes = [volume_buckets.get(k, 0) for k in closed_keys]

            if len(closes) < 15:
                raise ValueError(f"only got {len(closes)} closed 4H buckets, need at least 15")
            return closes, volumes
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(20)  # longer pause before retrying, gives rate limits real time to clear
    raise last_error


# ---------------------------------------------------------------------------
# INDICATORS
# ---------------------------------------------------------------------------
def pct_change(closes):
    """% change of the latest completed candle vs the one before it."""
    prev, latest = closes[-2], closes[-1]
    return (latest - prev) / prev * 100


def compute_sma(closes, period=50):
    """Simple moving average over the last `period` closes. None if not enough data."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def trend_confirmation_note(closes):
    """
    Checks the latest close against a longer (50-period, ~8 days on 4H candles)
    moving average, to say whether this 4H move lines up with or fights the
    broader multi-day trend. Returns None if there isn't enough history yet.
    """
    sma = compute_sma(closes, period=50)
    if sma is None:
        return None
    latest = closes[-1]
    if latest > sma * 1.01:
        return "This lines up with the broader multi-day uptrend."
    elif latest < sma * 0.99:
        return "This is running against the broader multi-day downtrend."
    else:
        return "Price is hovering right around its multi-day average."


def volume_note(volumes):
    """
    Compares the latest closed 4H bucket's volume to the average of the prior
    20 buckets, to flag whether this move happened on real interest or thin
    volume. Returns None if there isn't enough history yet.
    """
    if len(volumes) < 21:
        return None
    latest_vol = volumes[-1]
    prior_avg = sum(volumes[-21:-1]) / 20
    if prior_avg <= 0:
        return None
    ratio = latest_vol / prior_avg
    if ratio >= 1.4:
        return "Volume is well above average — real interest behind this move."
    elif ratio <= 0.6:
        return "Volume is thin here — low conviction behind this move."
    else:
        return None  # roughly average volume, not worth commenting on


def compute_rsi(closes, period=14):
    """Standard RSI (Wilder's smoothing) over the closes list."""
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


# ---------------------------------------------------------------------------
# SENTIMENT BUCKETING
# ---------------------------------------------------------------------------
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


def rsi_bucket(rsi):
    if rsi is None:
        return "unknown"
    if rsi >= 70:
        return "overbought"
    elif rsi <= 30:
        return "oversold"
    else:
        return "neutral"


# ---------------------------------------------------------------------------
# TEMPLATES — plain-language, no LLM call, picked pseudo-randomly per run
# ---------------------------------------------------------------------------
TREND_TEMPLATES = {
    "strong_bullish": [
        "{ticker} ripping on the 4H, up {pct}%. Momentum is clearly with the buyers right now.",
        "Strong move on {ticker} — {pct}% higher on the 4H candle. Buyers firmly in control.",
        "{ticker} breaking out, +{pct}% on the 4H. This is a real momentum candle, not noise.",
    ],
    "bullish": [
        "{ticker} grinding higher, up {pct}% on the 4H. Buyers have a slight edge.",
        "Modest strength in {ticker}, +{pct}% on the 4H candle. Trend leaning up.",
        "{ticker} ticking up {pct}% on the 4H — nothing explosive, but the bias is bullish.",
    ],
    "neutral": [
        "{ticker} basically flat on the 4H, {pct}%. Market's undecided here.",
        "Quiet 4H candle for {ticker}, {pct}% change. Range-bound for now.",
        "{ticker} chopping sideways, {pct}% on the 4H — no clear direction yet.",
    ],
    "bearish": [
        "{ticker} slipping, down {pct}% on the 4H. Sellers have a slight edge.",
        "Modest weakness in {ticker}, {pct}% on the 4H candle. Bias leaning down.",
        "{ticker} cooling off, {pct}% on the 4H — nothing dramatic, but sellers are active.",
    ],
    "strong_bearish": [
        "{ticker} getting hit hard, {pct}% on the 4H. Sellers firmly in control.",
        "Sharp drop on {ticker} — {pct}% on the 4H candle. Real selling pressure here.",
        "{ticker} breaking down, {pct}% on the 4H. This is a real distribution candle, not noise.",
    ],
}

RSI_NOTES = {
    "overbought": [
        "RSI at {rsi} — stretched, watch for a pullback.",
        "RSI reads {rsi}, into overbought territory.",
        "RSI sitting at {rsi}, getting hot up here.",
    ],
    "oversold": [
        "RSI at {rsi} — stretched to the downside, watch for a bounce.",
        "RSI reads {rsi}, into oversold territory.",
        "RSI sitting at {rsi}, getting washed out down here.",
    ],
    "neutral": [
        "RSI at {rsi}, nothing extreme either way.",
        "RSI reads {rsi} — room to move in either direction.",
        "RSI sitting at {rsi}, no extreme yet.",
    ],
    "unknown": [""],
}


# ---------------------------------------------------------------------------
# SENTIMENT EMOJI
# ---------------------------------------------------------------------------
TREND_EMOJI = {
    "strong_bullish": "🚀",
    "bullish": "🚀",
    "neutral": "😐",
    "bearish": "🐻",
    "strong_bearish": "🐻",
}


def build_tweet(ticker, pct, rsi, t_bucket, r_bucket, trend_note=None, vol_note=None):
    emoji = TREND_EMOJI.get(t_bucket, "")
    trend_line = random.choice(TREND_TEMPLATES[t_bucket]).format(
        ticker=f"${ticker}", pct=f"{abs(pct):.1f}" if pct >= 0 else f"{pct:.1f}"
    )
    rsi_line = random.choice(RSI_NOTES[r_bucket]).format(rsi=f"{rsi:.0f}" if rsi else "N/A")
    parts = [emoji, trend_line, rsi_line]
    if trend_note:
        parts.append(trend_note)
    if vol_note:
        parts.append(vol_note)
    tweet = " ".join(p for p in parts if p).strip()
    tweet += " Not financial advice."
    return tweet


# ---------------------------------------------------------------------------
# TELEGRAM DELIVERY
# ---------------------------------------------------------------------------
def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — printing instead of sending.\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=15)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Daily 4H Signal — {today}", ""]

    for i, coin in enumerate(COINS):
        if i > 0:
            time.sleep(8)  # bigger stagger so 9 coins don't trip CoinGecko's free-tier rate limit
        try:
            closes, volumes = fetch_candles(coin["id"])
            pct = pct_change(closes)
            rsi = compute_rsi(closes)
            t_bucket = trend_bucket(pct)
            r_bucket = rsi_bucket(rsi)
            trend_note = trend_confirmation_note(closes)
            vol_note = volume_note(volumes)
            tweet = build_tweet(coin["ticker"], pct, rsi, t_bucket, r_bucket, trend_note, vol_note)

            lines.append(f"— ${coin['ticker']} —")
            lines.append(tweet)
            lines.append("")
        except Exception as e:
            print(f"Error processing {coin['id']}: {e}", file=sys.stderr)
            lines.append(f"— ${coin['ticker']} —")
            lines.append("⚠️ Couldn't fetch data this run, skipped.")
            lines.append("")

    message = "\n".join(lines).strip()
    send_telegram_message(message)
    print("Done.")


if __name__ == "__main__":
    main()
