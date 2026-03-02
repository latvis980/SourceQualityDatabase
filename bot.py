# bot.py for MBFC scraping service
"""
MBFC Bulk Scraper — Telegram Bot Controller

Commands:
  /status          - Show progress summary
  /collect         - Collect all source URLs from MBFC category pages (run first)
  /scrape_next     - Scrape the next 100 pages
  /scrape_next 50  - Scrape the next N pages (custom size)
  /failed          - Show failed URLs
  /retry_failed    - Retry all failed URLs
  /skip <url>      - Manually skip a URL (move to completed)
  /restart         - Wipe state and start from scratch
  /help            - Show all commands
"""

import asyncio
import os
import traceback
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters
)

from logger import bot_logger
from state_manager import (
    load_state, save_state, init_state, get_next_batch,
    mark_batch_done, reset_failed_to_pending, get_status, clear_state
)
from url_collector import collect_all_urls
from batch_scraper import run_batch

# -------------------------------------------------------
# CONFIG
# -------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
DEFAULT_BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

# Global lock — prevents two batches running simultaneously
_scrape_lock = asyncio.Lock()
_is_collecting = False
_is_scraping = False


# -------------------------------------------------------
# SECURITY: only respond to your own chat ID
# -------------------------------------------------------

def _authorized(update: Update) -> bool:
    if ALLOWED_CHAT_ID == 0:
        return True  # No restriction configured
    return update.effective_chat.id == ALLOWED_CHAT_ID


async def _reject(update: Update):
    await update.message.reply_text("Unauthorized.")


# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------

async def _send(update: Update, text: str):
    """Send a message, splitting if it exceeds Telegram's limit."""
    MAX = 4000
    if len(text) <= MAX:
        await update.message.reply_text(text)
        return
    # Split into chunks
    for i in range(0, len(text), MAX):
        await update.message.reply_text(text[i: i + MAX])
        await asyncio.sleep(0.3)


def _format_status(state: dict) -> str:
    s = get_status(state)
    if not s["has_urls"]:
        return (
            "No URLs collected yet.\n"
            "Run /collect first to fetch all MBFC source URLs."
        )

    last = s["last_batch_at"] or "never"
    if last != "never":
        try:
            dt = datetime.fromisoformat(last)
            last = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass

    done_str = "Yes" if s["is_done"] else "No"
    return (
        f"MBFC Scraper Status\n"
        f"{'='*30}\n"
        f"Total URLs:    {s['total']}\n"
        f"Completed:     {s['completed']} ({s['percent_done']}%)\n"
        f"Pending:       {s['pending']}\n"
        f"Failed:        {s['failed']}\n"
        f"Batches run:   {s['batches_run']}\n"
        f"Last batch:    {last}\n"
        f"Finished:      {done_str}\n"
    )


