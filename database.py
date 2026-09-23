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
    # Works with both Supabase and Render PostgreSQL
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except Exception:
        return psycopg2.connect(DATABASE_URL)

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
                    risk_1r        NUMERIC(10,4) DEFAULT 0,
                    stop_loss      NUMERIC(12,2),
                    take_profit    NUMERIC(12,2),
                    rr             NUMERIC(5,2)  DEFAULT 1.0,
                    sl_dist        NUMERIC(12,4) DEFAULT 0,
                    be_moved       BOOLEAN       DEFAULT FALSE,
                    profit_locked  BOOLEAN       DEFAULT FALSE,
                    mfe            NUMERIC(10,4) DEFAULT 0,
                    mae            NUMERIC(10,4) DEFAULT 0,
                    mfe_r          NUMERIC(8,3)  DEFAULT 0,
                    mae_r          NUMERIC(8,3)  DEFAULT 0,
                    be_trigger_r   NUMERIC(8,3)  DEFAULT 0,
                    initial_size        NUMERIC(12,6) DEFAULT 0,
                    realized_pl         NUMERIC(10,4) DEFAULT 0,
                    planned_rr          NUMERIC(8,3)  DEFAULT 0,
                    planned_tp_r        NUMERIC(8,3)  DEFAULT 0,
                    planned_tp_dollars  NUMERIC(10,4) DEFAULT 0,
                    entry_timeframe     VARCHAR(10)   DEFAULT '',
                    entry_trend         VARCHAR(20)   DEFAULT '',
                    entry_structure     VARCHAR(50)   DEFAULT '',
                    entry_rsi           NUMERIC(6,2)  DEFAULT 0       DEFAULT FALSE,
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
                except Exception as e:
                    print(f"[DB] migration error: {e}")

            # Migrate positions table
            pos_cols = [
                ("risk_1r",      "NUMERIC(10,4) DEFAULT 0"),
                ("sl_dist",      "NUMERIC(12,4) DEFAULT 0"),
                ("profit_locked","BOOLEAN DEFAULT FALSE"),
                ("mfe",          "NUMERIC(10,4) DEFAULT 0"),
                ("mae",          "NUMERIC(10,4) DEFAULT 0"),
                ("mfe_r",        "NUMERIC(8,3)  DEFAULT 0"),
                ("mae_r",        "NUMERIC(8,3)  DEFAULT 0"),
                ("be_trigger_r", "NUMERIC(8,3)  DEFAULT 0"),
                ("initial_size", "NUMERIC(12,6) DEFAULT 0"),
                ("realized_pl",         "NUMERIC(10,4) DEFAULT 0"),
                ("planned_rr",          "NUMERIC(8,3) DEFAULT 0"),
                ("planned_tp_r",        "NUMERIC(8,3) DEFAULT 0"),
                ("planned_tp_dollars",  "NUMERIC(10,4) DEFAULT 0"),
                ("entry_timeframe",     "VARCHAR(10) DEFAULT ''"),
                ("entry_trend",         "VARCHAR(20) DEFAULT ''"),
                ("entry_structure",     "VARCHAR(50) DEFAULT ''"),
                ("entry_rsi",           "NUMERIC(6,2) DEFAULT 0"),
            ]
            for col, definition in pos_cols:
                try:
                    db.execute(f"ALTER TABLE positions ADD COLUMN IF NOT EXISTS {col} {definition}")
                except Exception as e:
                    print(f"[DB] positions migration {col}: {e}")

            # Migrate trade_history table
            history_cols = [
                ("exit_type",          "VARCHAR(20) DEFAULT 'UNKNOWN'"),
                ("risk_1r",            "NUMERIC(10,4) DEFAULT 0"),
                ("planned_rr",         "NUMERIC(8,3)  DEFAULT 0"),
                ("planned_tp_r",       "NUMERIC(8,3)  DEFAULT 0"),
                ("planned_tp_dollars", "NUMERIC(10,4) DEFAULT 0"),
                ("mfe",          "NUMERIC(10,4) DEFAULT 0"),
                ("mae",          "NUMERIC(10,4) DEFAULT 0"),
                ("mfe_r",        "NUMERIC(8,3) DEFAULT 0"),
                ("mae_r",        "NUMERIC(8,3) DEFAULT 0"),
                ("be_trigger_r", "NUMERIC(8,3) DEFAULT 0"),
                ("realized_r",   "NUMERIC(8,3) DEFAULT 0"),
            ]
            for col, definition in history_cols:
                try:
                    cur.execute(f"ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS {col} {definition}")
                except Exception as e:
                    print(f"[DB] history migration {col}: {e}")

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
                    exit_type    VARCHAR(20)   DEFAULT 'UNKNOWN',
                    risk_1r      NUMERIC(10,4) DEFAULT 0,
                    planned_rr          NUMERIC(8,3)  DEFAULT 0,
                    planned_tp_r        NUMERIC(8,3)  DEFAULT 0,
                    planned_tp_dollars  NUMERIC(10,4) DEFAULT 0,
                    mfe          NUMERIC(10,4) DEFAULT 0,
                    mae          NUMERIC(10,4) DEFAULT 0,
                    mfe_r        NUMERIC(8,3)  DEFAULT 0,
                    mae_r        NUMERIC(8,3)  DEFAULT 0,
                    be_trigger_r NUMERIC(8,3)  DEFAULT 0,
                    realized_r   NUMERIC(8,3)  DEFAULT 0,
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
    for f in ["entry_price","size","risk_amount","risk_1r","stop_loss",
               "take_profit","rr","atr_at_open","sl_dist",
               "mfe","mae","mfe_r","mae_r","be_trigger_r",
               "initial_size","realized_pl","planned_rr",
               "planned_tp_r","planned_tp_dollars","entry_rsi"]:
        p[f] = float(p.get(f) or 0)
    p["be_moved"]       = bool(p.get("be_moved", False))
    p["trail_sl"]       = bool(p.get("trail_sl", False))
    p["partial_closed"] = bool(p.get("partial_closed", False))
    p["profit_locked"]  = bool(p.get("profit_locked", False))
    p["mode"]           = p.get("trade_mode", "STRUCTURED")
    p["trailing"]       = p.get("trail_sl", False)
    if p.get("opened_at") and not isinstance(p["opened_at"], str):
        p["opened_at"] = p["opened_at"].isoformat()
    return p

