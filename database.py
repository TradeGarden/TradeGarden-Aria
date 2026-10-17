"""
database.py — Aria source of truth
===================================
Production-grade PostgreSQL persistence.
All financial operations are atomic. No fake defaults.
"""

import json
import uuid
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL

PRICE = Decimal("0.00001")
SIZE  = Decimal("0.00000001")
MONEY = Decimal("0.01")
RATIO = Decimal("0.0001")


def _d(v, places=MONEY) -> Decimal:
    if v is None or v == "":
        raise ValueError(f"_d() received {v!r}")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ValueError(f"_d() cannot convert {v!r}")
    if not d.is_finite():
        raise ValueError(f"_d() non-finite: {v!r}")
    return d.quantize(places, rounding=ROUND_HALF_UP)


def _d0(v, places=MONEY) -> Decimal:
    try:
        return _d(v, places)
    except (ValueError, TypeError):
        return Decimal("0").quantize(places, rounding=ROUND_HALF_UP)


def _bool(v) -> bool:
    if isinstance(v, bool): return v
    if v is None: return False
    if isinstance(v, (int, float, Decimal)): return v != 0
    s = str(v).strip().lower()
    if s in ("true","1","yes","y","on"): return True
    if s in ("false","0","no","n","off",""): return False
    raise ValueError(f"_bool() cannot parse {v!r}")


def _now_utc():
    return datetime.now(timezone.utc)


def get_conn():
    conn = None
    try:
        try:
            conn = psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)
        except psycopg2.OperationalError:
            if "localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL:
                conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            else:
                raise
        with conn.cursor() as cur:
            cur.execute("SET TIME ZONE 'UTC'")
        return conn
    except Exception:
        if conn:
            conn.close()
        raise


# ── Schema ─────────────────────────────────────────────────────────────────

