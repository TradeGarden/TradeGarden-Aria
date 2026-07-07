"""
executor.py — Stage 4: EXECUTE
================================
Responsibilities:
  - Enforce ALL trading rules before opening a trade
  - Calculate position size, stop loss, take profit
  - Enforce daily loss limit and max trades/day
  - Save position, log to journal
  - Reject trades that don't meet every condition
"""

import json, os, uuid
from datetime import datetime, date
from journal import append_trade, load_closed_trades
from config import (
    RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS,
    BUY_CONDITIONS, SELL_CONDITIONS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
)

POSITION_FILE = "paper_position.json"
BALANCE_FILE  = "paper_balance.txt"


# ──────────────────────────────────────────────
#  BALANCE
# ──────────────────────────────────────────────

def load_balance() -> float:
    try:
        if os.path.exists(BALANCE_FILE):
            with open(BALANCE_FILE) as f:
                return float(f.read().strip())
    except Exception:
        pass
    return 500.0


def save_balance(b: float):
    with open(BALANCE_FILE, "w") as f:
        f.write(str(round(b, 2)))


# ──────────────────────────────────────────────
#  POSITION
# ──────────────────────────────────────────────

def load_position():
    try:
        if os.path.exists(POSITION_FILE):
            with open(POSITION_FILE) as f:
                d = json.load(f)
                return d if d else None
    except Exception:
        pass
    return None


def save_position(p):
    with open(POSITION_FILE, "w") as f:
        json.dump(p, f, indent=2)


def clear_position():
    with open(POSITION_FILE, "w") as f:
        json.dump(None, f)


# ──────────────────────────────────────────────
#  DAILY STATS
# ──────────────────────────────────────────────

def _todays_closed() -> list:
    today  = date.today().isoformat()
    return [
        t for t in load_closed_trades()
        if t.get("closed_at", "")[:10] == today
    ]


def todays_trade_count() -> int:
    today = date.today().isoformat()
    from journal import load_journal
    return sum(
        1 for t in load_journal()
        if t.get("action") == "OPEN"
        and t.get("opened_at", "")[:10] == today
    )


def todays_loss_pct(balance: float) -> float:
    """Return today's total loss as a % of current balance."""
    todays_pl = sum(t.get("pl", 0) for t in _todays_closed())
    if todays_pl >= 0 or balance <= 0:
        return 0.0
    return round(abs(todays_pl) / balance * 100, 2)


# ──────────────────────────────────────────────
#  POSITION SIZE
# ──────────────────────────────────────────────

def calc_position(balance: float, price: float, atr: float, side: str) -> dict:
    risk_usd = round(balance * RISK_PER_TRADE_PCT / 100, 2)

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    size    = round(risk_usd / sl_dist, 6) if sl_dist > 0 else 0.0

    return {
        "risk_usd":    risk_usd,
        "size":        size,
        "stop_loss":   sl,
        "take_profit": tp,
        "rr":          rr,
    }


# ──────────────────────────────────────────────
#  RULE CHECKER
# ──────────────────────────────────────────────

def check_rules(side: str, analysis: dict, confidence: int, rr: float, balance: float) -> dict:
    """
    Enforce every trading rule.
    Returns {"approved": True/False, "reason": "..."}
    """
    ms  = analysis["ms"]
    vol = analysis["vol"]
    pat = analysis["patterns"]

    conditions = BUY_CONDITIONS if side == "BUY" else SELL_CONDITIONS
    expected_trend = conditions["market_structure"]   # "Bullish" or "Bearish"

    # 1. Max open positions
    if load_position():
        return {"approved": False, "reason": "Already have an open position. Max 1 at a time."}

    # 2. Max trades today
    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False, "reason": f"Daily trade limit reached ({MAX_TRADES_PER_DAY} trades)."}

    # 3. Daily loss limit
    daily_loss = todays_loss_pct(balance)
    if daily_loss >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False, "reason": f"Daily loss limit hit ({daily_loss:.1f}%). Trading paused for today."}

    # 4. Market structure
    if ms["trend"] != expected_trend:
        return {"approved": False, "reason": f"Market structure is {ms['trend']}, not {expected_trend}. WAIT."}

    # 5. EMA alignment
    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY"  and not ema_bull:
        return {"approved": False, "reason": "EMA20 is not above EMA50. Trend not confirmed. WAIT."}
    if side == "SELL" and not ema_bear:
        return {"approved": False, "reason": "EMA20 is not below EMA50. Trend not confirmed. WAIT."}

    # 6. Volume confirmation
    if conditions["volume_confirm"]:
        if side == "BUY"  and vol["buy_pressure"]  <= 55:
            return {"approved": False, "reason": f"Volume does not confirm buyers ({vol['buy_pressure']}% buy pressure). WAIT."}
        if side == "SELL" and vol["sell_pressure"] <= 55:
            return {"approved": False, "reason": f"Volume does not confirm sellers ({vol['sell_pressure']}% sell pressure). WAIT."}

    # 7. Candlestick confirmation
    if conditions["candle_confirm"]:
        has_confirm = any(
            p["direction"] == ("Bullish" if side == "BUY" else "Bearish")
            for p in pat
        )
        if not has_confirm:
            return {"approved": False, "reason": f"No {'bullish' if side=='BUY' else 'bearish'} confirmation candle. WAIT."}

    # 8. Minimum R:R
    if rr < MIN_RISK_REWARD:
        return {"approved": False, "reason": f"Risk/Reward {rr} is below minimum {MIN_RISK_REWARD}. WAIT."}

    # 9. Minimum confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved": False, "reason": f"Confidence {confidence}% is below minimum {MIN_CONFIDENCE}%. WAIT."}

    return {"approved": True, "reason": "All conditions met."}