def save_position(position: dict):
    """Save or update an open position including all analytics fields."""
    p = {**position}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO positions
                    (trade_id, symbol, side, entry_price, size,
                     risk_amount, risk_1r, stop_loss, take_profit, rr,
                     sl_dist, be_moved, profit_locked, partial_closed,
                     trail_sl, atr_at_open, opened_at, status,
                     confidence, trade_mode, sl_moved_at,
                     mfe, mae, mfe_r, mae_r, be_trigger_r,
                     initial_size, realized_pl,
                     planned_rr, planned_tp_r, planned_tp_dollars,
                     entry_timeframe, entry_trend, entry_structure, entry_rsi)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO UPDATE SET
                    stop_loss      = EXCLUDED.stop_loss,
                    size           = EXCLUDED.size,
                    risk_1r        = EXCLUDED.risk_1r,
                    sl_dist        = EXCLUDED.sl_dist,
                    be_moved       = EXCLUDED.be_moved,
                    profit_locked  = EXCLUDED.profit_locked,
                    partial_closed = EXCLUDED.partial_closed,
                    trail_sl       = EXCLUDED.trail_sl,
                    status         = EXCLUDED.status,
                    mfe            = EXCLUDED.mfe,
                    mae            = EXCLUDED.mae,
                    mfe_r          = EXCLUDED.mfe_r,
                    mae_r          = EXCLUDED.mae_r,
                    be_trigger_r   = EXCLUDED.be_trigger_r,
                    realized_pl    = EXCLUDED.realized_pl
            """, (
                p.get("trade_id",""),
                p.get("symbol",""),
                p.get("side",""),
                p.get("entry_price",0),
                p.get("size",0),
                p.get("risk_amount",0),
                p.get("risk_1r",0),
                p.get("stop_loss",0),
                p.get("take_profit",0),
                p.get("rr",0),
                p.get("sl_dist",0),
                p.get("be_moved",False),
                p.get("profit_locked",False),
                p.get("partial_closed",False),
                p.get("trail_sl",False),
                p.get("atr_at_open",0),
                p.get("opened_at",""),
                p.get("status","OPEN"),
                p.get("confidence",0),
                p.get("trade_mode","STRUCTURED"),
                p.get("sl_moved_at",""),
                p.get("mfe",0),
                p.get("mae",0),
                p.get("mfe_r",0),
                p.get("mae_r",0),
                p.get("be_trigger_r",0),
                p.get("initial_size", p.get("size",0)),
                p.get("realized_pl",0),
                p.get("planned_rr",0),
                p.get("planned_tp_r",0),
                p.get("planned_tp_dollars",0),
                p.get("entry_timeframe",""),
                p.get("entry_trend",""),
                p.get("entry_structure",""),
                p.get("entry_rsi",0),
            ))
        conn.commit()
def load_position():
    positions = get_open_positions()
    return positions[0] if positions else None

def get_open_positions() -> list:
    """Returns all open positions with all analytics fields."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        trade_id, symbol, side, entry_price, size,
                        risk_amount,
                        COALESCE(risk_1r, risk_amount, 0)   AS risk_1r,
                        stop_loss, take_profit,
                        COALESCE(rr, 0)                      AS rr,
                        COALESCE(sl_dist, 0)                 AS sl_dist,
                        COALESCE(be_moved, FALSE)            AS be_moved,
                        COALESCE(profit_locked, FALSE)       AS profit_locked,
                        COALESCE(partial_closed, FALSE)      AS partial_closed,
                        COALESCE(trail_sl, FALSE)            AS trail_sl,
                        COALESCE(atr_at_open, 0)             AS atr_at_open,
                        COALESCE(mfe, 0)                     AS mfe,
                        COALESCE(mae, 0)                     AS mae,
                        COALESCE(mfe_r, 0)                   AS mfe_r,
                        COALESCE(mae_r, 0)                   AS mae_r,
                        COALESCE(be_trigger_r, 0)            AS be_trigger_r,
                        COALESCE(initial_size, size, 0)      AS initial_size,
                        COALESCE(realized_pl, 0)             AS realized_pl,
                        COALESCE(planned_rr, rr, 0)          AS planned_rr,
                        COALESCE(planned_tp_r, rr, 0)        AS planned_tp_r,
                        COALESCE(planned_tp_dollars, 0)      AS planned_tp_dollars,
                        COALESCE(entry_timeframe, '')        AS entry_timeframe,
                        COALESCE(entry_trend, '')            AS entry_trend,
                        COALESCE(entry_structure, '')        AS entry_structure,
                        COALESCE(entry_rsi, 0)               AS entry_rsi,
                        COALESCE(confidence, 0)              AS confidence,
                        COALESCE(trade_mode, 'STRUCTURED')   AS trade_mode,
                        status, opened_at,
                        COALESCE(sl_moved_at, '')            AS sl_moved_at
                    FROM positions
                    WHERE status = 'OPEN'
                    ORDER BY opened_at ASC
                """)
                return [_normalize(dict(r)) for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB] get_open_positions error: {e}")
        return []

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
    """
    Save completed trade with ALL analytics fields.
    Fixed: now saves exit_type, risk_1r, mfe, mae, mfe_r, mae_r,
           be_trigger_r, realized_r so recommendations.py can use them.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trade_history
                    (trade_id, symbol, side, entry, exit_price, stop_loss,
                     take_profit, size, risk, risk_1r, pl, new_balance,
                     duration, exit_reason, exit_type,
                     mfe, mae, mfe_r, mae_r, be_trigger_r, realized_r,
                     confidence, session, trend, structure,
                     rsi, trade_mode, opened_at, closed_at)
                VALUES (
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,NOW()
                )
            """, (
                trade.get("trade_id",""),
                trade.get("symbol",""),
                trade.get("side",""),
                trade.get("entry",0),
                trade.get("exit",0),
                trade.get("stop_loss",0),
                trade.get("take_profit",0),
                trade.get("size",0),
                trade.get("risk",0),
                trade.get("risk_1r", trade.get("risk",0)),
                trade.get("pl",0),
                trade.get("new_balance",0),
                trade.get("duration",""),
                trade.get("exit_reason",""),
                trade.get("exit_type","UNKNOWN"),
                trade.get("mfe",0),
                trade.get("mae",0),
                trade.get("mfe_r",0),
                trade.get("mae_r",0),
                trade.get("be_trigger_r",0),
                trade.get("realized_r",0),
                trade.get("confidence",0),
                trade.get("session",""),
                trade.get("trend",""),
                trade.get("structure",""),
                trade.get("rsi",0),
                trade.get("mode","STRUCTURED"),
                trade.get("opened_at", datetime.utcnow().isoformat()),
                trade.get("planned_rr",         0),
                trade.get("planned_tp_r",        0),
                trade.get("planned_tp_dollars",  0),
            ))
        conn.commit()

def finalize_trade(trade_id: str, new_balance: float,
                   closed_trade: dict) -> bool:
    """
    Atomic transaction: update balance + insert trade_history + close position.
    All three operations succeed or all roll back.
    This prevents balance/history divergence on failures.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Update balance
                cur.execute(
                    "UPDATE account SET balance=%s, updated_at=NOW() WHERE id=1",
                    (new_balance,)
                )
                # 2. Insert trade_history
                cur.execute("""
                    INSERT INTO trade_history
                        (trade_id, symbol, side, entry, exit_price,
                         stop_loss, take_profit, size, risk, risk_1r,
                         pl, new_balance, duration, exit_reason, exit_type,
                         mfe, mae, mfe_r, mae_r, be_trigger_r, realized_r,
                         planned_rr, planned_tp_r, planned_tp_dollars,
                         confidence, session, trend, structure,
                         rsi, trade_mode, opened_at, closed_at)
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()
                    )
                    ON CONFLICT (trade_id) DO NOTHING
                """, (
                    closed_trade.get("trade_id",""),
                    closed_trade.get("symbol",""),
                    closed_trade.get("side",""),
                    closed_trade.get("entry",0),
                    closed_trade.get("exit",0),
                    closed_trade.get("stop_loss",0),
                    closed_trade.get("take_profit",0),
                    closed_trade.get("size",0),
                    closed_trade.get("risk",0),
                    closed_trade.get("risk_1r",0),
                    closed_trade.get("pl",0),
                    new_balance,
                    closed_trade.get("duration",""),
                    closed_trade.get("exit_reason",""),
                    closed_trade.get("exit_type","UNKNOWN"),
                    closed_trade.get("mfe",0),
                    closed_trade.get("mae",0),
                    closed_trade.get("mfe_r",0),
                    closed_trade.get("mae_r",0),
                    closed_trade.get("be_trigger_r",0),
                    closed_trade.get("realized_r",0),
                    closed_trade.get("planned_rr",0),
                    closed_trade.get("planned_tp_r",0),
                    closed_trade.get("planned_tp_dollars",0),
                    closed_trade.get("confidence",0),
                    closed_trade.get("session",""),
                    closed_trade.get("trend",""),
                    closed_trade.get("structure",""),
                    closed_trade.get("rsi",0),
                    closed_trade.get("mode","STRUCTURED"),
                    closed_trade.get("opened_at",""),
                ))
                # 3. Mark position closed
                cur.execute(
                    "UPDATE positions SET status='CLOSED' WHERE trade_id=%s",
                    (trade_id,)
                )
            conn.commit()  # All three succeed or none do
        return True
    except Exception as e:
        print(f"[DB] finalize_trade FAILED for {trade_id}: {e}")
        return False