# -------------------------------------------------------
# COMMAND HANDLERS
# -------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _reject(update)
    text = (
        "MBFC Scraper Bot — Commands\n"
        "============================\n"
        "/status          - Show progress\n"
        "/collect         - Collect all source URLs (run once at start)\n"
        "/scrape_next     - Scrape next 100 pages\n"
        "/scrape_next 50  - Scrape next N pages\n"
        "/failed          - List failed URLs\n"
        "/retry_failed    - Retry failed URLs\n"
        "/skip <url>      - Mark a URL as skipped\n"
        "/restart         - Wipe all state and restart\n"
        "/help            - Show this message\n"
    )
    await _send(update, text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return await _reject(update)
    state = load_state()
    await _send(update, _format_status(state))


async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect all MBFC source URLs from category pages."""
    global _is_collecting

    if not _authorized(update):
        return await _reject(update)

    if _is_collecting:
        await _send(update, "URL collection is already running. Please wait.")
        return

    if _is_scraping:
        await _send(update, "A scrape batch is currently running. Please wait for it to finish.")
        return

    state = load_state()
    if state["total"] > 0:
        s = get_status(state)
        await _send(update,
            f"URLs already collected: {s['total']} total, {s['pending']} pending.\n"
            f"Use /restart if you want to start fresh and re-collect."
        )
        return

    _is_collecting = True
    await _send(update, "Starting URL collection from all MBFC category pages...\nThis takes 2-3 minutes.")

    collected_msgs = []

    async def on_progress(msg: str):
        collected_msgs.append(msg)
        # Send live updates every 3 messages to avoid spam
        if len(collected_msgs) % 3 == 0:
            await _send(update, "\n".join(collected_msgs[-3:]))

    try:
        all_urls = await collect_all_urls(progress_callback=on_progress)
        state = init_state(all_urls)
        s = get_status(state)
        await _send(update,
            f"URL collection complete.\n"
            f"Total unique source URLs found: {s['total']}\n\n"
            f"Ready to scrape. Use /scrape_next to start the first batch."
        )
    except Exception as e:
        bot_logger.logger.error(f"URL collection failed: {e}\n{traceback.format_exc()}")
        await _send(update, f"URL collection failed:\n{str(e)}")
    finally:
        _is_collecting = False


async def cmd_scrape_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scrape the next batch of pages."""
    global _is_scraping

    if not _authorized(update):
        return await _reject(update)

    if _is_scraping:
        await _send(update, "A scrape is already running. Wait for it to finish.")
        return

    if _is_collecting:
        await _send(update, "URL collection is running. Wait for it to finish, then use /scrape_next.")
        return

    # Parse optional batch size argument
    batch_size = DEFAULT_BATCH_SIZE
    if context.args:
        try:
            batch_size = int(context.args[0])
            if batch_size < 1 or batch_size > 500:
                await _send(update, "Batch size must be between 1 and 500.")
                return
        except ValueError:
            await _send(update, "Usage: /scrape_next [number]\nExample: /scrape_next 50")
            return

    state = load_state()

    if not state["total"]:
        await _send(update,
            "No URLs collected yet. Run /collect first."
        )
        return

    s = get_status(state)
    if s["is_done"]:
        await _send(update,
            f"All {s['total']} pages already scraped!\n"
            f"Failed: {s['failed']} — use /retry_failed if needed."
        )
        return

    if s["pending"] == 0:
        await _send(update,
            f"No pending URLs. Completed: {s['completed']}, Failed: {s['failed']}\n"
            f"Use /retry_failed to retry failures."
        )
        return

    actual_size = min(batch_size, s["pending"])
    batch_number = s["batches_run"] + 1

    await _send(update,
        f"Starting batch {batch_number}: {actual_size} pages "
        f"({s['pending']} remaining after this)..."
    )

    batch_urls = get_next_batch(state, actual_size)
    _is_scraping = True

    progress_msgs = []

    async def on_progress(msg: str):
        # Send progress updates to Telegram periodically
        progress_msgs.append(msg)
        if len(progress_msgs) % 5 == 0:
            await _send(update, msg)

    try:
        batch_result = await run_batch(
            urls=batch_urls,
            batch_number=batch_number,
            progress_callback=on_progress,
        )

        mark_batch_done(state, batch_result.succeeded, batch_result.failed)
        state = load_state()  # reload after update
        s = get_status(state)

        summary = batch_result.summary_text(batch_number)
        overall = (
            f"\nOverall progress: {s['completed']}/{s['total']} ({s['percent_done']}%)\n"
            f"Remaining: {s['pending']} pending, {s['failed']} failed"
        )

        if s["pending"] > 0:
            overall += f"\n\nUse /scrape_next to continue with the next batch."
        else:
            overall += f"\n\nAll pages processed! Use /retry_failed for any failures."

        await _send(update, summary + overall)

    except Exception as e:
        bot_logger.logger.error(f"Batch failed: {e}\n{traceback.format_exc()}")
        # Don't mark as done — leave pending so user can retry
        await _send(update, f"Batch failed with error:\n{str(e)}\n\nState not updated — try /scrape_next again.")
    finally:
        _is_scraping = False


async def cmd_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the list of failed URLs."""
    if not _authorized(update):
        return await _reject(update)

    state = load_state()
    failed = state.get("failed", [])

    if not failed:
        await _send(update, "No failed URLs.")
        return

    lines = [f"Failed URLs ({len(failed)} total):"]
    # Show max 50 to avoid huge messages
    for url in failed[:50]:
        lines.append(f"  {url}")
    if len(failed) > 50:
        lines.append(f"  ... and {len(failed) - 50} more")

    lines.append(f"\nUse /retry_failed to retry all of these.")
    await _send(update, "\n".join(lines))


async def cmd_retry_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move failed URLs back to pending and immediately scrape them."""
    global _is_scraping

    if not _authorized(update):
        return await _reject(update)

    if _is_scraping:
        await _send(update, "A scrape is already running. Wait for it to finish.")
        return

    state = load_state()
    failed_count = len(state.get("failed", []))

    if failed_count == 0:
        await _send(update, "No failed URLs to retry.")
        return

    count = reset_failed_to_pending(state)
    state = load_state()

    await _send(update,
        f"Moved {count} failed URLs back to pending.\n"
        f"Use /scrape_next to process them."
    )


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a specific URL as completed/skipped."""
    if not _authorized(update):
        return await _reject(update)

    if not context.args:
        await _send(update, "Usage: /skip <url>")
        return

    url = context.args[0].strip()
    state = load_state()

    if url in state.get("pending", []):
        state["pending"].remove(url)
        state["completed"].append(url)
        save_state(state)
        await _send(update, f"Skipped (marked as completed):\n{url}")
    elif url in state.get("failed", []):
        state["failed"].remove(url)
        state["completed"].append(url)
        save_state(state)
        await _send(update, f"Removed from failed and marked completed:\n{url}")
    else:
        await _send(update, f"URL not found in pending or failed:\n{url}")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wipe state and start from scratch."""
    global _is_scraping, _is_collecting

    if not _authorized(update):
        return await _reject(update)

    if _is_scraping or _is_collecting:
        await _send(update, "A job is currently running. Stop it first.")
        return

    clear_state()
    _is_scraping = False
    _is_collecting = False

    await _send(update,
        "State cleared. All progress wiped.\n"
        "Run /collect to start fresh."
    )


async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await _send(update, "Unknown command. Use /help to see available commands.")


# -------------------------------------------------------
# STARTUP
# -------------------------------------------------------

async def on_startup(application: Application):
    """Send a startup message to the configured chat."""
    if ALLOWED_CHAT_ID:
        try:
            state = load_state()
            s = get_status(state)
            if s["has_urls"]:
                msg = (
                    f"MBFC Scraper bot started.\n"
                    f"Resuming: {s['completed']}/{s['total']} done, "
                    f"{s['pending']} pending.\n"
                    f"Use /scrape_next to continue."
                )
            else:
                msg = (
                    "MBFC Scraper bot started.\n"
                    "No state found. Use /collect to begin."
                )
            await application.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=msg)
        except Exception as e:
            bot_logger.logger.error(f"Could not send startup message: {e}")


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    bot_logger.logger.info("Starting MBFC Scraper Telegram bot...")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("collect", cmd_collect))
    app.add_handler(CommandHandler("scrape_next", cmd_scrape_next))
    app.add_handler(CommandHandler("failed", cmd_failed))
    app.add_handler(CommandHandler("retry_failed", cmd_retry_failed))
    app.add_handler(CommandHandler("skip", cmd_skip))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    bot_logger.logger.info("Bot running. Waiting for commands...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
