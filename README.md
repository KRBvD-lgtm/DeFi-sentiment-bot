Here it is — just select all of this text (from "# DeFi Signal Room" down to the final line) and paste it into the README editor on GitHub.

DeFi Signal Room — 4H Crypto Sentiment Bot

A free, fully automated bot that reads price action, momentum, and volume for 9 major DeFi/RWA tokens every 4 hours, and posts a plain-language sentiment read to a public Telegram channel — no manual analysis, no paid APIs, no VPS.

Live channel: t.me/DeFiSignalRoom 

What it does

Every 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC), the bot:

Pulls hourly price + volume data for each coin from CoinGecko's free public API
Buckets that into 4-hour candles
Computes, per coin: % change on the latest 4H candle, RSI(14), a 50-period multi-day trend check, volume context, and approximate support/resistance from the last ~5 days
Turns that into a plain-language message with a 🚀 (bullish), 🐻 (bearish), or 😐 (neutral) lead
Posts the digest automatically to the Telegram channel
Coins tracked (ordered by market cap)

$ETH, $HYPE, $LINK, $UNI, $ONDO, $AAVE, $MORPHO, $AERO, $PENDLE

Tech stack
Data source: CoinGecko public API — free, no key required
Hosting/scheduling: GitHub Actions — free, no server, no VPS
Delivery: Telegram Bot API — posts directly to a public channel
Language: Python 3, single file (bot.py), one dependency (requests)

Total cost: $0/month.

Repo structure

bot.py — the whole bot (logic, templates, delivery)
requirements.txt — just "requests"
.github/workflows/daily-sentiment.yml — the schedule that runs it
README.md — this file

How it works, in more detail

Data: CoinGecko doesn't expose fixed 4H candles on the free tier, so the bot pulls hourly price and volume points and buckets them into 4-hour windows itself, using the last price in each window as that candle's close.

Sentiment logic: no AI/LLM calls — this is deterministic, rule-based logic. % change and RSI get bucketed into strong bullish through strong bearish, and a pool of template phrasings is picked per bucket so wording varies run to run without needing an API call.

Support/resistance: approximated from the swing high/low of the last 30 closed 4H candles (about 5 days). This uses closing prices, not true intraday wicks, so treat it as a solid approximation rather than exact technical S/R.

Reliability: coins are fetched with an 8-second stagger between each and up to 5 retries with 20-second backoff, to stay under CoinGecko's free-tier rate limits. Occasional single-coin skips can still happen on a busy run — the bot logs those honestly rather than guessing.

Setup (if you're forking or rebuilding this)
Create a Telegram bot: message @BotFather, send /newbot, follow the prompts, save the bot token it gives you.
Create a Telegram channel (recommended) or get your personal chat ID:
For a public channel: create a new Channel in Telegram, make it public, pick a handle, add your bot as an admin with "Post Messages" permission. Your TELEGRAM_CHAT_ID is the channel handle, e.g. @YourChannelName.
For posting to yourself instead: message your bot directly, then visit https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates — your chat ID is the number under "chat":{"id": ...}.
Fork or clone this repo, then add secrets in Settings, Secrets and variables, Actions, New repository secret:
TELEGRAM_BOT_TOKEN — your bot token
TELEGRAM_CHAT_ID — your channel handle or chat ID
Test it: go to Actions, 4H Signal, Run workflow (use "Run workflow", not "Re-run all jobs" — the latter replays an old commit instead of your current code). Check Telegram after 1-2 minutes. It'll now run automatically every 4 hours from then on.
Customizing

Coins: edit the COINS list at the top of bot.py. Each entry needs a valid CoinGecko API id, found on the coin's CoinGecko page.
Schedule: edit the cron line in .github/workflows/daily-sentiment.yml (always UTC).
Sentiment thresholds or wording: edit trend_bucket(), rsi_bucket(), TREND_TEMPLATES, and RSI_NOTES in bot.py.
Support/resistance lookback window: change the lookback default in support_resistance_note().

Disclaimer

This is not financial advice. The sentiment logic is simple, rule-based technical analysis (price change, RSI, a moving average, volume, and swing high/low) — not a trading signal, not a recommendation, and not a substitute for your own research.