def load_closed_trades_today() -> list:
    """Load all trades closed since midnight UTC today."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        trade_id, symbol, side, entry, exit_price,
                        COALESCE(risk_1r, risk, 0) as risk_1r,
                        pl, exit_reason,
                        COALESCE(exit_type, 'UNKNOWN') as exit_type,
                        COALESCE(realized_r, 0) as realized_r,
                        COALESCE(mfe_r, 0) as mfe_r,
                        COALESCE(mae_r, 0) as mae_r,
                        COALESCE(planned_tp_dollars, 0) as planned_tp_dollars,
                        COALESCE(rr, 0) as rr,
                        confidence, closed_at
                    FROM trade_history
                    WHERE closed_at >= CURRENT_DATE
                    ORDER BY closed_at DESC
                """)
                rows = cur.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d["pl"]         = float(d.get("pl",0) or 0)
                    d["risk_1r"]    = float(d.get("risk_1r",0) or 0)
                    d["realized_r"] = float(d.get("realized_r",0) or 0)
                    d["closed_at"]  = d["closed_at"].isoformat() if d.get("closed_at") else ""
                    result.append(d)
                return result
    except Exception as e:
        print(f"[DB] load_closed_trades_today error: {e}")
        # Fallback: load recent history and filter by today UTC
        try:
            from datetime import date
            today = date.today().isoformat()
            all_trades = load_closed_trades(200)
            return [t for t in all_trades
                    if str(t.get("closed_at","")).startswith(today)]
        except Exception:
            return []


