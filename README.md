# MBFC Bulk Scraper

Standalone Railway service that scrapes all Media Bias/Fact Check source pages
and uploads the data to your VeriFlow Supabase database.
Controlled entirely via a Telegram bot — no web interface.

---

## Setup: Step by Step

### Step 1: Create a Telegram Bot

1. Open Telegram, search for @BotFather
2. Send `/newbot` and follow the prompts
3. Copy the bot token you receive

### Step 2: Get Your Chat ID

1. Search for @userinfobot on Telegram
2. Start a chat — it will reply with your user ID
3. Copy the number (e.g. `123456789`)

### Step 3: Deploy to Railway

1. Create a new Railway project
2. Connect this GitHub repo (or upload these files)
3. Add environment variables (see .env.example for all required values)
4. Railway will build and deploy automatically

### Step 4: Start the Bot

1. Open Telegram and find your new bot
2. Send `/collect` — this fetches all MBFC source URLs (takes ~3 min)
3. Once done, send `/scrape_next` to start the first batch of 100 pages
4. Check the results, then send `/scrape_next` again for the next batch
5. Repeat until done. Total: ~10,000 pages across ~100 batches.

---

## Bot Commands

| Command | Description |
|---|---|
| `/status` | Show overall progress |
| `/collect` | Fetch all source URLs from MBFC (run once at start) |
| `/scrape_next` | Scrape the next 100 pages |
| `/scrape_next 50` | Scrape a custom-size batch |
| `/failed` | Show URLs that failed to scrape |
| `/retry_failed` | Retry all failed URLs |
| `/skip <url>` | Mark a URL as skipped |
| `/restart` | Wipe all progress and start over |
| `/help` | Show all commands |

---

## How It Works

1. `/collect` visits all 10 MBFC category pages (Left, Right, Satire, etc.)
   and extracts every individual source URL. Saves ~10,000 URLs to `state.json`.

2. `/scrape_next` takes the next batch of URLs from the pending list,
   scrapes each page with Playwright (with ad blocking), extracts structured
   data with GPT-4o-mini, and writes to Supabase via upsert.

3. After each batch, the bot sends you a summary. You review it and send
   `/scrape_next` again when ready.

4. `state.json` persists all progress — if the service restarts, it picks
   up from where it left off.

---

## Supabase Table

Data is written to the `media_credibility` table using upsert on `domain`.
Each record has `source = 'mbfc_bulk'` to distinguish from live-lookup records.

---

## File Structure

```
bot.py               - Telegram bot (entry point)
url_collector.py     - Scrapes MBFC category pages for source URLs
batch_scraper.py     - Runs batches of page scrapes concurrently
mbfc_scraper.py      - Playwright scraper with ad blocking + AI extraction
supabase_writer.py   - Writes to Supabase with tier assignment
state_manager.py     - Reads/writes state.json for progress tracking
logger.py            - Logging setup
requirements.txt     - Python dependencies
nixpacks.toml        - Railway build config (installs Playwright + Chromium)
Procfile             - Railway start command
.env.example         - Environment variable reference
state.json           - Auto-created, tracks all progress (do not edit manually)
```
