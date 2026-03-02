# state_manager.py for MBFC scraping service
"""
Manages scraping state in a local JSON file.
Tracks: all URLs, pending, completed, failed, current batch.
Survives restarts — picks up where it left off.
"""

import json
import os
from datetime import datetime
from typing import List, Optional
from logger import bot_logger

STATE_FILE = "state.json"


def _empty_state() -> dict:
    return {
        "all_urls": [],
        "pending": [],
        "completed": [],
        "failed": [],
        "current_batch": [],
        "total": 0,
        "batches_run": 0,
        "last_batch_at": None,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }


def load_state() -> dict:
    """Load state from disk. Returns empty state if file doesn't exist."""
    if not os.path.exists(STATE_FILE):
        bot_logger.logger.info("No state file found — starting fresh.")
        return _empty_state()
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        bot_logger.logger.info(
            f"State loaded: {len(state.get('completed', []))} completed, "
            f"{len(state.get('pending', []))} pending, "
            f"{len(state.get('failed', []))} failed"
        )
        return state
    except Exception as e:
        bot_logger.logger.error(f"Failed to load state file: {e}")
        return _empty_state()


def save_state(state: dict):
    """Save state to disk atomically."""
    state["updated_at"] = datetime.utcnow().isoformat()
    try:
        # Write to temp file first, then rename (atomic on Linux)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
        bot_logger.logger.debug("State saved to disk.")
    except Exception as e:
        bot_logger.logger.error(f"Failed to save state: {e}")


def init_state(all_urls: List[str]) -> dict:
    """
    Initialize state with a fresh list of URLs.
    Called after URL collection is complete.
    """
    state = _empty_state()
    deduped = list(dict.fromkeys(all_urls))  # preserve order, remove dupes
    state["all_urls"] = deduped
    state["pending"] = deduped.copy()
    state["total"] = len(deduped)
    save_state(state)
    bot_logger.logger.info(f"State initialized with {len(deduped)} URLs.")
    return state


def get_next_batch(state: dict, batch_size: int = 100) -> List[str]:
    """
    Pop the next batch_size URLs from the pending list.
    Saves the batch to current_batch in state (so we can recover if interrupted).
    Returns the batch.
    """
    batch = state["pending"][:batch_size]
    state["current_batch"] = batch
    save_state(state)
    return batch


def mark_batch_done(state: dict, succeeded: List[str], failed: List[str]):
    """
    After a batch runs, move URLs from pending into completed or failed.
    """
    processed = set(succeeded + failed)

    # Remove processed URLs from pending
    state["pending"] = [u for u in state["pending"] if u not in processed]

    # Add to completed/failed
    state["completed"].extend(succeeded)
    state["failed"].extend(failed)

    # Clear current batch
    state["current_batch"] = []
    state["batches_run"] = state.get("batches_run", 0) + 1
    state["last_batch_at"] = datetime.utcnow().isoformat()

    save_state(state)
    bot_logger.logger.info(
        f"Batch done: +{len(succeeded)} completed, +{len(failed)} failed. "
        f"Remaining pending: {len(state['pending'])}"
    )


def reset_failed_to_pending(state: dict):
    """Move all failed URLs back to pending for retry."""
    count = len(state["failed"])
    state["pending"] = state["failed"] + state["pending"]
    state["failed"] = []
    save_state(state)
    bot_logger.logger.info(f"Reset {count} failed URLs back to pending.")
    return count


def get_status(state: dict) -> dict:
    """Return a summary of the current state."""
    total = state.get("total", 0)
    completed = len(state.get("completed", []))
    pending = len(state.get("pending", []))
    failed = len(state.get("failed", []))

    pct = round((completed / total * 100), 1) if total > 0 else 0

    return {
        "total": total,
        "completed": completed,
        "pending": pending,
        "failed": failed,
        "batches_run": state.get("batches_run", 0),
        "last_batch_at": state.get("last_batch_at"),
        "percent_done": pct,
        "has_urls": total > 0,
        "is_done": pending == 0 and total > 0,
    }


def clear_state():
    """Delete the state file completely (used by /restart)."""
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        bot_logger.logger.info("State file deleted.")
