"""
executor.py - Stage 4: EXECUTE
================================
Handles position opening, management, and closing.

Data model:
  - One logical trade = one trade_history record (regardless of partials)
  - realized_pl accumulates partial proceeds in position
  - initial_size never changes after a partial close
  - MFE/MAE use initial_size and candle high/low for accuracy
  - All milestones are R-based (not fixed dollar amounts)

R-Based Milestone System:
  +1.25R → Break-even (SL to entry)
  +2.0R  → Lock +1R profit (SL to entry + 1 sl_dist)
  +3.0R  → Partial TP 50% + activate trail
  +3.0R+ → Let remainder run if structure valid

Timeout: entry_timeframe stored at open determines hold duration.
"""

import uuid
from datetime import datetime

from config import (
    MAX_RISK_USD, RISK_PER_TRADE_PCT, MIN_RISK_REWARD,
    MIN_CONFIDENCE, MIN_TREND_STRENGTH, MIN_TIMEFRAMES_ALIGNED,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
    MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, TRAIL_ATR_MULT,
    TIMEOUT_SHORT_HOURS, TIMEOUT_MEDIUM_HOURS, TIMEOUT_LONG_HOURS,
    MAX_HOLD_DAYS, SCAN_INTERVAL_SECONDS,
    DAILY_LOSS_LIMIT_PCT, MIN_PLANNED_TP_USD,
)
from database import (
    save_closed_trade, append_trade, load_journal,
    load_balance, save_balance,
    get_open_positions, get_open_positions_count,
    save_position, close_position_in_db, load_position,
)

import threading
_auto_thread  = None
_auto_running = False


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}")


# ── Candle strength ────────────────────────────────────────────────────────

def candle_strength(candles: list, ema20: float) -> int:
    """Candle/price-action strength only. Volume counted separately."""
    if len(candles) < 2:
        return 0
    c    = candles[-1]
    body = abs(c["close"] - c["open"])
    rng  = c["high"] - c["low"]
    if rng == 0:
        return 0
    body_pct = body / rng
    score    = 0
    if body_pct > 0.6:
        score += 10
    elif body_pct > 0.4:
        score += 5
    # Close direction vs EMA
    if ema20:
        if c["close"] > c["open"] and c["close"] > ema20:
            score += 5
        elif c["close"] < c["open"] and c["close"] < ema20:
            score += 5
    return min(score, 20)


# ── Confidence scoring ────────────────────────────────────────────────────

def compute_confidence(analysis: dict, decision: str) -> dict:
    """
    Confidence breakdown (max 100).
    Each category scored independently — volume not double-counted.
    """
    ms     = analysis.get("ms",  {})
    e20    = analysis.get("ema20",  0) or 0
    e50    = analysis.get("ema50",  0) or 0
    r      = analysis.get("rsi14",  50)
    vol    = analysis.get("vol",    {})
    frames = analysis.get("frames", [])
    atr    = analysis.get("atr14",  0) or 0
    candles= analysis.get("candles",[])

    d      = decision if decision in ("BUY","SELL") else "BUY"
    is_buy = d == "BUY"

    scores = {
        "Market Structure": 0,
        "EMA Alignment":    0,
        "RSI":              0,
        "Candle Strength":  0,
        "Volume":           0,
    }

    # 1. Market structure (max 25)
    trend  = ms.get("trend","")
    bos    = ms.get("bos", False)
    disp   = ms.get("displacement", 0)
    sp     = ms.get("strength_pct", 0)
    if (is_buy and trend=="Bullish") or (not is_buy and trend=="Bearish"):
        scores["Market Structure"] = 15
        if sp >= 50: scores["Market Structure"] += 5
        if bos:      scores["Market Structure"] += 3
        if disp >= 0.8: scores["Market Structure"] += 2

    # 2. EMA alignment (max 25)
    slope = analysis.get("ema20_slope", {})
    if e20 and e50:
        if (is_buy and e20 > e50) or (not is_buy and e20 < e50):
            scores["EMA Alignment"] = 15
            sd = slope.get("direction","")
            if is_buy  and sd in ("RISING","RISING_STRONG"):  scores["EMA Alignment"] += 10
            elif not is_buy and sd in ("FALLING","FALLING_STRONG"): scores["EMA Alignment"] += 10

    # 3. RSI (max 15)
    if is_buy:
        if 40 < r < 65:   scores["RSI"] = 15
        elif r <= 40:      scores["RSI"] = 12
        elif 65 <= r < 75: scores["RSI"] = 6
    else:
        if 35 < r < 60:   scores["RSI"] = 15
        elif r >= 60:      scores["RSI"] = 12
        elif 25 <= r < 35: scores["RSI"] = 6

    # 4. Candle strength (max 20) — price action only, no volume
    avg_vol = vol.get("avg20", 0)
    scores["Candle Strength"] = candle_strength(candles, e20)

    # 5. Volume (max 15) — counted once here only
    bp  = vol.get("buy_pressure",  50)
    sp2 = vol.get("sell_pressure", 50)
    rel = vol.get("relative", 1.0)
    if is_buy:
        if bp > 60 and rel > 1.2: scores["Volume"] = 15
        elif bp > 50:             scores["Volume"] = 8
        else:                     scores["Volume"] = 2
    else:
        if sp2 > 60 and rel > 1.2: scores["Volume"] = 15
        elif sp2 > 50:             scores["Volume"] = 8
        else:                      scores["Volume"] = 2

    total = min(sum(scores.values()), 100)
    return {"breakdown": scores, "total": total}


