DeFi Signal Room — Multi-Timeframe Crypto Sentiment Bot

A free, fully automated bot that reads live price action across 4H, Daily, and Weekly timeframes for 9 major DeFi/RWA tokens, layers in funding rates, BTC correlation, TVL, and a market-wide risk gauge, tracks its own accuracy over time, and posts it all to a public Telegram channel — no manual analysis, no paid APIs, no VPS.

Live channel: t.me/DeFi_Signal_Room (update this to your actual channel handle)

What it does

Once a day, the bot:

Pulls native 4H, Daily, and Weekly candles directly from Gate.io's live exchange API (real-time, no API key required), plus the true current live price via Gate.io's ticker endpoint
Fetches BTC's daily closes once and reuses them for every coin's correlation check
Fetches funding rates from Gate.io's perpetual futures market, where available
Fetches live TVL (Total Value Locked) from DefiLlama for coins that are actual DeFi protocols
Fetches day-over-day stablecoin supply change from DefiLlama as a market-wide risk gauge
Computes, per coin: % change across all three timeframes, a confluence read (does Daily agree with Weekly?), RSI(14) on the Daily close, volume context, funding rate context, 30-day BTC correlation, and approximate support/resistance
Logs that day's Daily-timeframe call, and 24 hours later checks whether it was directionally right — building a real, honest accuracy track record over time
Formats everything into a clean, one-variable-per-line message per coin, plus a market-wide context line up top and a footer with the day's biggest mover and rolling 7-day accuracy
Deletes the previous post(s) and sends the new one(s) — the channel always shows exactly one live, current post (auto-splitting into multiple messages if the digest is too long for a single Telegram message)
Coins tracked (ordered by market cap)

$ETH, $HYPE, $LINK, $UNI, $ONDO, $AAVE, $MORPHO, $AERO, $PENDLE

Example output (per coin)

📊 Daily Signal — 2026-07-28 12:10 UTC

🟢 Risk-on: stablecoin supply down 0.42% (total $203.10B) — capital moving into risk assets.

🚀 $AAVE
Price: $142.33
4H: +0.3% (Neutral)
Daily: +2.1% (Bullish)
Weekly: +6.4% (Bullish)
Daily and weekly are both bullish — trend confirmed across timeframes.
RSI (Daily) 58 — has room to move either direction.
Funding rate: +0.012% — longs paying shorts, crowded long.
Volume (daily) is well above average — real interest behind this move.
BTC correlation (30d): +0.61 — moderately correlated with BTC.
Nearby support ~$130.00, resistance ~$149.80 (daily, last ~30d).
TVL $14.20B, up 1.8% over 24h.

... (8 more coins) ...

📈 Biggest mover today: $AERO -6.2% (daily)
📊 Accuracy (last 7d): 41/54 calls correct (76%)

Tech stack
Price/candle/funding data: Gate.io public API — free, no key required, live exchange data, no regional blocking observed for public market data
TVL and stablecoin data: DefiLlama public API — free, no key required
Hosting/scheduling: GitHub Actions — free, no server, no VPS
Delivery: Telegram Bot API — posts directly to a public channel, deletes its own previous post(s) each run
Language: Python 3, single file (bot.py), one dependency (requests)

Total cost: $0/month.

Repo structure

