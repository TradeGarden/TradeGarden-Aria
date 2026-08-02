"""
database.py — PostgreSQL Persistent Storage
=============================================
Replaces all file-based storage (paper_balance.txt, paper_position.json, trade_journal.json).
Everything lives in PostgreSQL — survives restarts, sleep cycles, and redeploys.

Tables:
  account       — balance, equity, paper/live mode
  positions     — open trades
  trade_history — every completed trade, permanent
  journal       — every action (OPEN, CLOSE, WAIT, SL_MOVE, etc.)
  price_cache   — last known prices

Setup:
  1. Create a PostgreSQL database on Render (free tier)
  2. Set DATABASE_URL environment variable in Render dashboard
  3. On first run, tables are created automatically
"""

import os
import json
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


def get_conn():
    """Get a database connection. Raises clear error if DATABASE_URL not set."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable not set. "
            "Add your PostgreSQL URL in Render → Environment."
        )
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ══════════════════════════════════════════════
#  SETUP — create all tables on first run
# ══════════════════════════════════════════════

def setup_database():
    """
    Create all tables if they don't exist.
    Safe to call on every startup.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id           SERIAL PRIMARY KEY,
                    mode         VARCHAR(20)  DEFAULT 'paper',
                    balance      NUMERIC(12,2) DEFAULT 500.00,
                    equity       NUMERIC(12,2) DEFAULT 500.00,
                    updated_at   TIMESTAMP    DEFAULT NOW()
                );

                -- Insert default paper account if none exists
                INSERT INTO account (mode, balance, equity)
                SELECT 'paper', 500.00, 500.00
                WHERE NOT EXISTS (SELECT 1 FROM account WHERE id = 1);

                CREATE TABLE IF NOT EXISTS positions (
                    id           SERIAL PRIMARY KEY,
                    trade_id     VARCHAR(20)  UNIQUE,
                    symbol       VARCHAR(10),
                    side         VARCHAR(5),
                    entry_price  NUMERIC(12,2),
                    size         NUMERIC(12,6),
                    risk_amount  NUMERIC(10,2),
                    stop_loss    NUMERIC(12,2),
                    take_profit  NUMERIC(12,2),
                    rr           NUMERIC(5,2),
                    be_moved     BOOLEAN      DEFAULT FALSE,
                    trailing     BOOLEAN      DEFAULT FALSE,
                    opened_at    TIMESTAMP    DEFAULT NOW(),
                    status       VARCHAR(10)  DEFAULT 'OPEN'
                );

                CREATE TABLE IF NOT EXISTS trade_history (
                    id           SERIAL PRIMARY KEY,
                    trade_id     VARCHAR(20),
                    symbol       VARCHAR(10),
                    side         VARCHAR(5),
                    entry        NUMERIC(12,2),
                    exit_price   NUMERIC(12,2),
                    stop_loss    NUMERIC(12,2),
                    take_profit  NUMERIC(12,2),
                    size         NUMERIC(12,6),
                    risk         NUMERIC(10,2),
                    pl           NUMERIC(10,2),
                    new_balance  NUMERIC(12,2),
                    duration     VARCHAR(20),
                    exit_reason  VARCHAR(100),
                    confidence   INTEGER,
                    session      VARCHAR(20),
                    trend        VARCHAR(20),
                    structure    VARCHAR(20),
                    rsi          NUMERIC(6,2),
                    opened_at    TIMESTAMP,
                    closed_at    TIMESTAMP    DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS journal (
                    id           SERIAL PRIMARY KEY,
                    record_id    VARCHAR(20),
                    action       VARCHAR(20),
                    symbol       VARCHAR(10),
                    side         VARCHAR(5),
                    price        NUMERIC(12,2),
                    pl           NUMERIC(10,2),
                    reason       TEXT,
                    data         JSONB,
                    created_at   TIMESTAMP    DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS price_cache (
                    symbol       VARCHAR(10)  PRIMARY KEY,
                    price        NUMERIC(12,2),
                    updated_at   TIMESTAMP    DEFAULT NOW()
                );
            """)
        conn.commit()
    print("[DB] Tables ready")


# ══════════════════════════════════════════════
#  ACCOUNT — balance and equity
# ══════════════════════════════════════════════

def get_account() -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account WHERE id = 1")
            row = cur.fetchone()
            return dict(row) if row else {"balance": 500.00, "equity": 500.00, "mode": "paper"}


def update_balance(new_balance: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE account SET balance = %s, equity = %s, updated_at = NOW() WHERE id = 1",
                (round(new_balance, 2), round(new_balance, 2))
            )
        conn.commit()


def update_equity(equity: float):
    """Update equity only (floating P/L) — balance unchanged until trade closes."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE account SET equity = %s, updated_at = NOW() WHERE id = 1",
                (round(equity, 2),)
            )
        conn.commit()


def load_balance() -> float:
    return float(get_account()["balance"])


def save_balance(b: float):
    update_balance(b)


# ══════════════════════════════════════════════
#  POSITIONS — open trades
# ══════════════════════════════════════════════

def save_position(position: dict):
    """Insert or update an open position."""
    if not position:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO positions
                    (trade_id, symbol, side, entry_price, size, risk_amount,
                     stop_loss, take_profit, rr, be_moved, trailing, opened_at, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN')
                ON CONFLICT (trade_id) DO UPDATE SET
                    stop_loss  = EXCLUDED.stop_loss,
                    be_moved   = EXCLUDED.be_moved,
                    trailing   = EXCLUDED.trailing,
                    status     = EXCLUDED.status
            """, (
                position["trade_id"], position["symbol"], position["side"],
                position["entry_price"], position["size"], position["risk_amount"],
                position["stop_loss"], position["take_profit"], position["rr"],
                position.get("be_moved", False), position.get("trailing", False),
                position.get("opened_at", datetime.utcnow().isoformat())
            ))
        conn.commit()


def load_position() -> dict | None:
    """Return the current open position, or None."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM positions WHERE status = 'OPEN' LIMIT 1")
            row = cur.fetchone()
            if not row:
                return None
            p = dict(row)
            # Normalize types for the rest of the system
            p["entry_price"] = float(p["entry_price"])
            p["size"]        = float(p["size"])
            p["risk_amount"] = float(p["risk_amount"])
            p["stop_loss"]   = float(p["stop_loss"])
            p["take_profit"] = float(p["take_profit"])
            p["rr"]          = float(p["rr"])
            p["opened_at"]   = p["opened_at"].isoformat() if p["opened_at"] else ""
            return p


def close_position_in_db(trade_id: str):
    """Mark position as closed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE positions SET status = 'CLOSED' WHERE trade_id = %s",
                (trade_id,)
            )
        conn.commit()


def clear_position():
    """Mark all open positions as closed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE positions SET status = 'CLOSED' WHERE status = 'OPEN'")
        conn.commit()


# ══════════════════════════════════════════════
#  TRADE HISTORY — permanent record of every closed trade
# ══════════════════════════════════════════════

def save_closed_trade(trade: dict):
    """Insert a completed trade into permanent history."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_history
                    (trade_id, symbol, side, entry, exit_price, stop_loss, take_profit,
                     size, risk, pl, new_balance, duration, exit_reason,
                     confidence, session, trend, structure, rsi, opened_at, closed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """, (
                trade.get("trade_id",""), trade.get("symbol",""), trade.get("side",""),
                trade.get("entry",0), trade.get("exit",0),
                trade.get("stop_loss",0), trade.get("take_profit",0),
                trade.get("size",0), trade.get("risk",0),
                trade.get("pl",0), trade.get("new_balance",0),
                trade.get("duration",""), trade.get("exit_reason",""),
                trade.get("confidence",0), trade.get("session",""),
                trade.get("trend",""), trade.get("structure",""),
                trade.get("rsi",0), trade.get("opened_at", datetime.utcnow().isoformat())
            ))
        conn.commit()