# ── Position sizing ───────────────────────────────────────────────────────

def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    """
    Calculate position size from risk budget and ATR stop distance.
    All four values (sl_dist, size, risk_1r, expected_profit) are
    internally consistent.
    """
    # Confidence multiplier
    if confidence >= 85:  mult = 1.0
    elif confidence >= 78: mult = 0.8
    elif confidence >= 70: mult = 0.6
    else:                  mult = 0.4

    pct_risk = round(balance * RISK_PER_TRADE_PCT / 100, 2)
    risk_1r  = round(min(MAX_RISK_USD, pct_risk) * mult, 2)
    risk_1r  = min(MAX_RISK_USD, max(risk_1r, 0.30))

    sl_dist  = atr * SL_ATR_MULTIPLIER
    min_dist = price * 0.001
    if sl_dist < min_dist:
        sl_dist = min_dist

    tp_dist  = atr * TP_ATR_MULTIPLIER

    if side == "BUY":
        sl = round(price - sl_dist, 2)
        tp = round(price + tp_dist, 2)
    else:
        sl = round(price + sl_dist, 2)
        tp = round(price - tp_dist, 2)

    rr   = round(tp_dist / sl_dist, 2)
    size = round(risk_1r / sl_dist, 6)
    expected = round(size * tp_dist, 2)

    return {
        "risk_1r":         risk_1r,
        "size":            size,
        "stop_loss":       sl,
        "take_profit":     tp,
        "sl_dist":         sl_dist,
        "tp_dist":         tp_dist,
        "rr":              rr,
        "expected_profit": expected,
        "size_label":      f"Conf {confidence}% → ${risk_1r:.2f} risk | TP ~${expected:.2f}",
    }


# ── Exit classification ───────────────────────────────────────────────────

def _get_exit_type(reason: str, pl: float, partial: float) -> str:
    """
    Normalized exit type. Uses reason string — NOT P/L thresholds.
    P/L thresholds are unreliable (e.g. a manual close at $0.60).
    """
    if partial < 1.0:
        return "PARTIAL_TP"
    r = reason.lower()
    if "take profit"  in r: return "TP"
    if "trailing stop" in r or "trail" in r: return "PROFIT_LOCK"
    if "stop loss"    in r: return "ACTUAL_SL"
    if "timeout"      in r: return "TIMEOUT"
    if "structure"    in r: return "STRUCTURE"
    if "manual"       in r: return "MANUAL"
    if "profit lock"  in r or "locked" in r: return "PROFIT_LOCK"
    if "break-even"   in r or "breakeven" in r or "be" in r: return "BREAK_EVEN"
    if "partial"      in r: return "PARTIAL_TP"
    return "UNKNOWN"


def _classify_exit(reason: str, pl: float, partial: float) -> str:
    """Human-readable exit description based on reason, not P/L thresholds."""
    et = _get_exit_type(reason, pl, partial)
    if et == "PARTIAL_TP":   return f"Partial TP (${pl:+.2f})"
    if et == "TP":           return f"Take Profit (${pl:+.2f})"
    if et == "ACTUAL_SL":    return f"Actual Loss (${pl:+.2f})"
    if et == "TIMEOUT":      return f"Timeout (${pl:+.2f})"
    if et == "STRUCTURE":    return f"Structure exit (${pl:+.2f})"
    if et == "MANUAL":       return f"Manual close (${pl:+.2f})"
    if et == "PROFIT_LOCK":  return f"Profit-lock exit (${pl:+.2f})"
    if et == "BREAK_EVEN":   return f"Break-Even exit (${pl:+.2f})"
    return reason


# ── Timeout ───────────────────────────────────────────────────────────────

def get_timeout_hours(position: dict) -> float:
    """Entry timeframe determines hold duration.
    15M        → SHORT
    1H         → MEDIUM
    4H / Daily → LONG
    """
    tf = str(position.get("entry_timeframe","")).strip().upper()
    if tf == "15M":              return TIMEOUT_SHORT_HOURS
    if tf == "1H":               return TIMEOUT_MEDIUM_HOURS
    if tf in ("4H","1D","DAILY"):return TIMEOUT_LONG_HOURS
    return TIMEOUT_MEDIUM_HOURS


# ── Daily loss check ──────────────────────────────────────────────────────

def todays_trade_count() -> int:
    try:
        from database import load_closed_trades_today
        return len(load_closed_trades_today())
    except Exception:
        from database import load_closed_trades
        return len(load_closed_trades(1))


