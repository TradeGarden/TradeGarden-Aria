"""
journal.py - Stage 6: REVIEW
==============================
Now backed by PostgreSQL via database.py.
Nothing is ever lost. Survives restarts.
"""
from database import append_trade, load_journal, load_recent, load_closed_trades

# Re-export so the rest of the system imports from journal.py unchanged
__all__ = ["append_trade","load_journal","load_recent","load_closed_trades"]
