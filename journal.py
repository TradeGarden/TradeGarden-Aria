"""
journal.py — Stage 6: REVIEW
==============================
Responsibilities:
  - Store every trade, decision, and action permanently
  - Nothing is ever deleted
  - Every trade becomes training data for reports and recommendations

Storage format: trade_journal.json (list of records)
Each record has a unique ID and timestamp.
"""

import json
import os
import uuid
from datetime import datetime

JOURNAL_FILE = "trade_journal.json"


# ──────────────────────────────────────────────
#  CORE READ / WRITE
# ──────────────────────────────────────────────

def _load() -> list:
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save(trades: list):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(trades, f, indent=2)


# ──────────────────────────────────────────────
#  APPEND
# ──────────────────────────────────────────────

def append_trade(entry: dict):
    """
    Append one record to the journal.
    Automatically adds id and recorded_at if missing.
    """
    entry.setdefault("id", str(uuid.uuid4())[:8])
    entry.setdefault("recorded_at", datetime.utcnow().isoformat())
    trades = _load()
    trades.append(entry)
    _save(trades)


# ──────────────────────────────────────────────
#  READ
# ──────────────────────────────────────────────

def load_journal() -> list:
    """Return all journal records, oldest first."""
    return _load()


def load_closed_trades() -> list:
    """Return only CLOSE records — completed trades."""
    return [t for t in _load() if t.get("action") == "CLOSE"]


def load_open_records() -> list:
    """Return only OPEN records."""
    return [t for t in _load() if t.get("action") == "OPEN"]


def load_recent(n: int = 30) -> list:
    """Return the last n records, newest first."""
    return list(reversed(_load()[-n:]))


# ──────────────────────────────────────────────
#  QUERY HELPERS
# ──────────────────────────────────────────────

def trades_in_range(days: int) -> list:
    """Return CLOSE records from the last N days."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    closed = load_closed_trades()
    result = []
    for t in closed:
        try:
            closed_at = datetime.fromisoformat(t.get("closed_at", ""))
            if closed_at >= cutoff:
                result.append(t)
        except Exception:
            pass
    return result


def trade_by_id(trade_id: str) -> dict | None:
    """Find a specific trade by its trade_id."""
    for t in _load():
        if t.get("trade_id") == trade_id:
            return t
    return None


def all_actions_for_trade(trade_id: str) -> list:
    """Return all journal records related to one trade_id (OPEN, SL moves, CLOSE)."""
    return [t for t in _load() if t.get("trade_id") == trade_id]
