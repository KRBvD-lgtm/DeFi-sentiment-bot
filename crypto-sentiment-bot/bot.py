#!/usr/bin/env python3
"""
Daily crypto 4H sentiment bot.

- Pulls the latest 4H candles for a list of coins from Binance's public API
- Computes % change on the latest completed 4H candle + RSI(14)
- Buckets that into a sentiment label
- Fills in a template tweet (no paid AI calls, fully free)
- Sends the daily digest to a Telegram chat, where you review and post manually

Run manually with:  python bot.py
Runs automatically via the GitHub Actions workflow in .github/workflows/daily-sentiment.yml
"""

import os
import sys
import random
import datetime
import requests

# ---------------------------------------------------------------------------
# CONFIG — edit this list to add/remove coins. Must be valid Binance USDT pairs.
# ---------------------------------------------------------------------------
COINS = [
    {"symbol": "MORPHOUSDT", "ticker": "MORPHO"},
    {"symbol": "AAVEUSDT",   "ticker": "AAVE"},
    {"symbol": "SYRUPUSDT",  "ticker": "SYRUP"},
    {"symbol": "UNIUSDT",    "ticker": "UNI"},
    {"symbol": "AEROUSDT",   "ticker": "AERO"},
]

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
INTERVAL = "4h"
CANDLE_LIMIT = 60  # enough history for RSI(14) with room to spare

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# DATA FETCH
# ---------------------------------------------------------------------------
def fetch_candles(symbol: str):
    """Fetch recent 4H candles for a symbol from Binance's public REST API."""
    params = {"symbol": symbol, "interval": INTERVAL, "limit": CANDLE_LIMIT}
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()
    # Each candle: [open_time, open, high, low, close, volume, close_time, ...]
    closes = [float(c[4]) for c in raw]
    return closes


# ---------------------------------------------------------------------------
# INDICATORS
# ---------------------------------------------------------------------------
def pct_change(closes):
    """% change of the latest completed candle vs the one before it."""
    prev, latest = closes[-2], closes[-1]
    return (latest - prev) / prev * 100


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


def build_tweet(ticker, pct, rsi, t_bucket, r_bucket):
    trend_line = random.choice(TREND_TEMPLATES[t_bucket]).format(
        ticker=f"${ticker}", pct=f"{abs(pct):.1f}" if pct >= 0 else f"{pct:.1f}"
    )
    rsi_line = random.choice(RSI_NOTES[r_bucket]).format(rsi=f"{rsi:.0f}" if rsi else "N/A")
    tweet = f"{trend_line} {rsi_line}".strip()
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
    lines = [f"📊 Daily 4H sentiment draft — {today}", ""]

    for coin in COINS:
        try:
            closes = fetch_candles(coin["symbol"])
            pct = pct_change(closes)
            rsi = compute_rsi(closes)
            t_bucket = trend_bucket(pct)
            r_bucket = rsi_bucket(rsi)
            tweet = build_tweet(coin["ticker"], pct, rsi, t_bucket, r_bucket)

            lines.append(f"— ${coin['ticker']} —")
            lines.append(tweet)
            lines.append("")
        except Exception as e:
            print(f"Error processing {coin['symbol']}: {e}", file=sys.stderr)
            lines.append(f"— ${coin['ticker']} —")
            lines.append("⚠️ Couldn't fetch data this run, skipped.")
            lines.append("")

    message = "\n".join(lines).strip()
    send_telegram_message(message)
    print("Done.")


if __name__ == "__main__":
    main()