def todays_loss_pct(balance: float) -> float:
    """Calculate total realized loss today as % of balance."""
    try:
        from database import load_closed_trades_today
        trades = load_closed_trades_today()
    except Exception:
        from database import load_closed_trades
        trades = load_closed_trades(1)
    losses = sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0)) < 0)
    return round(abs(losses) / balance * 100, 2) if balance > 0 else 0.0


# ── Structure validity ────────────────────────────────────────────────────

def structure_still_valid(position: dict, analysis: dict) -> bool:
    """
    Returns False if original trade thesis is broken.
    Uses CHoCH + sequence — not EMA (too laggy).
    """
    try:
        side  = position["side"]
        ms    = analysis.get("ms", {})
        trend = ms.get("trend","Neutral")
        choch = ms.get("choch", False)
        seq   = ms.get("sequence","")

        if choch:
            return False
        if side == "BUY"  and trend == "Bearish": return False
        if side == "SELL" and trend == "Bullish": return False

        parts = [p.strip() for p in seq.split("→")] if seq else []
        if len(parts) >= 2:
            last = parts[-1]
            if side == "BUY"  and last == "LL": return False
            if side == "SELL" and last == "HH": return False

        r = analysis.get("rsi14", 50)
        if side == "BUY"  and r > 85: return False
        if side == "SELL" and r < 15: return False

        return True
    except Exception:
        return True


# ── Check rules ───────────────────────────────────────────────────────────

def check_rules(symbol: str, side: str, analysis: dict,
                confidence: int, rr: float, balance: float,
                expected_profit: float = 0.0) -> dict:
    """Gate checks before any trade is opened."""
    ms     = analysis.get("ms",     {})
    regime = analysis.get("regime", {})
    price  = analysis.get("price",  0)
    atr    = analysis.get("atr14",  0)

    # Hard limits
    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved":False,"reason":f"Max {MAX_OPEN_POSITIONS} positions open."}
    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved":False,"reason":f"Daily ceiling of {MAX_TRADES_PER_DAY} trades reached."}
    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved":False,"reason":f"Daily loss limit {DAILY_LOSS_LIMIT_PCT}% hit."}
    symbol_u = str(symbol).strip().upper()
    if any(str(p.get("symbol","")).strip().upper() == symbol_u
           for p in get_open_positions()):
        return {"approved":False,"reason":f"{symbol} already open."}

    # Input validation
    if symbol not in VALID_SYMBOLS:
        return {"approved":False,"reason":f"Invalid symbol: {symbol}"}
    if side not in ("BUY","SELL"):
        return {"approved":False,"reason":f"Invalid side: {side}"}
    if price <= 0 or atr <= 0:
        return {"approved":False,"reason":"Invalid price or ATR."}

    # Ranging market
    if regime.get("regime") == "RANGING":
        return {"approved":False,"reason":"Market RANGING — no trend-following entries."}

    # Structure
    trend    = ms.get("trend","")
    strength = ms.get("strength_pct",0)
    sw_low   = ms.get("swing_low",0)
    sw_high  = ms.get("swing_high",0)

    if side=="BUY"  and trend!="Bullish":
        return {"approved":False,"reason":f"Structure is {trend}. Need Bullish to BUY."}
    if side=="SELL" and trend!="Bearish":
        return {"approved":False,"reason":f"Structure is {trend}. Need Bearish to SELL."}
    if strength < MIN_TREND_STRENGTH:
        return {"approved":False,"reason":f"Trend strength {strength}% below {MIN_TREND_STRENGTH}%."}

    # Anti-chase
    if atr > 0 and price > 0:
        if side=="BUY"  and sw_low  > 0 and (price-sw_low)/atr  > 4.5:
            return {"approved":False,"reason":f"Chasing — price {(price-sw_low)/atr:.1f} ATR above swing low."}
        if side=="SELL" and sw_high > 0 and (sw_high-price)/atr > 4.5:
            return {"approved":False,"reason":f"Chasing — price {(sw_high-price)/atr:.1f} ATR below swing high."}

    # R:R
    if rr < MIN_RISK_REWARD:
        return {"approved":False,"reason":f"R:R 1:{rr} below minimum 1:{MIN_RISK_REWARD}."}

    # Minimum planned TP opportunity
    if MIN_PLANNED_TP_USD > 0 and expected_profit < MIN_PLANNED_TP_USD:
        return {"approved":False,
                "reason":f"Planned TP ${expected_profit:.2f} below "
                         f"minimum ${MIN_PLANNED_TP_USD:.2f}."}

    # Confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved":False,"reason":f"Confidence {confidence}% below {MIN_CONFIDENCE}%."}

    # Timeframes
    frames   = analysis.get("frames",[])
    tf_total = len(frames)
    tf_ok    = sum(1 for f in frames if f.get("decision")==side)
    if tf_ok < MIN_TIMEFRAMES_ALIGNED:
        return {"approved":False,
                "reason":f"Only {tf_ok}/{tf_total} timeframes agree on {side}. "
                         f"Need {MIN_TIMEFRAMES_ALIGNED}+."}

    # CHoCH
    if ms.get("choch"):
        return {"approved":False,"reason":"CHoCH detected — structure reversing."}

    # Sequence validation
    seq   = ms.get("sequence","")
    parts = [p.strip() for p in seq.split("→")] if seq else []
    if parts:
        last = parts[-1]
        if side=="BUY"  and last=="LL": return {"approved":False,"reason":f"Last swing LL breaks bullish structure."}
        if side=="SELL" and last=="HH": return {"approved":False,"reason":f"Last swing HH breaks bearish structure."}

    return {"approved":True,"reason":"All conditions met."}