def load_closed_trades(days: int = 999) -> list:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM trade_history "
                "WHERE closed_at >= NOW() - INTERVAL %s "
                "ORDER BY closed_at DESC",
                (f"{int(days)} days",)
            )
            result = []
            for r in cur.fetchall():
                d = dict(r)
                # Core fields
                d["pl"]            = float(d.get("pl", 0) or 0)
                d["entry"]         = float(d.get("entry", 0) or 0)
                d["exit"]          = float(d.get("exit_price", 0) or 0)
                d["confidence"]    = int(d.get("confidence", 0) or 0)
                d["rr"]            = float(d.get("rr", 0) or 0)
                # Analytics fields - use COALESCE from DB, cast here
                d["risk_1r"]       = float(d.get("risk_1r") or d.get("risk") or 0)
                d["exit_type"]     = str(d.get("exit_type") or "UNKNOWN")
                d["mfe"]           = float(d.get("mfe") or 0)
                d["mae"]           = float(d.get("mae") or 0)
                d["mfe_r"]         = float(d.get("mfe_r") or 0)
                d["mae_r"]         = float(d.get("mae_r") or 0)
                d["be_trigger_r"]  = float(d.get("be_trigger_r") or 0)
                d["realized_r"]    = float(d.get("realized_r") or 0)
                d["closed_at"]     = d["closed_at"].isoformat() if d.get("closed_at") else ""
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