# ──────────────────────────────────────────────
#  OPEN TRADE
# ──────────────────────────────────────────────

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    """
    Stage 4 — EXECUTE.
    Runs all rule checks, calculates position, saves and logs.

    Returns:
      {"success": True,  "position": {...}}  if approved
      {"success": False, "reason": "..."}    if rejected
    """
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]

    pos_calc = calc_position(balance, price, atr, side)
    rr       = pos_calc["rr"]

    # Run all rules
    check = check_rules(side, analysis, confidence, rr, balance)
    if not check["approved"]:
        return {"success": False, "reason": check["reason"]}

    trade_id = str(uuid.uuid4())[:8].upper()

    position = {
        "trade_id":    trade_id,
        "symbol":      symbol,
        "side":        side,
        "entry_price": price,
        "size":        pos_calc["size"],
        "risk_amount": pos_calc["risk_usd"],
        "stop_loss":   pos_calc["stop_loss"],
        "take_profit": pos_calc["take_profit"],
        "rr":          rr,
        "opened_at":   datetime.utcnow().isoformat(),
        "status":      "OPEN",
        "be_moved":    False,
        "trailing":    False,
    }

    save_position(position)

    append_trade({
        "action":        "OPEN",
        "trade_id":      trade_id,
        "symbol":        symbol,
        "side":          side,
        "entry":         price,
        "stop_loss":     pos_calc["stop_loss"],
        "take_profit":   pos_calc["take_profit"],
        "size":          pos_calc["size"],
        "risk":          pos_calc["risk_usd"],
        "rr":            rr,
        "trend":         decision["trend"],
        "structure":     analysis["ms"]["structure"],
        "sequence":      analysis["ms"]["sequence"],
        "ema20":         analysis["ema20"],
        "ema50":         analysis["ema50"],
        "rsi":           analysis["rsi14"],
        "rsi_label":     analysis["rsi_label"],
        "macd_line":     analysis["macd_line"],
        "volume_label":  analysis["vol"]["label"],
        "buy_pressure":  analysis["vol"]["buy_pressure"],
        "sell_pressure": analysis["vol"]["sell_pressure"],
        "patterns":      [p["name"] for p in analysis["patterns"]],
        "confidence":    confidence,
        "confidence_breakdown": decision["confidence"]["breakdown"],
        "reasoning":     decision["reasons"],
        "session":       analysis.get("session", ""),
        "opened_at":     datetime.utcnow().isoformat(),
    })

    return {"success": True, "position": position}


# ──────────────────────────────────────────────
#  CLOSE TRADE
# ──────────────────────────────────────────────

def close_trade(position: dict, current_price: float, reason: str = "Manual") -> dict:
    entry = position["entry_price"]
    size  = position["size"]
    side  = position["side"]

    pl = (current_price - entry) * size if side == "BUY" else (entry - current_price) * size
    pl = round(pl, 2)

    balance     = load_balance()
    new_balance = round(balance + pl, 2)
    save_balance(new_balance)
    clear_position()

    duration = ""
    try:
        opened   = datetime.fromisoformat(position["opened_at"])
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        duration = f"{h}h {rem // 60}m"
    except Exception:
        pass

    append_trade({
        "action":      "CLOSE",
        "trade_id":    position.get("trade_id", ""),
        "symbol":      position["symbol"],
        "side":        side,
        "entry":       entry,
        "exit":        current_price,
        "stop_loss":   position["stop_loss"],
        "take_profit": position["take_profit"],
        "size":        size,
        "risk":        position.get("risk_amount", 0),
        "pl":          pl,
        "new_balance": new_balance,
        "duration":    duration,
        "exit_reason": reason,
        "closed_at":   datetime.utcnow().isoformat(),
    })

    return {"pl": pl, "new_balance": new_balance, "duration": duration}