# ── Close trade (FIXED ordering) ──────────────────────────────────────────

def close_trade(position: dict, price: float,
                reason: str = "Manual", partial: float = 1.0) -> dict:
    """
    FIXED close flow:
      1. Calculate P/L for this execution
      2. Accumulate realized_pl in position
      3. Save balance exactly once
      4. PARTIAL: update position, journal entry, return
      5. FULL: write trade_history, THEN close_position_in_db, then journal
    """
    try:
        entry        = float(position["entry_price"])
        side         = position["side"]
        current_size = float(position["size"])
        close_size   = round(current_size * partial, 6)

        if close_size <= 0:
            return {"success":False,"reason":"Nothing to close."}

        pl = round(
            ((price - entry) * close_size if side=="BUY"
             else (entry - price) * close_size), 2
        )
        risk_1r            = float(position.get("risk_1r") or position.get("risk_amount") or 0)
        previous_realized  = float(position.get("realized_pl") or 0)
        total_realized     = round(previous_realized + pl, 2)

        # Update balance exactly once
        balance     = load_balance()
        new_balance = round(balance + pl, 2)
        save_balance(new_balance)

        # Duration
        duration = ""
        try:
            opened   = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            secs     = int((datetime.utcnow() - opened).total_seconds())
            h, m     = secs // 3600, (secs % 3600) // 60
            duration = f"{h}h {m}m" if h else f"{m}m"
        except Exception as _e:
            _log(f"Duration calculation error: {_e}")

        # ── PARTIAL CLOSE ────────────────────────────────────────────
        if partial < 1.0:
            position["size"]           = round(current_size - close_size, 6)
            position["partial_closed"] = True
            position["realized_pl"]    = total_realized
            save_position(position)
            append_trade({
                "action":            "PARTIAL_TP",
                "trade_id":          position.get("trade_id",""),
                "symbol":            position["symbol"],
                "side":              side,
                "entry":             entry,
                "exit":              price,
                "size":              close_size,
                "pl":                pl,
                "r_multiple":        round(pl/risk_1r,3) if risk_1r>0 else 0,
                "realized_pl_total": total_realized,
                "reason":            reason,
                "timestamp":         datetime.utcnow().isoformat(),
            })
            return {
                "success":        True,
                "partial":        True,
                "realized_pl":    total_realized,
                "closed_size":    close_size,
                "remaining_size": position["size"],
            }

        # ── FULL CLOSE ───────────────────────────────────────────────
        # Use initial_size for trade record (reflects full original position)
        initial_size = float(position.get("initial_size") or current_size)
        realized_r   = round(total_realized / risk_1r, 3) if risk_1r > 0 else 0

        save_closed_trade({
            "trade_id":           position.get("trade_id",""),
            "symbol":             position["symbol"],
            "side":               side,
            "entry":              entry,
            "exit":               price,
            "stop_loss":          position.get("stop_loss",0),
            "take_profit":        position.get("take_profit",0),
            "size":               initial_size,
            "risk":               risk_1r,
            "risk_1r":            risk_1r,
            "pl":                 total_realized,        # total, not just final portion
            "realized_r":         realized_r,
            "realized_pl":        total_realized,
            "mfe":                position.get("mfe",0),
            "mae":                position.get("mae",0),
            "mfe_r":              position.get("mfe_r",0),
            "mae_r":              position.get("mae_r",0),
            "be_trigger_r":       position.get("be_trigger_r",0),
            "planned_rr":         position.get("planned_rr",  position.get("rr",0)),
            "planned_tp_r":       position.get("planned_tp_r", 0),
            "planned_tp_dollars": position.get("planned_tp_dollars",0),
            "confidence":         position.get("confidence",0),
            "mode":               position.get("trade_mode","STRUCTURED"),
            "exit_reason":        _classify_exit(reason, total_realized, 1.0),
            "exit_type":          _get_exit_type(reason, total_realized, 1.0),
            "duration":           duration,
            "new_balance":        new_balance,
            "session":            position.get("session",""),
            "trend":              position.get("entry_trend",""),
            "structure":          position.get("entry_structure",""),
            "rsi":                position.get("entry_rsi",0),
            "rr":                 position.get("rr",0),
            "opened_at":          str(position.get("opened_at","")),
        })

        # ONLY NOW remove live position
        close_position_in_db(position.get("trade_id",""))

        append_trade({
            "action":            "CLOSE",
            "trade_id":          position.get("trade_id",""),
            "symbol":            position["symbol"],
            "side":              side,
            "entry":             entry,
            "exit":              price,
            "size":              initial_size,
            "pl":                total_realized,
            "r_multiple":        realized_r,
            "new_balance":       new_balance,
            "duration":          duration,
            "exit_reason":       _classify_exit(reason, total_realized, 1.0),
            "exit_type":         _get_exit_type(reason, total_realized, 1.0),
            "risk_1r":           risk_1r,
            "mfe":               position.get("mfe",0),
            "mae":               position.get("mae",0),
            "mfe_r":             position.get("mfe_r",0),
            "mae_r":             position.get("mae_r",0),
            "realized_r":        realized_r,
            "planned_rr":        position.get("planned_rr", position.get("rr",0)),
            "planned_tp_r":      position.get("planned_tp_r",0),
            "planned_tp_dollars":position.get("planned_tp_dollars",0),
            "confidence":        position.get("confidence",0),
            "timestamp":         datetime.utcnow().isoformat(),
        })

        _log(
            f"{'WIN' if total_realized>=0 else 'LOSS'} "
            f"#{position.get('trade_id','')} {position['symbol']} "
            f"P/L ${total_realized:+,.2f} ({realized_r:+.2f}R) | "
            f"Balance ${new_balance:,.2f} | {reason}"
        )
        return {
            "success":     True,
            "partial":     False,
            "pl":          total_realized,
            "new_balance": new_balance,
            "duration":    duration,
            "r_multiple":  realized_r,
        }

    except Exception as e:
        _log(f"close_trade error: {e}")
        return {"success":False,"pl":0,"new_balance":load_balance(),
                "duration":"","r_multiple":0,"reason":str(e)}