def load_trade_history(limit: int = 50) -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM trade_history ORDER BY closed_at DESC LIMIT %s",
                (limit,)
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]


def load_closed_trades(days: int = 999) -> list:
    """Return closed trades for reports/recommendations."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM trade_history
                WHERE closed_at >= NOW() - INTERVAL '%s days'
                ORDER BY closed_at DESC
            """, (days,))
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["pl"]        = float(d.get("pl", 0))
                d["entry"]     = float(d.get("entry", 0))
                d["exit"]      = float(d.get("exit_price", 0))
                d["confidence"]= int(d.get("confidence", 0))
                d["closed_at"] = d["closed_at"].isoformat() if d["closed_at"] else ""
                result.append(d)
            return result


# ══════════════════════════════════════════════
#  JOURNAL — every action logged permanently
# ══════════════════════════════════════════════

def append_trade(entry: dict):
    """Log any action to the journal (OPEN, CLOSE, WAIT, SL_MOVE, etc.)."""
    import uuid
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO journal (record_id, action, symbol, side, price, pl, reason, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                str(uuid.uuid4())[:8],
                entry.get("action", ""),
                entry.get("symbol", ""),
                entry.get("side", entry.get("side_considered", "")),
                entry.get("price", entry.get("entry", 0)),
                entry.get("pl", 0),
                entry.get("reason", entry.get("exit_reason", "")),
                json.dumps(entry)
            ))
        conn.commit()


def load_journal() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT data FROM journal ORDER BY created_at DESC LIMIT 200")
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                result.append(d)
            return result


def load_recent(n: int = 50) -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT data, created_at FROM journal ORDER BY created_at DESC LIMIT %s", (n,))
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                result.append(d)
            return result


# ══════════════════════════════════════════════
#  PRICE CACHE
# ══════════════════════════════════════════════

def cache_price(symbol: str, price: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_cache (symbol, price, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (symbol) DO UPDATE SET price = EXCLUDED.price, updated_at = NOW()
            """, (symbol, price))
        conn.commit()


def get_cached_price(symbol: str) -> float | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price, updated_at FROM price_cache WHERE symbol = %s",
                (symbol,)
            )
            row = cur.fetchone()
            if row:
                # Only use cache if fresh (< 5 minutes old)
                age = (datetime.utcnow() - row[1]).total_seconds()
                if age < 300:
                    return float(row[0])
    return None
