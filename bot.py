#!/usr/bin/env python3
"""
Daily multi-timeframe DeFi sentiment bot.

- Pulls live price + volume data from Gate.io's exchange (real-time, no API key needed)
  and computes native 4H, Daily, and Weekly candles
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
# CONFIG — just the ticker symbol, used directly as {TICKER}_USDT on Gate.io.
# Ordered by market cap, largest to smallest.
# ---------------------------------------------------------------------------
COINS = [
    "ETH", "HYPE", "LINK", "UNI", "ONDO",
    "AAVE", "MORPHO", "AERO", "PENDLE",
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

GATE_CANDLES_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
GATE_TICKER_URL = "https://api.gateio.ws/api/v4/spot/tickers"
GATE_FUTURES_CONTRACT_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}"
DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
STABLECOIN_CHART_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; sentiment-bot/1.0)"}

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Where we remember the previous post's message ID between runs, so the next
# run can delete it before posting the new one — keeps the channel to a
# single always-current post. This file gets committed back to the repo by
# the GitHub Actions workflow after each run.
STATE_FILE = "last_message.json"

# Tracks each day's sentiment call per coin and whether it was directionally
# right 24h later — powers the accuracy summary. Also committed back to the
# repo by the workflow.
PREDICTIONS_FILE = "predictions.json"


# ---------------------------------------------------------------------------
# DATA FETCH — live exchange data from Gate.io (no API key needed, no
# regional blocking observed for public market data, broad altcoin coverage)
# ---------------------------------------------------------------------------
def fetch_gate_candles(ticker: str, interval: str, limit: int = 100, retries: int = 5):
    """
    Fetch native candles directly from Gate.io's public spot market API.
    Each raw candle is [timestamp, quote_volume, close, high, low, open].
    Returns a list of dicts, oldest to newest, sorted by timestamp.
    """
    pair = f"{ticker}_USDT"
    params = {"currency_pair": pair, "interval": interval, "limit": limit}
    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.get(GATE_CANDLES_URL, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            raw = resp.json()
            if not raw or not isinstance(raw, list):
                raise ValueError(f"empty or unexpected candle response: {raw}")
            candles = []
            for c in raw:
                candles.append({
                    "ts": int(c[0]),
                    "volume": float(c[1]),
                    "close": float(c[2]),
                    "high": float(c[3]),
                    "low": float(c[4]),
                    "open": float(c[5]),
                })
            candles.sort(key=lambda x: x["ts"])
            return candles
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(15)
    raise last_error


def fetch_live_price(ticker: str, retries: int = 3):
    """Fetch the true real-time last-traded price from Gate.io's ticker endpoint."""
    pair = f"{ticker}_USDT"
    params = {"currency_pair": pair}
    for attempt in range(retries):
        try:
            resp = requests.get(GATE_TICKER_URL, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return float(data[0]["last"])
        except Exception as e:
            print(f"Live price fetch failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(5)
    return None


def fetch_timeframes(ticker: str):
    """
    Three native-candle fetches from Gate.io: 4H, Daily (1d), and Weekly (7d).
    The most recent candle in each response is still "live"/forming, so it's
    dropped for analysis — % change, RSI, etc. are computed on closed candles
    only. The true current price is fetched separately via the live ticker.
    """
    c4h = fetch_gate_candles(ticker, "4h", limit=60)
    c1d = fetch_gate_candles(ticker, "1d", limit=300)
    c7d = fetch_gate_candles(ticker, "7d", limit=60)

    closes_4h = [c["close"] for c in c4h[:-1]]
    volumes_4h = [c["volume"] for c in c4h[:-1]]
    closes_daily = [c["close"] for c in c1d[:-1]]
    volumes_daily = [c["volume"] for c in c1d[:-1]]
    closes_weekly = [c["close"] for c in c7d[:-1]]
    volumes_weekly = [c["volume"] for c in c7d[:-1]]

    if len(closes_4h) < 15 or len(closes_daily) < 20 or len(closes_weekly) < 15:
        raise ValueError("not enough closed candles returned from Gate.io")

    live_price = fetch_live_price(ticker)
    if live_price is None:
        live_price = closes_daily[-1]  # fall back to last closed daily candle

    return {
        "closes_4h": closes_4h, "volumes_4h": volumes_4h,
        "closes_daily": closes_daily, "volumes_daily": volumes_daily,
        "closes_weekly": closes_weekly, "volumes_weekly": volumes_weekly,
        "live_price": live_price,
    }


def fetch_funding_rate(ticker: str, retries: int = 3):
    """
    Fetch the current perpetual futures funding rate for this ticker from
    Gate.io. Returns None gracefully if the coin has no USDT perpetual
    listed (not all altcoins do) — this is expected for some coins, not an error.
    """
    url = GATE_FUTURES_CONTRACT_URL.format(contract=f"{ticker}_USDT")
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 404:
                return None  # no perpetual market for this coin, not a real error
            resp.raise_for_status()
            data = resp.json()
            rate = data.get("funding_rate")
            return float(rate) if rate is not None else None
        except Exception as e:
            print(f"Funding rate fetch failed for {ticker} (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(5)
    return None


def fetch_stablecoin_trend(retries: int = 3):
    """
    Day-over-day % change in total stablecoin supply across all chains, from
    DefiLlama. Rising supply = capital parking in stablecoins (risk-off);
    falling supply = capital deploying into risk assets (risk-on). Returns
    (pct_change, total_usd) or None if the fetch fails.
    """
    for attempt in range(retries):
        try:
            resp = requests.get(STABLECOIN_CHART_URL, headers=HEADERS, timeout=25)
            resp.raise_for_status()
            data = resp.json()
            if not data or len(data) < 2:
                return None

            def total_usd(entry):
                usd_map = entry.get("totalCirculatingUSD", {})
                return sum(v for v in usd_map.values() if isinstance(v, (int, float)))

            latest = total_usd(data[-1])
            prev = total_usd(data[-2])
            if prev <= 0:
                return None
            pct = (latest - prev) / prev * 100
            return pct, latest
        except Exception as e:
            print(f"Stablecoin trend fetch failed (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(10)
    return None


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


def funding_rate_note(rate):
    """rate is a raw funding rate fraction (e.g. 0.0001 = 0.01%), or None if unavailable."""
    if rate is None:
        return None
    pct = rate * 100
    if abs(pct) < 0.01:
        return f"Funding rate: <b>{pct:+.3f}%</b> — balanced, no crowding either way."
    elif pct > 0:
        return f"Funding rate: <b>{pct:+.3f}%</b> — longs paying shorts, crowded long."
    else:
        return f"Funding rate: <b>{pct:+.3f}%</b> — shorts paying longs, crowded short."


def returns_series(closes):
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]


def pearson_correlation(x, y):
    n = len(x)
    if n < 2 or n != len(y):
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)


def correlation_note(alt_closes_daily, btc_closes_daily, window=30):
    """
    Rolling correlation between this coin's daily returns and BTC's daily
    returns over the last `window` days. Uses returns (% changes), not raw
    price levels, since price levels are trivially correlated for almost
    any two assets sharing a broad market trend.
    """
    if not btc_closes_daily:
        return None
    n = min(len(alt_closes_daily), len(btc_closes_daily), window + 1)
    if n < 11:
        return None
    alt_window = alt_closes_daily[-n:]
    btc_window = btc_closes_daily[-n:]
    corr = pearson_correlation(returns_series(alt_window), returns_series(btc_window))
    if corr is None:
        return None
    if corr >= 0.7:
        desc = "strongly correlated with BTC"
    elif corr >= 0.3:
        desc = "moderately correlated with BTC"
    elif corr <= -0.3:
        desc = "moving inversely to BTC"
    else:
        desc = "largely decoupled from BTC"
    return f"BTC correlation ({window}d): <b>{corr:+.2f}</b> — {desc}."


def biggest_mover_note(movers):
    """movers: list of (ticker, daily_pct_change) tuples."""
    if not movers:
        return None
    ticker, pct = max(movers, key=lambda m: abs(m[1]))
    direction = "📈" if pct >= 0 else "📉"
    return f"{direction} Biggest mover today: <b>${ticker} {pct:+.1f}%</b> (daily)"


def stablecoin_risk_note(result):
    """result: (pct_change, total_usd) from fetch_stablecoin_trend(), or None."""
    if result is None:
        return None
    pct, total = result
    total_str = format_tvl(total)
    if pct <= -0.3:
        return f"🟢 Risk-on: stablecoin supply down <b>{abs(pct):.2f}%</b> (total ${total_str}) — capital moving into risk assets."
    elif pct >= 0.3:
        return f"🔴 Risk-off: stablecoin supply up <b>{pct:.2f}%</b> (total ${total_str}) — capital parking in stablecoins."
    else:
        return f"⚪ Stablecoin supply roughly flat (total ${total_str}) — no clear market-wide risk shift."


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


def build_message(ticker, tf, daily_rsi, protocol, funding_rate=None, btc_closes_daily=None):
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
    price_line = f"Price: <b>${format_price(tf['live_price'])}</b>"
    line_4h = f"4H: {fmt_pct(pct_4h)} ({label_4h})"
    line_daily = f"Daily: {fmt_pct(pct_daily)} ({label_daily})"
    line_weekly = f"Weekly: {fmt_pct(pct_weekly)} ({label_weekly})"

    conf_note = confluence_note(label_daily, label_weekly)

    rsi_bucket_label = rsi_bucket(daily_rsi)
    rsi_display = f"{daily_rsi:.0f}" if daily_rsi is not None else "N/A"
    rsi_line = random.choice(RSI_NOTES[rsi_bucket_label])
    if rsi_line:
        rsi_line = f"<b>RSI</b> (Daily) <b>{rsi_display}</b> — {rsi_line[4:] if rsi_line.startswith('RSI ') else rsi_line}"

    funding_line = funding_rate_note(funding_rate)
    vol_note = volume_note(tf["volumes_daily"], label="daily")
    corr_note = correlation_note(tf["closes_daily"], btc_closes_daily) if btc_closes_daily else None
    sr_note = support_resistance_note(tf["closes_daily"], lookback=30, label="daily")
    tvl_line = tvl_note(protocol)

    parts = [
        title_line, price_line, line_4h, line_daily, line_weekly,
        conf_note, rsi_line, funding_line, vol_note, corr_note, sr_note, tvl_line,
    ]
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# STATE — remember the last posted message so we can delete it next run
# ---------------------------------------------------------------------------
def load_last_message_ids():
    """Returns a list of previous message IDs (possibly empty). Handles the
    old single-message_id format too, for a smooth transition."""
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        if "message_ids" in data:
            return data["message_ids"]
        if data.get("message_id"):  # old single-ID format
            return [data["message_id"]]
        return []
    except Exception as e:
        print(f"Couldn't read state file, treating as no previous message: {e}", file=sys.stderr)
        return []


def save_last_message_ids(message_ids):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"message_ids": message_ids}, f)
    except Exception as e:
        print(f"Couldn't write state file: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# ACCURACY TRACKING — remember each day's Daily-timeframe call per coin, then
# check 24h later (i.e. the next run) whether it was directionally right
# ---------------------------------------------------------------------------
def load_predictions_state():
    if not os.path.exists(PREDICTIONS_FILE):
        return {"pending": {}, "history": []}
    try:
        with open(PREDICTIONS_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"Couldn't read predictions state, starting fresh: {e}", file=sys.stderr)
        return {"pending": {}, "history": []}


def save_predictions_state(state):
    try:
        with open(PREDICTIONS_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Couldn't write predictions state: {e}", file=sys.stderr)


def evaluate_and_record_prediction(state, ticker, today_str, new_predicted_label, live_price):
    """
    If there's a pending prediction for this ticker from a previous run,
    checks whether it was directionally correct (comparing today's price to
    the price at prediction time), records it to history, then stores
    today's fresh prediction as the new pending entry.
    """
    pending = state["pending"].get(ticker)
    if pending and pending.get("date") != today_str:
        old_price = pending.get("price")
        old_predicted = pending.get("predicted_label")
        if old_price and old_predicted:
            try:
                actual_pct = (live_price - old_price) / old_price * 100
                actual_label = label_for(trend_bucket(actual_pct))
                correct = actual_label == old_predicted
                state["history"].append({
                    "date": pending["date"], "ticker": ticker,
                    "predicted": old_predicted, "actual": actual_label, "correct": correct,
                })
                state["history"] = state["history"][-1000:]  # keep it bounded
            except Exception as e:
                print(f"Prediction eval error for {ticker}: {e}", file=sys.stderr)

    state["pending"][ticker] = {"date": today_str, "predicted_label": new_predicted_label, "price": live_price}


def accuracy_summary_note(state, days=7):
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    recent = [h for h in state["history"] if h["date"] >= cutoff]
    if not recent:
        return None
    correct = sum(1 for h in recent if h["correct"])
    total = len(recent)
    pct = correct / total * 100
    return f"📊 <b>Accuracy (last {days}d):</b> {correct}/{total} calls correct ({pct:.0f}%)"


# ---------------------------------------------------------------------------
# TELEGRAM DELIVERY
# ---------------------------------------------------------------------------
def chunk_message(full_text: str, limit: int = 4000):
    """
    Splits the full digest into Telegram-safe chunks (under the 4096 char
    limit, with some margin). Splits only at blank-line section boundaries,
    so a single coin's block is never cut in half.
    """
    sections = full_text.split("\n\n")
    chunks = []
    current = ""
    for section in sections:
        candidate = f"{current}\n\n{section}" if current else section
        if len(candidate) > limit and current:
            chunks.append(current)
            current = section
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


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


def send_telegram_messages(full_text: str):
    """
    Splits the digest into Telegram-safe chunks (if needed) and sends each
    as its own message. Returns the list of message_ids actually sent (so
    they can all be deleted before next run's post).
    """
    chunks = chunk_message(full_text)
    message_ids = []
    for chunk in chunks:
        mid = send_telegram_message(chunk)
        if mid:
            message_ids.append(mid)
        time.sleep(1)  # brief pause between multi-part sends
    return message_ids


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    now = datetime.datetime.utcnow()
    today_display = now.strftime("%Y-%m-%d %H:%M UTC")
    today_str = now.strftime("%Y-%m-%d")

    lines = [f"📊 Daily Signal — {today_display}", ""]

    # Market-wide context, shown once up top
    stable_result = fetch_stablecoin_trend()
    risk_note = stablecoin_risk_note(stable_result)
    if risk_note:
        lines.append(risk_note)
        lines.append("")

    tvl_index = fetch_tvl_index()

    # BTC daily closes, fetched once and reused for every coin's correlation note
    btc_closes_daily = None
    try:
        btc_candles = fetch_gate_candles("BTC", "1d", limit=40)
        btc_closes_daily = [c["close"] for c in btc_candles[:-1]]
    except Exception as e:
        print(f"BTC fetch for correlation failed (non-fatal, correlation notes will be skipped): {e}", file=sys.stderr)

    pred_state = load_predictions_state()
    movers = []

    for i, ticker in enumerate(COINS):
        if i > 0:
            time.sleep(8)
        try:
            tf = fetch_timeframes(ticker)
            daily_rsi = compute_rsi(tf["closes_daily"])
            pct_daily = pct_change(tf["closes_daily"])
            movers.append((ticker, pct_daily))

            protocol = None
            if ticker in DEFILLAMA_NAMES:
                protocol = find_protocol(tvl_index, DEFILLAMA_NAMES[ticker])

            funding_rate = fetch_funding_rate(ticker)

            message = build_message(ticker, tf, daily_rsi, protocol, funding_rate, btc_closes_daily)
            lines.append(message)
            lines.append("")

            label_daily = label_for(trend_bucket(pct_daily))
            evaluate_and_record_prediction(pred_state, ticker, today_str, label_daily, tf["live_price"])
        except Exception as e:
            print(f"Error processing {ticker}: {e}", file=sys.stderr)
            lines.append(f"— ${ticker} —")
            lines.append("⚠️ Couldn't fetch data this run, skipped.")
            lines.append("")

    # Footer: biggest mover + rolling accuracy
    footer_parts = [biggest_mover_note(movers), accuracy_summary_note(pred_state, days=7)]
    footer = "\n".join(p for p in footer_parts if p)
    if footer:
        lines.append(footer)

    full_message = "\n".join(lines).strip()

    last_ids = load_last_message_ids()
    for mid in last_ids:
        delete_telegram_message(mid)

    new_ids = send_telegram_messages(full_message)
    if new_ids:
        save_last_message_ids(new_ids)

    save_predictions_state(pred_state)

    print("Done.")


if __name__ == "__main__":
    main()