# ── Open trade ────────────────────────────────────────────────────────────

def open_trade(symbol: str, decision: dict, analysis: dict,
               balance: float) -> dict:
    """Open a new position with full planned opportunity tracking."""
    try:
        side       = decision.get("decision","")
        price      = analysis.get("price", 0)
        atr        = analysis.get("atr14", 0)
        confidence = decision.get("confidence",{}).get("total", 60)
        ms         = analysis.get("ms",{})

        # Normalize + validate inputs
        symbol = str(symbol).strip().upper()
        side   = str(side).strip().upper()
        if symbol not in VALID_SYMBOLS:
            return {"success":False,"reason":f"Invalid symbol: {symbol}"}
        if side not in ("BUY","SELL"):
            return {"success":False,"reason":f"Invalid side: {side}"}
        if price <= 0:
            return {"success":False,"reason":"Invalid price."}
        if atr <= 0:
            return {"success":False,"reason":"Invalid ATR (insufficient candle data)."}

        calc = calc_position(balance, price, atr, side, confidence)

        # Two-stage sizing:
        # Stage 1: confidence determines risk budget (max dollar risk)
        # Stage 2: final structural SL determines position size
        dec_levels  = decision.get("levels", {})
        risk_budget = calc["risk_1r"]   # confidence-based budget
        if dec_levels.get("stop_loss",0) > 0:
            calc["stop_loss"]   = dec_levels["stop_loss"]
            calc["take_profit"] = dec_levels["take_profit"]

        # Recalculate everything from final SL distance
        sl_d = abs(price - calc["stop_loss"])
        tp_d = abs(calc["take_profit"] - price)
        if sl_d <= 0 or tp_d <= 0:
            return {"success":False,"reason":"Invalid SL/TP distance after override."}

        calc["sl_dist"]         = sl_d
        calc["tp_dist"]         = tp_d
        calc["size"]            = round(risk_budget / sl_d, 6)
        calc["risk_1r"]         = round(calc["size"] * sl_d, 2)
        calc["rr"]              = round(tp_d / sl_d, 2)
        calc["expected_profit"] = round(calc["size"] * tp_d, 2)

        # Hard cap
        if calc["risk_1r"] > MAX_RISK_USD:
            calc["size"]            = round(MAX_RISK_USD / sl_d, 6)
            calc["risk_1r"]         = MAX_RISK_USD
            calc["expected_profit"] = round(calc["size"] * tp_d, 2)

        rr = calc["rr"]

        check = check_rules(symbol, side, analysis, confidence, rr,
                            balance, calc["expected_profit"])
        if not check["approved"]:
            return {"success":False,"reason":check["reason"]}

        trade_id = str(uuid.uuid4())[:8].upper()

        # Determine entry timeframe from MTF analysis
        frames = analysis.get("frames",[])
        entry_tf = ""
        for f in reversed(frames):  # prefer lower TF for timing
            if f.get("decision") == side:
                entry_tf = f.get("label","")
                break

        position = {
            "trade_id":          trade_id,
            "symbol":            symbol,
            "side":              side,
            "entry_price":       price,
            "size":              calc["size"],
            "initial_size":      calc["size"],   # never changes
            "risk_amount":       calc["risk_1r"],
            "risk_1r":           calc["risk_1r"],
            "stop_loss":         calc["stop_loss"],
            "take_profit":       calc["take_profit"],
            "sl_dist":           calc["sl_dist"],
            "tp_dist":           calc["tp_dist"],
            "rr":                rr,
            "planned_rr":        rr,
            "planned_tp_r":      rr,
            "planned_tp_dollars":calc["expected_profit"],
            "realized_pl":       0.0,
            "confidence":        confidence,
            "trade_mode":        "STRUCTURED",
            "entry_timeframe":   entry_tf,
            "entry_trend":       ms.get("trend",""),
            "entry_structure":   ms.get("structure",""),
            "entry_rsi":         round(analysis.get("rsi14",50), 1),
            "be_moved":          False,
            "profit_locked":     False,
            "partial_closed":    False,
            "trail_sl":          False,
            "atr_at_open":       atr,
            "mfe":               0.0,
            "mae":               0.0,
            "mfe_r":             0.0,
            "mae_r":             0.0,
            "be_trigger_r":      0.0,
            "opened_at":         datetime.utcnow().isoformat(),
            "status":            "OPEN",
            "sl_moved_at":       "",
        }

        save_position(position)
        # Opening a position does not change balance in paper trading.
        # Balance only changes when positions close (in close_trade).

        size_desc = calc.get("size_label","")
        append_trade({
            "action":         "OPEN",
            "trade_id":       trade_id,
            "symbol":         symbol,
            "side":           side,
            "entry_price":    price,
            "stop_loss":      calc["stop_loss"],
            "take_profit":    calc["take_profit"],
            "size":           calc["size"],
            "risk_1r":        calc["risk_1r"],
            "rr":             round(rr, 2),
            "confidence":     confidence,
            "planned_tp":     calc["expected_profit"],
            "entry_timeframe":entry_tf,
            "structure":      ms.get("sequence",""),
            "size_label":     size_desc,
            "timestamp":      datetime.utcnow().isoformat(),
        })

        _log(
            f"OPENED #{trade_id} {side} {calc['size']} {symbol} "
            f"@ ${price:,.2f} | SL ${calc['stop_loss']:,.2f} "
            f"| TP ${calc['take_profit']:,.2f} "
            f"| R:R 1:{rr:.2f} | Risk ${calc['risk_1r']:.2f} "
            f"| TP ~${calc['expected_profit']:.2f} | {size_desc}"
        )
        return {
            "success":  True,
            "trade_id": trade_id,
            "position": position,
        }

    except Exception as e:
        _log(f"open_trade error: {e}")
        return {"success":False,"reason":str(e)}


