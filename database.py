"""
database.py - PostgreSQL Persistent Storage
Handles missing columns gracefully with auto-migration.
"""
import os, json, uuid
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def setup_database():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Account table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id         SERIAL PRIMARY KEY,
                    mode       VARCHAR(20)   DEFAULT 'paper',
                    balance    NUMERIC(12,2) DEFAULT 500.00,
                    equity     NUMERIC(12,2) DEFAULT 500.00,
                    updated_at TIMESTAMP     DEFAULT NOW()
                );
                INSERT INTO account (mode, balance, equity)
                SELECT 'paper', 500.00, 500.00
                WHERE NOT EXISTS (SELECT 1 FROM account WHERE id = 1);
            """)

            # Positions table - create fresh
            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id             SERIAL PRIMARY KEY,
                    trade_id       VARCHAR(20)   UNIQUE,
                    symbol         VARCHAR(10),
                    side           VARCHAR(5),
                    entry_price    NUMERIC(12,2),
                    size           NUMERIC(12,6),
                    risk_amount    NUMERIC(10,2),
                    stop_loss      NUMERIC(12,2),
                    take_profit    NUMERIC(12,2),
                    rr             NUMERIC(5,2)  DEFAULT 1.0,
                    be_moved       BOOLEAN       DEFAULT FALSE,
                    trail_sl       BOOLEAN       DEFAULT FALSE,
                    partial_closed BOOLEAN       DEFAULT FALSE,
                    trade_mode     VARCHAR(10)   DEFAULT 'SCALPER',
                    atr_at_open    NUMERIC(12,2) DEFAULT 0,
                    opened_at      TIMESTAMP     DEFAULT NOW(),
                    status         VARCHAR(10)   DEFAULT 'OPEN'
                );
            """)

            # Auto-add missing columns to existing positions table
            missing_cols = [
                ("partial_closed", "BOOLEAN DEFAULT FALSE"),
                ("trade_mode",     "VARCHAR(10) DEFAULT 'SCALPER'"),
                ("atr_at_open",    "NUMERIC(12,2) DEFAULT 0"),
                ("trail_sl",       "BOOLEAN DEFAULT FALSE"),
                ("be_moved",       "BOOLEAN DEFAULT FALSE"),
                ("rr",             "NUMERIC(5,2) DEFAULT 1.0"),
            ]
            for col, definition in missing_cols:
                try:
                    cur.execute(f"ALTER TABLE positions ADD COLUMN IF NOT EXISTS {col} {definition}")
                except Exception:
                    pass

            # Trade history
            cur.execute("""
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
                    exit_reason  VARCHAR(200),
                    confidence   INTEGER       DEFAULT 0,
                    session      VARCHAR(20),
                    trend        VARCHAR(20),
                    structure    VARCHAR(20),
                    rsi          NUMERIC(6,2)  DEFAULT 0,
                    trade_mode   VARCHAR(10)   DEFAULT 'SCALPER',
                    opened_at    TIMESTAMP,
                    closed_at    TIMESTAMP     DEFAULT NOW()
                );
            """)

            # Journal
            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal (
                    id         SERIAL PRIMARY KEY,
                    record_id  VARCHAR(20),
                    action     VARCHAR(20),
                    symbol     VARCHAR(10),
                    side       VARCHAR(5),
                    price      NUMERIC(12,2) DEFAULT 0,
                    pl         NUMERIC(10,2) DEFAULT 0,
                    reason     TEXT,
                    data       JSONB,
                    created_at TIMESTAMP     DEFAULT NOW()
                );
            """)

            # Price cache
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_cache (
                    symbol     VARCHAR(10) PRIMARY KEY,
                    price      NUMERIC(12,2),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)

        conn.commit()
    print("[DB] Tables ready")


# ── Account ───────────────────────────────────────────────────────────────

def get_account() -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account WHERE id = 1")
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["balance"] = float(d.get("balance", 500.0))
                d["equity"]  = float(d.get("equity",  500.0))
                return d
    return {"balance": 500.0, "equity": 500.0, "mode": "paper"}

def load_balance() -> float:
    return float(get_account()["balance"])

def save_balance(b: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE account SET balance=%s, equity=%s, updated_at=NOW() WHERE id=1",
                (round(b,2), round(b,2)))
        conn.commit()

def update_equity(equity: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE account SET equity=%s, updated_at=NOW() WHERE id=1",
                (round(equity,2),))
        conn.commit()

def recalc_equity(prices: dict) -> float:
    balance   = load_balance()
    positions = get_open_positions()
    floating  = 0.0
    for pos in positions:
        sym   = pos["symbol"]
        price = prices.get(sym, pos["entry_price"])
        size  = pos["size"]
        fl    = (price - pos["entry_price"]) * size if pos["side"] == "BUY" \
                else (pos["entry_price"] - price) * size
        floating += fl
    equity = round(balance + floating, 2)
    update_equity(equity)
    return equity


# ── Positions ─────────────────────────────────────────────────────────────

def _normalize(p: dict) -> dict:
    for f in ["entry_price","size","risk_amount","stop_loss","take_profit","rr","atr_at_open"]:
        p[f] = float(p.get(f) or 0)
    p["be_moved"]       = bool(p.get("be_moved", False))
    p["trail_sl"]       = bool(p.get("trail_sl", False))
    p["partial_closed"] = bool(p.get("partial_closed", False))
    p["mode"]           = p.get("trade_mode", "SCALPER")
    p["trailing"]       = p.get("trail_sl", False)
    if p.get("opened_at") and not isinstance(p["opened_at"], str):
        p["opened_at"] = p["opened_at"].isoformat()
    return p

def save_position(position: dict):
    if not position:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO positions
                    (trade_id, symbol, side, entry_price, size, risk_amount,
                     stop_loss, take_profit, rr, be_moved, trail_sl,
                     partial_closed, trade_mode, atr_at_open, opened_at, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN')
                ON CONFLICT (trade_id) DO UPDATE SET
                    stop_loss       = EXCLUDED.stop_loss,
                    size            = EXCLUDED.size,
                    be_moved        = EXCLUDED.be_moved,
                    trail_sl        = EXCLUDED.trail_sl,
                    partial_closed  = EXCLUDED.partial_closed,
                    status          = EXCLUDED.status
            """, (
                position["trade_id"], position["symbol"], position["side"],
                position["entry_price"], position["size"],
                position.get("risk_amount", 0),
                position["stop_loss"], position["take_profit"],
                position.get("rr", 1.0),
                position.get("be_moved", False),
                position.get("trail_sl", False),
                position.get("partial_closed", False),
                position.get("mode", "SCALPER"),
                position.get("atr_at_open", 0),
                position.get("opened_at", datetime.utcnow().isoformat())
            ))
        conn.commit()

