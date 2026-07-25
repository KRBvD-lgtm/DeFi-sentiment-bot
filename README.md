# Daily Crypto 4H Sentiment Bot

Pulls 4H candles for $MORPHO, $AAVE, $SYRUP, $UNI, $AERO from Binance's free
public API, computes a sentiment read (% change + RSI), drafts a tweet per
coin from templates, and sends the daily digest to you on Telegram for
review before you post. Runs for free forever on GitHub Actions.

No paid APIs, no VPS, no AI API costs.

## What you get every day

A Telegram message like:

```
📊 Daily 4H sentiment draft — 2026-07-25 12:10 UTC

— $AAVE —
Modest strength in $AAVE, +2.3% on the 4H candle. Trend leaning up. RSI at 58, no extreme yet. Not financial advice.

— $UNI —
$UNI chopping sideways, -0.4% on the 4H — no clear direction yet. RSI reads 47 — room to move in either direction. Not financial advice.

...
```

You copy whichever draft(s) you like and post them yourself. Nothing posts
automatically — that's the point (and it's what keeps this free, since X
charges for automated posting access).

## One-time setup (about 10 minutes)

### 1. Create a Telegram bot to receive the drafts
1. In Telegram, message **@BotFather**
2. Send `/newbot`, follow the prompts, give it any name
3. BotFather will give you a **bot token** — save it, you'll need it below

### 2. Get your chat ID
1. Search for your new bot in Telegram and send it any message (e.g. "hi")
2. In a browser, visit:
   `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   (replace `<YOUR_BOT_TOKEN>` with your actual token)
3. Look for `"chat":{"id": ...}` in the response — that number is your **chat ID**

### 3. Put this code on GitHub
1. Create a new GitHub repository (private is fine, it's free)
2. Upload all the files in this folder, keeping the `.github/workflows/`
   folder structure intact
   - Easiest way: on the repo page, click "Add file" → "Upload files", drag
     everything in, including the hidden `.github` folder (use git if your
     browser hides dotfiles — see note below)

### 4. Add your secrets
1. In your repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret**, add:
   - Name: `TELEGRAM_BOT_TOKEN` → value: your bot token from step 1
   - Name: `TELEGRAM_CHAT_ID` → value: your chat ID from step 2

### 5. Test it
1. Go to the **Actions** tab in your repo
2. Click **Daily Crypto Sentiment Draft** → **Run workflow** → **Run workflow**
   (this is the `workflow_dispatch` trigger — it lets you fire it manually)
3. Check Telegram — you should get the digest within ~30 seconds

If it worked, you're done. It'll now run automatically every day at 12:10 UTC.

## Customizing

- **Change coins**: edit the `COINS` list at the top of `bot.py`. Must be
  valid Binance `...USDT` pairs (check on binance.com if unsure a pair exists)
- **Change the time it runs**: edit the `cron` line in
  `.github/workflows/daily-sentiment.yml`. Cron is always UTC. Candles close
  every 4h at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC — running ~10 min
  after one of those gives you a just-closed candle
- **Change sentiment thresholds/wording**: edit `trend_bucket()`,
  `rsi_bucket()`, and the `TREND_TEMPLATES` / `RSI_NOTES` dictionaries in
  `bot.py`

## Uploading the hidden `.github` folder via a browser

GitHub's drag-and-drop upload UI does support dotfiles/dot-folders, but your
computer's file picker might hide them. If you don't see `.github` when
browsing to upload:
- **Mac**: in the file picker, press `Cmd+Shift+.` to reveal hidden files/folders
- **Windows**: in File Explorer, View → Show → Hidden items, then select the
  folder in the upload dialog

Or, if you're comfortable with git:
```bash
git init
git add .
git commit -m "daily sentiment bot"
git branch -M main
git remote add origin <your-repo-url>
git push -u origin main
```

## Notes

- This is **not financial advice** and the sentiment logic is simple
  (% change + RSI) — treat it as a first draft you review, not a signal to
  trade or post blindly
- Binance's public API has no cost and no auth for this kind of read-only
  market data, so this has no ongoing fees
- GitHub Actions is free for this use case (scheduled jobs on a public or
  private repo, well within the free minutes allowance for a once-daily run)