# ── Manage position ───────────────────────────────────────────────────────

def manage_position(position: dict, price: float,
                    atr: float, analysis: dict) -> bool:
    """
    R-Based Milestone System:
      +1.25R → Break-even (SL to entry)
      +2.0R  → Lock +1R profit
      +3.0R  → Partial TP 50% + activate trail
      +3.0R+ → Let remainder run if structure valid

    Timeout: entry_timeframe determines hold duration.
    Returns True if position was closed.
    """
    try:
        entry   = float(position["entry_price"])
        sl      = float(position["stop_loss"])
        tp      = float(position["take_profit"])
        side    = position["side"]
        size    = float(position.get("size",0))
        risk_1r = float(position.get("risk_1r") or
                        position.get("risk_amount") or 2.0)
        if risk_1r <= 0:
            risk_1r = 2.0

        # ── Three separate P/L concepts ───────────────────────────────────
        # current_pl   = money at stake on REMAINING position (for SL/TP)
        # excursion_pl = what ORIGINAL trade is doing at this price
        # excursion_r  = milestone R — based on original size/risk
        initial_size = float(position.get("initial_size") or size or 0)

        current_pl = round(
            ((price - entry) * size if side=="BUY"
             else (entry - price) * size), 2
        )
        excursion_pl = round(
            ((price - entry) * initial_size if side=="BUY"
             else (entry - price) * initial_size), 2
        )
        excursion_r = excursion_pl / risk_1r if risk_1r > 0 else 0.0

        # Keep fl as alias for current_pl (used in log messages)
        fl = current_pl

        # ── MFE/MAE using candle high/low + initial_size ──────────────────
        candles = analysis.get("candles",[])
        if candles:
            last_candle     = candles[-1]
            favorable_price = last_candle["high"] if side=="BUY" else last_candle["low"]
            adverse_price   = last_candle["low"]  if side=="BUY" else last_candle["high"]
        else:
            favorable_price = price
            adverse_price   = price

        mfe_pl = round((favorable_price - entry) * initial_size
                       if side=="BUY"
                       else (entry - favorable_price) * initial_size, 2)
        mae_pl = round((adverse_price - entry) * initial_size
                       if side=="BUY"
                       else (entry - adverse_price) * initial_size, 2)

        _mfe_changed = _mae_changed = False
        if risk_1r > 0:
            if mfe_pl > float(position.get("mfe", 0.0)):
                position["mfe"]   = round(mfe_pl, 4)
                position["mfe_r"] = round(mfe_pl / risk_1r, 3)
                _mfe_changed = True
            if mae_pl < float(position.get("mae", 0.0)):
                position["mae"]   = round(mae_pl, 4)
                position["mae_r"] = round(mae_pl / risk_1r, 3)
                _mae_changed = True

        # ── Hard SL/TP ───────────────────────────────────────────────────
        if side == "BUY":
            if price <= sl:
                close_trade(position, price, "Stop Loss hit"); return True
            if price >= tp:
                close_trade(position, price, "Take Profit hit"); return True
        else:
            if price >= sl:
                close_trade(position, price, "Stop Loss hit"); return True
            if price <= tp:
                close_trade(position, price, "Take Profit hit"); return True

        # ── Structure validity ────────────────────────────────────────────
        protected = (
            position.get("be_moved",     False) or
            position.get("profit_locked", False) or
            position.get("trail_sl",      False)
        )
        if not structure_still_valid(position, analysis):
            if protected:
                _log(f"Structure shifted but protected — holding "
                     f"#{position.get('trade_id','')}")
            else:
                reason = (f"Structure invalidated — "
                          f"{analysis.get('ms',{}).get('trend','')} reversed "
                          f"(P/L ${fl:+.2f})")
                close_trade(position, price, reason)
                return True

        # ── Timeout ───────────────────────────────────────────────────────
        try:
            opened     = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            now_utc    = datetime.utcnow().replace(tzinfo=None)
            opened_naive = opened.replace(tzinfo=None)
            hours_open = (now_utc - opened_naive).total_seconds() / 3600
            timeout_h  = get_timeout_hours(position)

            if hours_open >= MAX_HOLD_DAYS * 24:
                close_trade(position, price,
                            f"Max hold {MAX_HOLD_DAYS}d reached (${fl:.2f})")
                return True
            if hours_open >= timeout_h and fl < MIN_PROFIT_TO_HOLD:
                close_trade(position, price,
                            f"Timeout {hours_open:.1f}h no progress (${fl:.2f})")
                return True
            if hours_open >= 48 and not position.get("partial_closed"):
                close_trade(position, price,
                            f"48h timeout — freeing capital (${fl:.2f})")
                return True
        except Exception as _e:
            _log(f"Timeout check error: {_e}")

        # ══════════════════════════════════════════════════════════════
        # R-BASED MILESTONE SYSTEM
        #   +1.25R → Break-even (SL to entry)
        #   +2.0R  → Lock +1R profit
        #   +3.0R  → Partial TP 50% + activate trail
        #   +3.0R+ → Let remainder run if structure valid
        # ══════════════════════════════════════════════════════════════

        # ── Milestone 1: Break-even at +1.25R ────────────────────────────
        if not position.get("be_moved", False) and excursion_r >= 1.25:
            new_sl = round(entry + 0.01, 2) if side=="BUY" else round(entry - 0.01, 2)
            if (side=="BUY" and new_sl > sl) or (side=="SELL" and new_sl < sl):
                position["stop_loss"] = new_sl
                position["be_moved"]  = True
                r_now = round(fl / risk_1r, 2)
                position["be_trigger_r"] = r_now
                save_position(position)
                append_trade({
                    "action":    "SL_MOVED_BE",
                    "trade_id":  position.get("trade_id",""),
                    "symbol":    position["symbol"],
                    "new_sl":    new_sl,
                    "old_sl":    sl,
                    "profit_at": round(fl, 2),
                    "r_at_move": r_now,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                _log(f"BE @ ${new_sl:,.2f} | +{r_now}R (${fl:.2f}) "
                     f"— BREAK-EVEN PROTECTION #{position.get('trade_id','')}")

        # ── Milestone 2: Lock +1R at +2R ─────────────────────────────────
        if (position.get("be_moved", False) and
                not position.get("profit_locked", False) and
                excursion_r >= 2.0):
            sl_dist    = float(position.get("sl_dist",0)) or abs(entry - sl)
            lock_price = (round(entry + sl_dist, 2) if side=="BUY"
                         else round(entry - sl_dist, 2))
            locked_usd = round(abs(lock_price - entry) * size, 2)
            cur_sl     = position["stop_loss"]
            if (side=="BUY" and lock_price > cur_sl) or \
               (side=="SELL" and lock_price < cur_sl):
                position["stop_loss"]    = lock_price
                position["profit_locked"]= True
                save_position(position)
                append_trade({
                    "action":     "PROFIT_LOCKED",
                    "trade_id":   position.get("trade_id",""),
                    "symbol":     position["symbol"],
                    "new_sl":     lock_price,
                    "old_sl":     cur_sl,
                    "locked_usd": locked_usd,
                    "profit_at":  round(fl, 2),
                    "r_at_move":  round(fl / risk_1r, 2),
                    "timestamp":  datetime.utcnow().isoformat(),
                })
                _log(f"1R LOCKED — SL→${lock_price:,.2f} "
                     f"(~${locked_usd:.2f}) #{position.get('trade_id','')}")

        # ── Milestone 3: Partial TP at +3R ───────────────────────────────
        if not position.get("partial_closed", False) and excursion_r >= 3.0:
            r_now  = round(fl / risk_1r, 1)
            result = close_trade(position, price,
                                 f"Partial TP +{r_now}R (${fl:.2f})", partial=0.5)
            if result.get("success"):
                _log(f"PARTIAL TP 50% @ ${price:,.2f} (+{r_now}R / +${fl:.2f})")
            # Return False — next scan recalculates with reduced size
            # Persist MFE/MAE
            if _mfe_changed or _mae_changed:
                save_position(position)
            return False

        # ── Milestone 4: Trail after +3R ─────────────────────────────────
        if (position.get("partial_closed", False) and
                excursion_r >= 3.0 and atr > 0):
            trail_dist = atr * TRAIL_ATR_MULT
            if side == "BUY":
                new_sl = round(price - trail_dist, 2)
                if new_sl > position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"TRAIL SL→${new_sl:,.2f} #{position.get('trade_id','')}")
            else:
                new_sl = round(price + trail_dist, 2)
                if new_sl < position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"TRAIL SL→${new_sl:,.2f} #{position.get('trade_id','')}")

        # Periodic MFE/MAE persist (every 5 scans to avoid DB overload)
        if _mfe_changed or _mae_changed:
            _scan_count = position.get("_scan_count", 0) + 1
            position["_scan_count"] = _scan_count
            if _scan_count % 5 == 0:
                pos_to_save = {k:v for k,v in position.items()
                               if not k.startswith("_")}
                save_position(pos_to_save)

        return False

    except Exception as e:
        _log(f"manage_position error: {e}")
        return False