def load_position():
    positions = get_open_positions()
    return positions[0] if positions else None

def get_open_positions() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at ASC")
            return [_normalize(dict(r)) for r in cur.fetchall()]

def get_open_positions_count() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'")
            return cur.fetchone()[0]

def close_position_in_db(trade_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE positions SET status='CLOSED' WHERE trade_id=%s",
                (trade_id,))
        conn.commit()

def clear_position():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE positions SET status='CLOSED' WHERE status='OPEN'")
        conn.commit()


# ── Trade History ─────────────────────────────────────────────────────────

def save_closed_trade(trade: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_history
                    (trade_id, symbol, side, entry, exit_price, stop_loss,
                     take_profit, size, risk, pl, new_balance, duration,
                     exit_reason, confidence, session, trend, structure,
                     rsi, trade_mode, opened_at, closed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            """, (
                trade.get("trade_id",""), trade.get("symbol",""),
                trade.get("side",""), trade.get("entry",0),
                trade.get("exit",0), trade.get("stop_loss",0),
                trade.get("take_profit",0), trade.get("size",0),
                trade.get("risk",0), trade.get("pl",0),
                trade.get("new_balance",0), trade.get("duration",""),
                trade.get("exit_reason",""), trade.get("confidence",0),
                trade.get("session",""), trade.get("trend",""),
                trade.get("structure",""), trade.get("rsi",0),
                trade.get("mode","SCALPER"),
                trade.get("opened_at", datetime.utcnow().isoformat())
            ))
        conn.commit()

def load_closed_trades(days: int = 999) -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM trade_history
                WHERE closed_at >= NOW() - INTERVAL '%s days'
                ORDER BY closed_at DESC
            """ % int(days))
            result = []
            for r in cur.fetchall():
                d = dict(r)
                d["pl"]        = float(d.get("pl", 0))
                d["entry"]     = float(d.get("entry", 0))
                d["exit"]      = float(d.get("exit_price", 0))
                d["confidence"]= int(d.get("confidence", 0))
                d["closed_at"] = d["closed_at"].isoformat() if d.get("closed_at") else ""
                result.append(d)
            return result

def load_trade_history(limit: int = 50) -> list:
    return load_closed_trades(999)[:limit]


# ── Journal ───────────────────────────────────────────────────────────────

def append_trade(entry: dict):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO journal (record_id, action, symbol, side, price, pl, reason, data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                str(uuid.uuid4())[:8],
                entry.get("action",""),
                entry.get("symbol",""),
                entry.get("side", entry.get("side_considered","")),
                entry.get("price", entry.get("entry",0)) or 0,
                entry.get("pl",0) or 0,
                entry.get("reason", entry.get("exit_reason","")) or "",
                json.dumps(entry)
            ))
        conn.commit()

def load_journal() -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT data FROM journal ORDER BY created_at DESC LIMIT 200")
            result = []
            for r in cur.fetchall():
                d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                result.append(d)
            return result

def load_recent(n: int = 50) -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT data FROM journal ORDER BY created_at DESC LIMIT %s",
                (n,))
            result = []
            for r in cur.fetchall():
                d = r["data"]
                if isinstance(d, str):
                    d = json.loads(d)
                result.append(d)
            return result


# ── Price Cache ───────────────────────────────────────────────────────────

def cache_price(symbol: str, price: float):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_cache (symbol, price, updated_at)
                VALUES (%s,%s,NOW())
                ON CONFLICT (symbol) DO UPDATE
                SET price=EXCLUDED.price, updated_at=NOW()
            """, (symbol, price))
        conn.commit()

def get_cached_price(symbol: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price, updated_at FROM price_cache WHERE symbol=%s",
                (symbol,))
            row = cur.fetchone()
            if row:
                age = (datetime.utcnow() - row[1]).total_seconds()
                if age < 300:
                    return float(row[0])
    return None