bot.py — the whole bot: data, indicators, message building, delivery
requirements.txt — just "requests"
last_message.json — auto-managed state (previous post's message ID(s))
predictions.json — auto-managed state (accuracy tracking history)
.github/workflows/daily-sentiment.yml — the cron schedule that runs it
README.md — this file

How it works, in more detail

Data source: Gate.io was chosen over CoinGecko's free tier after running into stale/delayed data on CoinGecko's anonymous public API. Gate.io's candlestick endpoint returns real, native OHLCV candles at 4H/Daily/Weekly granularity directly, and a separate ticker call gives a genuinely real-time current price. Gate.io lists 4,600+ coins, so coverage of smaller/newer tokens (HYPE, MORPHO, AERO, PENDLE, ONDO) is solid.

"Live" candle handling: the most recent candle in any Gate.io response is still actively forming. The bot drops that one before computing % change, RSI, etc., so all technical analysis is based on fully closed candles — the live ticker price is used separately just for the displayed current price.

Sentiment logic: no AI/LLM calls — this is deterministic, rule-based logic. % change on each timeframe gets bucketed into Bullish/Neutral/Bearish, and the confluence note simply checks whether Daily and Weekly agree.

Funding rate: pulled from Gate.io's perpetual futures market for each coin. Not every altcoin has a listed perpetual, so this line is simply omitted for coins without one — not treated as an error.

BTC correlation: a real 30-day Pearson correlation computed on daily returns (percentage changes), not raw price levels — price levels are trivially correlated for almost any two assets sharing a broad market trend, so returns are the meaningful comparison. BTC's daily closes are fetched once per run and reused across all 9 coins rather than re-fetched per coin.

Support/resistance: approximated from the swing high/low of the last 30 closed daily candles (~1 month) — real candle data, but still a practical window rather than reaching back to all-time extremes.

TVL: pulled from DefiLlama's /protocols endpoint once per run (not once per coin), matched by protocol name. Base-layer tokens (ETH, HYPE, LINK) don't have a "protocol TVL" in the DeFi sense, so they simply don't show a TVL line.

Stablecoin risk gauge: day-over-day % change in total stablecoin supply across all chains, from DefiLlama's stablecoin charts endpoint. Rising supply suggests capital parking in stablecoins (risk-off); falling supply suggests capital deploying into risk assets (risk-on). Shown once, at the top of the message, as market-wide context for everything below it.

Accuracy tracking: each day, the bot records its Daily-timeframe call (Bullish/Neutral/Bearish) and the price at that moment for every coin. The next day, before making a new call, it checks the previous call against the price movement since then, logs whether it was directionally correct, and rolls that into a 7-day accuracy percentage shown in the footer. This is a genuine, unfiltered track record — including the misses — not a cherry-picked highlight reel.

Self-cleaning channel: the bot remembers the message ID(s) of its last post in last_message.json (committed back to the repo by the GitHub Actions workflow after each run) and deletes them before posting the new digest, so the channel always shows a single, current post. If the digest is too long for one Telegram message (4096 character limit), it automatically splits into multiple messages at clean section boundaries — never cutting a coin's data in half — and tracks/deletes all parts together.

Reliability: each API call retries up to 5 times with backoff on transient errors, and coins are fetched with an 8-second stagger between each so a single flaky moment doesn't take down the whole run. If a coin's data can't be fetched, that coin is skipped for that run only — the rest of the message still posts.

Setup (if you're forking/rebuilding this)
Create a Telegram bot: message @BotFather, send /newbot, follow the prompts, save the bot token it gives you.
Create a Telegram channel and grant admin rights: create a new public Channel, add your bot as an admin with both "Post Messages" and "Delete Messages" permission — the bot needs delete rights to keep the channel to a single live post. Your TELEGRAM_CHAT_ID is the channel handle, e.g. @YourChannelName.
Fork or clone this repo, then add secrets in Settings, Secrets and variables, Actions, New repository secret:
TELEGRAM_BOT_TOKEN — your bot token
TELEGRAM_CHAT_ID — your channel handle

No API key is needed for Gate.io or DefiLlama — both are called anonymously.

The workflow already has contents: write permission set, needed so it can commit the updated state files (last_message.json, predictions.json) back to the repo after each run.
Test it: go to Actions, Daily Crypto Sentiment Draft, Run workflow (use "Run workflow", not "Re-run all jobs" — the latter replays an old commit instead of your current code). Check Telegram after a minute or two. Run it a second time afterward (or wait a day) to see the accuracy summary appear, since the first run has nothing yet to grade.

It'll now run automatically once a day from then on.

Customizing

Coins: edit the COINS list at the top of bot.py — just add the ticker (must have a TICKER_USDT pair on Gate.io).
Schedule: edit the cron line in .github/workflows/daily-sentiment.yml (always UTC).
Sentiment thresholds or wording: edit trend_bucket(), RSI_NOTES, and confluence_note() in bot.py.
Support/resistance lookback window: change the lookback default in support_resistance_note().
TVL protocol mapping: edit the DEFILLAMA_NAMES dict if you add a coin that's a genuine DeFi protocol.
BTC correlation window: change the window default in correlation_note().
Accuracy rolling window: change the days default in accuracy_summary_note().

Disclaimer

This is not financial advice. The sentiment logic is simple, rule-based technical analysis (price change across three timeframes, RSI, volume, funding rates, BTC correlation, swing high/low, TVL, and stablecoin flows) — not a trading signal, not a recommendation, and not a substitute for your own research. The accuracy tracker reports real, unfiltered results, including misses — past performance is not a guarantee of future results.
