Tech stack
Price/candle data: Gate.io public API — free, no key required, live exchange data, no regional blocking observed for public market data
TVL data: DefiLlama public API — free, no key required
Hosting/scheduling: GitHub Actions — free, no server, no VPS
Delivery: Telegram Bot API — posts directly to a public channel, deletes its own previous post each run
Language: Python 3, single file (bot.py), one dependency (requests)

Total cost: $0/month.

Repo structure
.
├── bot.py                                    # the whole bot — data, indicators, message, delivery
├── requirements.txt                          # just `requests`
├── last_message.json                         # auto-managed state file (previous post's message ID)
├── .github/workflows/daily-sentiment.yml     # the cron schedule that runs it
└── README.md
How it works, in more detail

Data source: Gate.io was chosen over CoinGecko's free tier after running into stale/delayed data on CoinGecko's anonymous public API. Gate.io's candlestick endpoint returns real, native OHLCV candles at 4H/Daily/Weekly granularity directly — no manual bucketing needed — and the separate ticker call gives a genuinely real-time current price. Gate.io lists 4,600+ coins, so coverage of smaller/newer tokens (HYPE, MORPHO, AERO, PENDLE, ONDO) is solid.

"Live" candle handling: the most recent candle in any Gate.io response is still actively forming (the current, in-progress period). The bot drops that one before computing % change / RSI / etc., so all technical analysis is based on fully closed candles — the live ticker price is used separately just for the displayed current price.

Sentiment logic: no AI/LLM calls — this is deterministic, rule-based logic. % change on each timeframe gets bucketed into Bullish/Neutral/Bearish, and the confluence note simply checks whether Daily and Weekly agree.

Support/resistance: approximated from the swing high/low of the last 30 closed daily candles (~1 month). Real candle data (not approximated from price points), but still a practical window rather than reaching back to all-time extremes.

TVL: pulled from DefiLlama's /protocols endpoint once per run (not once per coin), matched by protocol name. Base-layer tokens (ETH, HYPE, LINK) don't have a "protocol TVL" in the DeFi sense, so they simply don't show a TVL line.

Self-cleaning channel: the bot remembers the message ID of its last post (in last_message.json, committed back to the repo by the GitHub Actions workflow after each run) and deletes that message before posting the new one, so the channel always shows a single, current post rather than an accumulating feed.

Reliability: each Gate.io/DefiLlama call retries up to 5 times with backoff on transient errors, and coins are fetched with an 8-second stagger between each so a single flaky moment doesn't take down the whole run. If a coin's data can't be fetched, that coin is skipped for that run only — the rest of the message still posts.

Setup (if you're forking/rebuilding this)
1. Create a Telegram bot
Message @BotFather on Telegram
Send /newbot, follow the prompts
Save the bot token it gives you
2. Create a Telegram channel and grant admin rights
Create a new Channel in Telegram, make it public, pick a handle
Add your bot as an admin with both "Post Messages" and "Delete Messages" permission — the bot needs delete rights to keep the channel to a single live post
Your TELEGRAM_CHAT_ID is the channel handle, e.g. @YourChannelName
3. Fork/clone this repo, then add secrets

In your repo: Settings → Secrets and variables → Actions → New repository secret

TELEGRAM_BOT_TOKEN → your bot token
TELEGRAM_CHAT_ID → your channel handle

No API key is needed for Gate.io or DefiLlama — both are called anonymously.

4. Grant the workflow write permission

The workflow needs contents: write permission (already set in daily-sentiment.yml) so it can commit the updated last_message.json back to the repo after each run.

5. Test it

Go to Actions → Daily Crypto Sentiment Draft → Run workflow (use "Run workflow", not "Re-run all jobs" — the latter replays an old commit instead of your current code). Check Telegram after a minute or two.

Run it a second time afterward to confirm the delete-then-repost cycle works — the first run has no previous post to delete yet.

It'll now run automatically once a day from then on.

Customizing
Coins: edit the COINS list at the top of bot.py — just add the ticker (must have a TICKER_USDT pair on Gate.io)
Schedule: edit the cron line in .github/workflows/daily-sentiment.yml (always UTC)
Sentiment thresholds / wording: edit trend_bucket(), RSI_NOTES, and confluence_note() in bot.py
Support/resistance lookback window: change the lookback default in support_resistance_note()
TVL protocol mapping: edit the DEFILLAMA_NAMES dict if you add a coin that's a genuine DeFi protocol
Disclaimer

This is not financial advice. The sentiment logic is simple, rule-based technical analysis (price change across three timeframes, RSI, volume, swing high/low, and TVL) — not a trading signal, not a recommendation, and not a substitute for your own research.