# ── Auto trading loop ─────────────────────────────────────────────────────

def _auto_loop():
    global _auto_running
    from scanner    import scan
    from analyzer   import analyze
    from decision_engine import decide

    _log("Aria Professional Engine started.")

    while _auto_running:
        try:
            account = None
            try:
                from database import get_account
                account = get_account()
            except Exception as _e:
                _log(f"Account load error: {_e}")

            balance = float(account["balance"]) if account else 500.0

            for symbol in VALID_SYMBOLS:
                try:
                    scan_data = scan(symbol)
                    analysis  = analyze(scan_data)
                    if "error" in analysis:
                        _log(f"{symbol} analysis error: {analysis['error']}")
                        continue

                    analysis["candles"] = scan_data.get("candles",[])
                    price = analysis.get("price", 0)

                    # Manage open positions
                    closed_this_scan = False
                    for pos in [p for p in get_open_positions()
                                if p["symbol"] == symbol]:
                        try:
                            was_closed = manage_position(pos, price,
                                            analysis.get("atr14",0), analysis)
                            if was_closed:
                                closed_this_scan = True
                        except Exception as _e:
                            _log(f"manage error {symbol}: {_e}")

                    # Reload balance — management may have closed positions
                    balance = load_balance()

                    # Prevent same-scan close → immediate re-entry
                    if closed_this_scan:
                        _log(f"{symbol} closed this scan — skipping entry")
                        continue

                    # Decide
                    try:
                        decision = decide(analysis)
                    except Exception as _e:
                        _log(f"{symbol} decide error: {_e}")
                        continue

                    dec   = decision.get("decision","WAIT")
                    conf  = decision.get("confidence",{}).get("total",0)
                    trend = analysis.get("ms",{}).get("trend","")
                    sp    = analysis.get("ms",{}).get("strength_pct",0)
                    frames= analysis.get("frames",[])
                    tf_total = len(frames)
                    tf_ok    = sum(1 for f in frames if f.get("decision")==dec)

                    _log(f"{symbol} → {dec} | Conf {conf}% | "
                         f"Trend {trend} ({sp}%) | "
                         f"{tf_ok}/{tf_total} TF agree | "
                         f"RSI {analysis.get('rsi14',0):.1f}")

                    if dec in ("BUY","SELL"):
                        fresh_balance = load_balance()
                        result = open_trade(symbol, decision, analysis, fresh_balance)
                        if result.get("success"):
                            balance = fresh_balance
                        elif result.get("reason"):
                            _log(f"{symbol} blocked: {result['reason']}")

                except Exception as _e:
                    _log(f"Auto loop {symbol} error: {_e}")

        except Exception as _e:
            _log(f"Auto loop outer error: {_e}")

        import time
        time.sleep(SCAN_INTERVAL_SECONDS)


def start_auto_trading():
    global _auto_thread, _auto_running
    if _auto_running:
        return
    _auto_running = True
    _auto_thread  = threading.Thread(target=_auto_loop, daemon=True)
    _auto_thread.start()
    _log("Aria started — Structure first. Quality only.")


def stop_auto_trading():
    global _auto_running
    _auto_running = False
    _log("Auto-trading stopped.")


def get_auto_status() -> dict:
    return {
        "running": _auto_running,
        "thread":  _auto_thread.is_alive() if _auto_thread else False,
    }