def setup_database():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account (
                    id         INTEGER PRIMARY KEY DEFAULT 1 CHECK(id=1),
                    balance    NUMERIC(14,2) NOT NULL DEFAULT 500.00,
                    updated_at TIMESTAMPTZ   NOT NULL DEFAULT NOW()
                )""")
            cur.execute("SELECT COUNT(*) FROM account WHERE id=1")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO account(id,balance) VALUES(1,500.00)")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id                 BIGSERIAL PRIMARY KEY,
                    trade_id           TEXT          NOT NULL UNIQUE,
                    symbol             TEXT          NOT NULL,
                    side               TEXT          NOT NULL CHECK(side IN('BUY','SELL')),
                    entry_price        NUMERIC(14,5) NOT NULL,
                    size               NUMERIC(16,8) NOT NULL,
                    initial_size       NUMERIC(16,8) NOT NULL DEFAULT 0,
                    risk_amount        NUMERIC(10,2) NOT NULL DEFAULT 0,
                    risk_1r            NUMERIC(10,2) NOT NULL DEFAULT 0,
                    stop_loss          NUMERIC(14,5) NOT NULL,
                    take_profit        NUMERIC(14,5) NOT NULL,
                    rr                 NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    sl_dist            NUMERIC(14,5) NOT NULL DEFAULT 0,
                    be_moved           BOOLEAN       NOT NULL DEFAULT FALSE,
                    profit_locked      BOOLEAN       NOT NULL DEFAULT FALSE,
                    trail_sl           BOOLEAN       NOT NULL DEFAULT FALSE,
                    partial_closed     BOOLEAN       NOT NULL DEFAULT FALSE,
                    partial_seq        INTEGER       NOT NULL DEFAULT 0,
                    mfe                NUMERIC(10,2) NOT NULL DEFAULT 0,
                    mae                NUMERIC(10,2) NOT NULL DEFAULT 0,
                    mfe_r              NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    mae_r              NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    be_trigger_r       NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    realized_pl        NUMERIC(10,2) NOT NULL DEFAULT 0,
                    planned_rr         NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    planned_tp_r       NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    planned_tp_dollars NUMERIC(10,2) NOT NULL DEFAULT 0,
                    entry_timeframe    TEXT          NOT NULL DEFAULT '',
                    entry_trend        TEXT          NOT NULL DEFAULT '',
                    entry_structure    TEXT          NOT NULL DEFAULT '',
                    entry_rsi          NUMERIC(6,2)  NOT NULL DEFAULT 0,
                    confidence         INTEGER       NOT NULL DEFAULT 0,
                    trade_mode         TEXT          NOT NULL DEFAULT 'STRUCTURED',
                    atr_at_open        NUMERIC(14,5) NOT NULL DEFAULT 0,
                    sl_moved_at        TIMESTAMPTZ,
                    opened_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
                    status             TEXT          NOT NULL DEFAULT 'OPEN'
                        CHECK(status IN('OPEN','CLOSED'))
                )""")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS trade_history (
                    id                 BIGSERIAL PRIMARY KEY,
                    trade_id           TEXT          NOT NULL UNIQUE,
                    symbol             TEXT          NOT NULL,
                    side               TEXT          NOT NULL,
                    entry              NUMERIC(14,5) NOT NULL,
                    exit_price         NUMERIC(14,5) NOT NULL,
                    stop_loss          NUMERIC(14,5) NOT NULL DEFAULT 0,
                    take_profit        NUMERIC(14,5) NOT NULL DEFAULT 0,
                    size               NUMERIC(16,8) NOT NULL DEFAULT 0,
                    risk               NUMERIC(10,2) NOT NULL DEFAULT 0,
                    risk_1r            NUMERIC(10,2) NOT NULL DEFAULT 0,
                    pl                 NUMERIC(10,2) NOT NULL,
                    total_pl           NUMERIC(10,2) NOT NULL DEFAULT 0,
                    new_balance        NUMERIC(14,2) NOT NULL,
                    duration           TEXT          NOT NULL DEFAULT '',
                    exit_reason        TEXT          NOT NULL DEFAULT '',
                    exit_type          TEXT          NOT NULL DEFAULT 'UNKNOWN',
                    mfe                NUMERIC(10,2) NOT NULL DEFAULT 0,
                    mae                NUMERIC(10,2) NOT NULL DEFAULT 0,
                    mfe_r              NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    mae_r              NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    be_trigger_r       NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    realized_r         NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    planned_rr         NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    planned_tp_r       NUMERIC(8,4)  NOT NULL DEFAULT 0,
                    planned_tp_dollars NUMERIC(10,2) NOT NULL DEFAULT 0,
                    confidence         INTEGER       NOT NULL DEFAULT 0,
                    session            TEXT          NOT NULL DEFAULT '',
                    trend              TEXT          NOT NULL DEFAULT '',
                    structure          TEXT          NOT NULL DEFAULT '',
                    rsi                NUMERIC(6,2)  NOT NULL DEFAULT 0,
                    trade_mode         TEXT          NOT NULL DEFAULT 'STRUCTURED',
                    opened_at          TIMESTAMPTZ,
                    closed_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
                )""")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS partial_close_events (
                    id          BIGSERIAL PRIMARY KEY,
                    event_id    TEXT          NOT NULL UNIQUE,
                    trade_id    TEXT          NOT NULL,
                    pl          NUMERIC(10,2) NOT NULL,
                    new_size    NUMERIC(16,8) NOT NULL,
                    realized_pl NUMERIC(10,2) NOT NULL,
                    new_balance NUMERIC(14,2) NOT NULL,
                    ts          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
                )""")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS journal (
                    id        BIGSERIAL   PRIMARY KEY,
                    record_id TEXT        NOT NULL DEFAULT '',
                    action    TEXT        NOT NULL DEFAULT '',
                    symbol    TEXT        NOT NULL DEFAULT '',
                    side      TEXT        NOT NULL DEFAULT '',
                    price     NUMERIC(14,5),
                    pl        NUMERIC(10,2),
                    reason    TEXT        NOT NULL DEFAULT '',
                    data      TEXT        NOT NULL DEFAULT '{}',
                    ts        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )""")

            for stmt in [
                "CREATE INDEX IF NOT EXISTS idx_pos_status  ON positions(status)",
                "CREATE INDEX IF NOT EXISTS idx_pos_sym     ON positions(symbol,status)",
                "CREATE INDEX IF NOT EXISTS idx_hist_closed ON trade_history(closed_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_pce_trade   ON partial_close_events(trade_id)",
                "CREATE INDEX IF NOT EXISTS idx_jnl_ts ON journal(ts DESC)",  # may fail on old schema — handled below
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_per_symbol
                   ON positions(symbol) WHERE status='OPEN'""",
            ]:
                cur.execute(stmt)

            # Migrations
            # Ensure journal has 'ts' column (old DBs may use 'created_at')
            try:
                cur.execute("SELECT ts FROM journal LIMIT 0")
            except Exception:
                try:
                    cur.execute("ALTER TABLE journal ADD COLUMN IF NOT EXISTS ts TIMESTAMPTZ DEFAULT NOW()")
                    print("[DB] migrated journal.ts")
                except Exception as _e2:
                    print(f"[DB] journal.ts migration warning: {_e2}")

            _migrate(cur, "positions", [
                ("initial_size",       "NUMERIC(16,8) NOT NULL DEFAULT 0"),
                ("risk_1r",            "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("sl_dist",            "NUMERIC(14,5) NOT NULL DEFAULT 0"),
                ("profit_locked",      "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("trail_sl",           "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("partial_seq",        "INTEGER NOT NULL DEFAULT 0"),
                ("mfe",                "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("mae",                "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("mfe_r",              "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("mae_r",              "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("be_trigger_r",       "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("realized_pl",        "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("planned_rr",         "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("planned_tp_r",       "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("planned_tp_dollars", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("entry_timeframe",    "TEXT NOT NULL DEFAULT ''"),
                ("entry_trend",        "TEXT NOT NULL DEFAULT ''"),
                ("entry_structure",    "TEXT NOT NULL DEFAULT ''"),
                ("entry_rsi",          "NUMERIC(6,2) NOT NULL DEFAULT 0"),
                ("confidence",         "INTEGER NOT NULL DEFAULT 0"),
                ("trade_mode",         "TEXT NOT NULL DEFAULT 'STRUCTURED'"),
                ("atr_at_open",        "NUMERIC(14,5) NOT NULL DEFAULT 0"),
                ("sl_moved_at",        "TIMESTAMPTZ"),
            ])
            _migrate(cur, "trade_history", [
                ("exit_type",          "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
                ("risk_1r",            "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("total_pl",           "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("mfe",                "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("mae",                "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("mfe_r",              "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("mae_r",              "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("be_trigger_r",       "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("realized_r",         "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("planned_rr",         "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("planned_tp_r",       "NUMERIC(8,4) NOT NULL DEFAULT 0"),
                ("planned_tp_dollars", "NUMERIC(10,2) NOT NULL DEFAULT 0"),
                ("trade_mode",         "TEXT NOT NULL DEFAULT 'STRUCTURED'"),
            ])
        conn.commit()
        print("[DB] Schema ready")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(cur, table, columns):
    for col, defn in columns:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, col))
        if not cur.fetchone():
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            print(f"[DB] migrated {table}.{col}")


# ── Account ────────────────────────────────────────────────────────────────

def load_balance() -> Decimal:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM account WHERE id=1")
            row = cur.fetchone()
            if row is None: raise RuntimeError("Account row missing.")
            return _d(row[0], MONEY)
    finally:
        conn.close()


def get_account() -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account WHERE id=1")
            row = cur.fetchone()
            if row is None: raise RuntimeError("Account row missing.")
            d = dict(row)
            d["balance"] = float(_d(d["balance"], MONEY))
            return d
    finally:
        conn.close()


def save_balance(balance: float):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE account SET balance=%s, updated_at=NOW() WHERE id=1",
                (float(_d(balance, MONEY)),))
        conn.commit()
    finally:
        conn.close()


# ── Positions ──────────────────────────────────────────────────────────────

_PROTECTED = {"status","size","realized_pl","partial_seq","partial_closed"}
_MILESTONE_OK = {
    "stop_loss","take_profit","be_moved","profit_locked","trail_sl",
    "mfe","mae","mfe_r","mae_r","be_trigger_r","sl_moved_at",
    "confidence","rr","sl_dist","planned_rr","planned_tp_r",
    "planned_tp_dollars","entry_timeframe","entry_trend",
    "entry_structure","entry_rsi","atr_at_open","trade_mode",
}


def create_position(position: dict):
    tid  = str(position.get("trade_id","")).strip()
    sym  = str(position.get("symbol","")).strip().upper()
    side = str(position.get("side","")).strip().upper()
    if not tid:  raise ValueError("create_position: trade_id empty.")
    if not sym:  raise ValueError("create_position: symbol empty.")
    if side not in ("BUY","SELL"): raise ValueError(f"create_position: invalid side {side!r}.")
    sz = _d0(position["size"], SIZE)
    if sz <= Decimal("0"): raise ValueError(f"create_position: size must be > 0.")
    init_sz = _d0(position.get("initial_size", position["size"]), SIZE)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO positions (
                    trade_id, symbol, side, entry_price, size, initial_size,
                    risk_amount, risk_1r, stop_loss, take_profit, rr, sl_dist,
                    be_moved, profit_locked, trail_sl, partial_closed, partial_seq,
                    mfe, mae, mfe_r, mae_r, be_trigger_r, realized_pl,
                    planned_rr, planned_tp_r, planned_tp_dollars,
                    entry_timeframe, entry_trend, entry_structure, entry_rsi,
                    confidence, trade_mode, atr_at_open, sl_moved_at, opened_at, status
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
            """, (
                tid, sym, side,
                _d0(position["entry_price"], PRICE),
                sz, init_sz,
                _d0(position.get("risk_amount",0), MONEY),
                _d0(position.get("risk_1r",0), MONEY),
                _d0(position["stop_loss"],  PRICE),
                _d0(position["take_profit"],PRICE),
                _d0(position.get("rr",0), RATIO),
                _d0(position.get("sl_dist",0), PRICE),
                _bool(position.get("be_moved",     False)),
                _bool(position.get("profit_locked", False)),
                _bool(position.get("trail_sl",      False)),
                _bool(position.get("partial_closed",False)),
                0,
                _d0(position.get("mfe",0), MONEY),
                _d0(position.get("mae",0), MONEY),
                _d0(position.get("mfe_r",0), RATIO),
                _d0(position.get("mae_r",0), RATIO),
                _d0(position.get("be_trigger_r",0), RATIO),
                _d0(position.get("realized_pl",0), MONEY),
                _d0(position.get("planned_rr",0), RATIO),
                _d0(position.get("planned_tp_r",0), RATIO),
                _d0(position.get("planned_tp_dollars",0), MONEY),
                str(position.get("entry_timeframe","")),
                str(position.get("entry_trend","")),
                str(position.get("entry_structure","")),
                _d0(position.get("entry_rsi",0), MONEY),
                int(position.get("confidence",0)),
                str(position.get("trade_mode","STRUCTURED")),
                _d0(position.get("atr_at_open",0), PRICE),
                position.get("sl_moved_at") or None,
                position.get("opened_at") or _now_utc(),
                "OPEN",
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_position_state(trade_id: str, updates: dict):
    tid = str(trade_id).strip()
    if not tid: raise ValueError("update_position_state: trade_id empty.")
    bad = {k for k in updates if k in _PROTECTED}
    if bad: raise ValueError(f"Protected fields rejected: {bad}")
    filtered = {k:v for k,v in updates.items() if k in _MILESTONE_OK}
    if not filtered: return

    set_parts, values = [], []
    for k,v in filtered.items():
        set_parts.append(f"{k} = %s")
        if k in ("be_moved","profit_locked","trail_sl"):
            values.append(_bool(v))
        elif k == "sl_moved_at":
            values.append(v if v else None)
        elif k in ("stop_loss","take_profit","sl_dist","atr_at_open"):
            values.append(_d0(v, PRICE))
        elif k in ("mfe","mae","planned_rr","planned_tp_r","planned_tp_dollars","entry_rsi"):
            values.append(_d0(v, MONEY))
        elif k in ("mfe_r","mae_r","be_trigger_r","rr"):
            values.append(_d0(v, RATIO))
        elif k == "confidence":
            values.append(int(v or 0))
        else:
            values.append(str(v or ""))
    values.append(tid)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE positions SET {', '.join(set_parts)} "
                f"WHERE trade_id=%s AND status='OPEN'",
                values)
            if cur.rowcount == 0:
                raise RuntimeError(f"{tid} is not OPEN or does not exist.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_position(position: dict):
    tid = str(position.get("trade_id","")).strip()
    if not tid: return
    safe = {k:v for k,v in position.items() if k in _MILESTONE_OK}
    if not safe: return
    try:
        update_position_state(tid, safe)
    except RuntimeError as e:
        if "not OPEN or does not exist" in str(e): return
        raise


def get_open_positions() -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at ASC")
            return [_cast_pos(dict(r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_open_positions_count() -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'")
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def load_position(trade_id: str) -> dict:
    tid = str(trade_id).strip()
    if not tid: raise ValueError("load_position: trade_id empty.")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM positions WHERE trade_id=%s", (tid,))
            row = cur.fetchone()
            if row is None: raise RuntimeError(f"Position {tid} not found.")
            return _cast_pos(dict(row))
    finally:
        conn.close()


def close_position_in_db(trade_id: str):
    raise RuntimeError(f"close_position_in_db() disabled. Use finalize_trade() for {trade_id}.")


def _cast_pos(d: dict) -> dict:
    for f in ("entry_price","stop_loss","take_profit","sl_dist","atr_at_open"):
        d[f] = float(_d0(d.get(f,0), PRICE))
    for f in ("size","initial_size"):
        d[f] = float(_d0(d.get(f,0), SIZE))
    for f in ("risk_amount","risk_1r","mfe","mae","realized_pl",
               "planned_tp_dollars","entry_rsi"):
        d[f] = float(_d0(d.get(f,0), MONEY))
    for f in ("rr","mfe_r","mae_r","be_trigger_r","planned_rr","planned_tp_r"):
        d[f] = float(_d0(d.get(f,0), RATIO))
    d["confidence"]     = int(d.get("confidence") or 0)
    d["partial_seq"]    = int(d.get("partial_seq") or 0)
    for f in ("be_moved","profit_locked","trail_sl","partial_closed"):
        d[f] = _bool(d.get(f, False))
    d["trade_mode"] = str(d.get("trade_mode","STRUCTURED"))
    d["mode"]       = d["trade_mode"]
    d["trailing"]   = d["trail_sl"]
    for ts in ("sl_moved_at","opened_at"):
        if d.get(ts) and not isinstance(d[ts], str):
            d[ts] = d[ts].isoformat()
    return d


# ── Atomic: finalize_trade ─────────────────────────────────────────────────

def finalize_trade(trade_id: str, final_leg_pl: float,
                   total_pl: float, closed_trade: dict) -> tuple:
    tid     = str(trade_id).strip()
    if not tid: raise ValueError("finalize_trade: trade_id empty.")
    leg_dec = _d(final_leg_pl, MONEY)
    tot_dec = _d(total_pl,     MONEY)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM account WHERE id=1 FOR UPDATE")
            acc = cur.fetchone()
            if acc is None: raise RuntimeError("Account row missing.")
            new_balance = _d(acc[0], MONEY) + leg_dec

            cur.execute(
                "SELECT pl, total_pl, new_balance FROM trade_history WHERE trade_id=%s",
                (tid,))
            hist = cur.fetchone()

            cur.execute(
                "SELECT status, realized_pl FROM positions WHERE trade_id=%s FOR UPDATE",
                (tid,))
            pos = cur.fetchone()

            if hist is not None:
                if pos is None:
                    raise RuntimeError(f"History exists for {tid} but position row missing.")
                if pos[0] == "OPEN":
                    raise RuntimeError(f"History exists for {tid} but position still OPEN.")
                stored_pl  = _d0(hist[0], MONEY)
                stored_tot = _d0(hist[1], MONEY)
                stored_bal = _d0(hist[2], MONEY)
                if abs(stored_pl  - leg_dec) > MONEY:
                    raise RuntimeError(f"Retry mismatch {tid}: stored pl={stored_pl} vs {leg_dec}")
                if abs(stored_tot - tot_dec) > MONEY:
                    raise RuntimeError(f"Retry mismatch {tid}: stored total={stored_tot} vs {tot_dec}")
                return True, float(stored_bal)

            if pos is None: raise RuntimeError(f"Position {tid} not found.")
            if pos[0] != "OPEN": raise RuntimeError(f"Position {tid} is {pos[0]}.")

            prior = _d0(pos[1], MONEY)
            expected = prior + leg_dec
            if abs(tot_dec - expected) > MONEY:
                raise RuntimeError(
                    f"Accounting invariant violated for {tid}: "
                    f"{prior} + {leg_dec} = {expected} ≠ {tot_dec}")

            cur.execute("""
                INSERT INTO trade_history (
                    trade_id, symbol, side, entry, exit_price,
                    stop_loss, take_profit, size, risk, risk_1r,
                    pl, total_pl, new_balance, duration,
                    exit_reason, exit_type, mfe, mae, mfe_r, mae_r,
                    be_trigger_r, realized_r, planned_rr, planned_tp_r,
                    planned_tp_dollars, confidence, session, trend,
                    structure, rsi, trade_mode, opened_at, closed_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,NOW()
                )
            """, (
                tid,
                str(closed_trade.get("symbol","")),
                str(closed_trade.get("side","")),
                _d0(closed_trade.get("entry",0),        PRICE),
                _d0(closed_trade.get("exit",0),         PRICE),
                _d0(closed_trade.get("stop_loss",0),    PRICE),
                _d0(closed_trade.get("take_profit",0),  PRICE),
                _d0(closed_trade.get("size",0),         SIZE),
                _d0(closed_trade.get("risk",0),         MONEY),
                _d0(closed_trade.get("risk_1r",0),      MONEY),
                leg_dec, tot_dec, new_balance,
                str(closed_trade.get("duration","")),
                str(closed_trade.get("exit_reason","")),
                str(closed_trade.get("exit_type","UNKNOWN")),
                _d0(closed_trade.get("mfe",0),          MONEY),
                _d0(closed_trade.get("mae",0),          MONEY),
                _d0(closed_trade.get("mfe_r",0),        RATIO),
                _d0(closed_trade.get("mae_r",0),        RATIO),
                _d0(closed_trade.get("be_trigger_r",0), RATIO),
                _d0(closed_trade.get("realized_r",0),   RATIO),
                _d0(closed_trade.get("planned_rr",0),   RATIO),
                _d0(closed_trade.get("planned_tp_r",0), RATIO),
                _d0(closed_trade.get("planned_tp_dollars",0), MONEY),
                int(closed_trade.get("confidence",0)),
                str(closed_trade.get("session","")),
                str(closed_trade.get("trend","")),
                str(closed_trade.get("structure","")),
                _d0(closed_trade.get("rsi",0), MONEY),
                str(closed_trade.get("mode","STRUCTURED")),
                closed_trade.get("opened_at") or None,
            ))

            cur.execute(
                "UPDATE positions SET status='CLOSED' WHERE trade_id=%s AND status='OPEN'",
                (tid,))
            if cur.rowcount != 1:
                raise RuntimeError(f"Failed to mark {tid} CLOSED.")

            cur.execute(
                "UPDATE account SET balance=%s, updated_at=NOW() WHERE id=1",
                (new_balance,))

            # Journal — atomic with close
            cur.execute(
                "INSERT INTO journal(record_id,action,symbol,side,price,pl,reason,data) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4())[:8], "CLOSE",
                 str(closed_trade.get("symbol","")),
                 str(closed_trade.get("side","")),
                 float(_d0(closed_trade.get("exit",0), PRICE)) or None,
                 float(leg_dec),
                 str(closed_trade.get("exit_reason","")),
                 json.dumps({"trade_id":tid,"total_pl":float(tot_dec)}, default=str)))

        conn.commit()
        return True, float(new_balance)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Atomic: finalize_partial_close ────────────────────────────────────────

def finalize_partial_close(trade_id: str, event_id: str,
                           pl: float, new_size: float,
                           realized_pl: float, position: dict,
                           journal_event: dict = None) -> tuple:
    tid          = str(trade_id).strip()
    eid          = str(event_id).strip()
    if not tid:  raise ValueError("finalize_partial_close: trade_id empty.")
    if not eid:  raise ValueError("finalize_partial_close: event_id empty.")
    pl_dec       = _d(pl,          MONEY)
    new_size_dec = _d(new_size,    SIZE)
    realized_dec = _d(realized_pl, MONEY)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Deduplication check
            cur.execute(
                "SELECT trade_id, pl, new_size, realized_pl, new_balance "
                "FROM partial_close_events WHERE event_id=%s", (eid,))
            dup = cur.fetchone()
            if dup is not None:
                if dup[0] != tid:
                    raise RuntimeError(f"Event {eid} belongs to {dup[0]}, not {tid}.")
                if abs(_d0(dup[1], MONEY) - pl_dec)       > MONEY:
                    raise RuntimeError(f"Event {eid} pl mismatch.")
                if abs(_d0(dup[2], SIZE)  - new_size_dec) > SIZE:
                    raise RuntimeError(f"Event {eid} size mismatch.")
                if abs(_d0(dup[3], MONEY) - realized_dec) > MONEY:
                    raise RuntimeError(f"Event {eid} realized_pl mismatch.")
                return True, float(_d0(dup[4], MONEY))

            cur.execute("SELECT balance FROM account WHERE id=1 FOR UPDATE")
            acc = cur.fetchone()
            if acc is None: raise RuntimeError("Account row missing.")
            new_balance = _d(acc[0], MONEY) + pl_dec

            cur.execute(
                "SELECT status, size, realized_pl FROM positions "
                "WHERE trade_id=%s FOR UPDATE", (tid,))
            pos = cur.fetchone()
            if pos is None: raise RuntimeError(f"Position {tid} not found.")
            if pos[0] != "OPEN": raise RuntimeError(f"Position {tid} is {pos[0]}.")

            old_size_dec = _d0(pos[1], SIZE)
            old_realized = _d0(pos[2], MONEY)
            if new_size_dec >= old_size_dec or new_size_dec <= Decimal("0"):
                raise RuntimeError(f"Invalid size: {old_size_dec} → {new_size_dec}")
            expected_realized = old_realized + pl_dec
            if abs(realized_dec - expected_realized) > MONEY:
                raise RuntimeError(
                    f"realized_pl mismatch: {old_realized}+{pl_dec}={expected_realized} ≠ {realized_dec}")

            cur.execute("""
                INSERT INTO partial_close_events
                    (event_id, trade_id, pl, new_size, realized_pl, new_balance)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (eid, tid, pl_dec, new_size_dec, realized_dec, new_balance))

            cur.execute("""
                UPDATE positions SET
                    size=% s, realized_pl=%s, partial_closed=TRUE,
                    partial_seq=partial_seq+1,
                    stop_loss=%s, take_profit=%s,
                    be_moved=%s, profit_locked=%s, trail_sl=%s,
                    sl_moved_at=%s,
                    mfe=%s, mae=%s, mfe_r=%s, mae_r=%s, be_trigger_r=%s
                WHERE trade_id=%s AND status='OPEN'
            """.replace("% s","%s"), (
                new_size_dec, realized_dec,
                _d0(position.get("stop_loss",0),  PRICE),
                _d0(position.get("take_profit",0), PRICE),
                _bool(position.get("be_moved",     False)),
                _bool(position.get("profit_locked", False)),
                _bool(position.get("trail_sl",      False)),
                position.get("sl_moved_at") or None,
                _d0(position.get("mfe",0),  MONEY),
                _d0(position.get("mae",0),  MONEY),
                _d0(position.get("mfe_r",0),RATIO),
                _d0(position.get("mae_r",0),RATIO),
                _d0(position.get("be_trigger_r",0), RATIO),
                tid,
            ))
            if cur.rowcount != 1:
                raise RuntimeError(f"Position UPDATE matched 0 rows for {tid}.")

            cur.execute(
                "UPDATE account SET balance=%s, updated_at=NOW() WHERE id=1",
                (new_balance,))

            evt = journal_event or {}
            cur.execute(
                "INSERT INTO journal(record_id,action,symbol,side,price,pl,reason,data) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4())[:8],
                 str(evt.get("action","PARTIAL_TP")),
                 str(evt.get("symbol","")),
                 str(evt.get("side","")),
                 _d0(evt.get("exit",0), PRICE) or None,
                 pl_dec,
                 str(evt.get("reason","")),
                 json.dumps(evt, default=str)))

        conn.commit()
        return True, float(new_balance)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Journal ────────────────────────────────────────────────────────────────

def append_trade(event: dict):
    try:
        price_v = event.get("exit_price", event.get("entry_price",
                  event.get("exit", event.get("entry"))))
        pl_v = event.get("pl")
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO journal(record_id,action,symbol,side,price,pl,reason,data) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(uuid.uuid4())[:8],
                     str(event.get("action","")),
                     str(event.get("symbol","")),
                     str(event.get("side","")),
                     _d0(price_v, PRICE) if price_v is not None else None,
                     _d0(pl_v,    MONEY) if pl_v    is not None else None,
                     str(event.get("exit_reason", event.get("reason",""))),
                     json.dumps(event, default=str)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[DB] append_trade warning: {e}")


def load_journal() -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM journal ORDER BY ts DESC LIMIT 500")
            result = []
            for r in cur.fetchall():
                d = dict(r)
                try:
                    if d.get("data") and d["data"] != "{}":
                        d.update(json.loads(d["data"]))
                except Exception:
                    pass
                if d.get("ts") and not isinstance(d["ts"], str):
                    d["ts"] = d["ts"].isoformat()
                result.append(d)
            return result
    except Exception as e:
        print(f"[DB] load_journal error: {e}")
        return []
    finally:
        conn.close()


def load_recent(limit: int = 100) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM journal ORDER BY ts DESC LIMIT %s", (limit,))
            result = []
            for r in cur.fetchall():
                d = dict(r)
                try:
                    if d.get("data") and d["data"] != "{}":
                        d.update(json.loads(d["data"]))
                except Exception:
                    pass
                if d.get("ts") and not isinstance(d["ts"], str):
                    d["ts"] = d["ts"].isoformat()
                result.append(d)
            return result
    except Exception as e:
        print(f"[DB] load_recent error: {e}")
        return []
    finally:
        conn.close()


# ── Trade history ──────────────────────────────────────────────────────────

def _cast_trade(d: dict) -> dict:
    for f in ("entry","exit_price","stop_loss","take_profit"):
        d[f] = float(_d0(d.get(f,0), PRICE))
    d["exit"] = d.get("exit_price", 0)
    d["size"] = float(_d0(d.get("size",0), SIZE))
    for f in ("pl","total_pl","risk","risk_1r","mfe","mae",
               "planned_tp_dollars","new_balance"):
        d[f] = float(_d0(d.get(f,0), MONEY))
    for f in ("mfe_r","mae_r","be_trigger_r","realized_r",
               "planned_rr","planned_tp_r"):
        d[f] = float(_d0(d.get(f,0), RATIO))
    d["rr"]         = float(_d0(d.get("planned_rr",0), RATIO))
    d["confidence"] = int(d.get("confidence",0) or 0)
    d["exit_type"]  = str(d.get("exit_type") or "UNKNOWN")
    d["closed_at"]  = d["closed_at"].isoformat() if d.get("closed_at") else ""
    return d


def load_closed_trades(days: int = 999) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM trade_history "
                "WHERE closed_at >= NOW() - (INTERVAL '1 day' * %s) "
                "ORDER BY closed_at DESC",
                (int(days),))
            return [_cast_trade(dict(r)) for r in cur.fetchall()]
    except Exception as e:
        print(f"[DB] load_closed_trades error: {e}")
        return []
    finally:
        conn.close()


def load_closed_trades_today() -> list:
    """RAISES on DB error — executor must catch and block new entries."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT trade_id, symbol, side,
                       pl, COALESCE(total_pl, pl, 0) AS total_pl,
                       COALESCE(risk_1r, risk, 0) AS risk_1r,
                       COALESCE(exit_type,'UNKNOWN') AS exit_type,
                       closed_at AS ts
                FROM trade_history
                WHERE closed_at >= DATE_TRUNC('day', NOW())
                ORDER BY closed_at DESC
            """)
            hist_rows = list(cur.fetchall())

            # Partial closes today — safe if table not yet created
            partial_rows = []
            try:
                cur.execute("""
                    SELECT event_id, trade_id, pl, pl AS total_pl,
                           0 AS risk_1r, 'PARTIAL_TP' AS exit_type, ts
                    FROM partial_close_events
                    WHERE ts >= DATE_TRUNC('day', NOW())
                    ORDER BY ts DESC
                """)
                partial_rows = list(cur.fetchall())
            except Exception:
                pass  # table not yet created on fresh DB

        result = []
        for r in hist_rows:
            d = dict(r)
            d["pl"]       = float(_d0(d.get("pl",0),       MONEY))
            d["total_pl"] = float(_d0(d.get("total_pl",0), MONEY))
            d["risk_1r"]  = float(_d0(d.get("risk_1r",0),  MONEY))
            d["source"]   = "trade_history"
            d["ts"]       = d["ts"].isoformat() if d.get("ts") else ""
            result.append(d)
        for r in partial_rows:
            d = dict(r)
            d["pl"]      = float(_d0(d.get("pl",0), MONEY))
            d["total_pl"]= d["pl"]
            d["risk_1r"] = 0.0
            d["source"]  = "partial_close_events"
            d["ts"]      = d["ts"].isoformat() if d.get("ts") else ""
            result.append(d)
        return result
    finally:
        conn.close()


# ── Shims ──────────────────────────────────────────────────────────────────

def save_closed_trade(trade: dict):
    print("[DB] WARNING: save_closed_trade() is deprecated.")


def recalc_equity(prices: dict = None) -> list:
    """Return equity curve from trade history."""
    try:
        trades = load_closed_trades(365)
        equity = []
        balance = 500.0
        for t in reversed(trades):
            balance += float(t.get("pl", 0))
            equity.append({
                "ts":      t.get("closed_at",""),
                "balance": round(balance, 2),
                "trade_id":t.get("trade_id",""),
            })
        return equity
    except Exception as e:
        print(f"[DB] recalc_equity error: {e}")
        return []


def load_positions_file() -> list:
    return get_open_positions()
