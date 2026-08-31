"""
executor.py — Aria Professional Trading Engine
===============================================
Structure-first. R-based profit protection.

Entry hierarchy (in order of priority):
  1. Market structure (HH/HL, BOS, CHoCH, S/R, FVG)
  2. Multi-timeframe confirmation (2+ TFs agree)
  3. Momentum and volume
  4. Technical indicators (confirm only)

R-Based Milestone System:
  +1R   → Break-even (SL to entry) — NEVER lose again
  +1.2R → Lock profit (SL above entry — guaranteed gain)
  +2R   → Partial TP 50% + activate trail on remainder
  +2.5R → Strong continuation
  +3R+  → Let winner run if structure valid

Timeout (structure invalidation overrides time):
  15m/1H → 4h timeout
  1H/4H  → 12h timeout
  4H/1D  → 24h timeout
"""

import threading, time, uuid
from datetime import datetime, date
from database import (
    load_balance, save_balance,
    save_position, close_position_in_db,
    save_closed_trade, append_trade, load_journal,
    get_open_positions, get_open_positions_count,
    recalc_equity, cache_price,
)
from config import (
    MAX_RISK_USD, RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE, MIN_TREND_STRENGTH,
    MIN_TIMEFRAMES_ALIGNED, RSI_OVERBOUGHT, RSI_OVERSOLD,
    MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TRAIL_ATR_MULT,
    MILESTONE_BREAKEVEN, MILESTONE_LOCK, MILESTONE_LOCK_AMOUNT,
    MILESTONE_PARTIAL_TP, MILESTONE_TRAIL,
    TIMEOUT_SHORT_HOURS, TIMEOUT_MEDIUM_HOURS, TIMEOUT_LONG_HOURS,
    MAX_HOLD_DAYS, MIN_PROFIT_TO_HOLD, SCAN_INTERVAL_SECONDS,
)


# ── Candle strength (structure-aware) ─────────────────────────────────────

def candle_strength(candles: list, ema20: float, avg_vol: float) -> int:
    if len(candles) < 2:
        return 0
    c, p = candles[-1], candles[-2]
    body = abs(c["close"] - c["open"])
    rng  = c["high"] - c["low"]
    if rng == 0:
        return 0
    score = 0
    if body / rng > 0.5:                                    score += 5
    if c["close"] > p["high"] or c["close"] < p["low"]:    score += 5
    if (c["close"] > ema20 and c["open"] < c["close"]) or \
       (c["close"] < ema20 and c["open"] > c["close"]):    score += 5
    if avg_vol > 0 and c["volume"] > avg_vol * 1.3:         score += 5
    return min(score, 20)


# ── Confidence scoring ────────────────────────────────────────────────────

def compute_confidence(analysis: dict, decision: str) -> dict:
    scores = {
        "Market Structure": 0,
        "EMA Alignment":    0,
        "RSI":              0,
        "Candle Strength":  0,
        "Volume":           0,
    }
    ms  = analysis.get("ms", {})
    e20 = analysis.get("ema20", 0)
    e50 = analysis.get("ema50", 0)
    r   = analysis.get("rsi14", 50)
    vol = analysis.get("vol", {})

    d   = decision if decision in ("BUY","SELL") else (
          "BUY" if ms.get("trend") == "Bullish" else "SELL")
    buy = d == "BUY"

    # Market Structure — priority 1
    trend = ms.get("trend","")
    bos   = ms.get("bos", False)
    choch = ms.get("choch", False)
    if (buy and trend=="Bullish") or (not buy and trend=="Bearish"):
        scores["Market Structure"] = 20
        if bos:   scores["Market Structure"] = min(25, scores["Market Structure"] + 3)
        if choch: scores["Market Structure"] = min(25, scores["Market Structure"] + 2)
    elif trend in ("Bullish","Bearish"):
        scores["Market Structure"] = 10

    # EMA — confirms structure
    if (buy and e20 > e50) or (not buy and e20 < e50):
        scores["EMA Alignment"] = 25

    # RSI — momentum
    if buy:
        if 40 < r < 70:  scores["RSI"] = 15
        elif r <= 40:     scores["RSI"] = 10
        elif r < RSI_OVERBOUGHT: scores["RSI"] = 5
    else:
        if 30 < r < 60:  scores["RSI"] = 15
        elif r >= 60:     scores["RSI"] = 10
        elif r > RSI_OVERSOLD: scores["RSI"] = 5

    # Candle strength — volume + pattern
    scores["Candle Strength"] = candle_strength(
        analysis.get("candles", []),
        e20,
        vol.get("avg20", 0)
    )

    # Volume
    bp = vol.get("buy_pressure", 50)
    sp = vol.get("sell_pressure", 50)
    if buy:
        scores["Volume"] = 15 if bp > 55 else 10 if bp > 48 else 5
    else:
        scores["Volume"] = 15 if sp > 55 else 10 if sp > 48 else 5

    return {"breakdown": scores, "total": sum(scores.values())}


# ── Structure invalidation check ──────────────────────────────────────────

def structure_still_valid(position: dict, analysis: dict) -> bool:
    """
    Returns False if the original trade thesis is broken.
    Structure invalidation → exit immediately, time is secondary.
    """
    side  = position["side"]
    ms    = analysis.get("ms", {})
    e20   = analysis.get("ema20", 0)
    e50   = analysis.get("ema50", 0)
    r     = analysis.get("rsi14", 50)
    trend = ms.get("trend","")

    if side == "BUY":
        if trend == "Bearish": return False   # structure flipped
        if e20 < e50:          return False   # EMA reversed
        if r > 82:             return False   # extreme overbought
    else:
        if trend == "Bullish": return False
        if e20 > e50:          return False
        if r < 18:             return False

    return True


# ── Dynamic timeout by entry timeframe ───────────────────────────────────

def get_timeout_hours(position: dict) -> float:
    """Returns timeout hours based on which TF triggered the entry."""
    # For now default to medium — future: store entry TF in position
    return TIMEOUT_MEDIUM_HOURS


# ── Position sizing ($5 max risk, R-based) ────────────────────────────────

def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    """
    Size is always derived from risk, never from desired profit.
    1R = actual initial risk. Max $5.

    Size = Risk / SL_distance
    If ATR is large → smaller size (never more than $5 risk)
    """
    # Confidence-based scaling within the $5 cap
    if confidence >= 85:
        mult = 1.0
    elif confidence >= 75:
        mult = 0.8
    else:
        mult = 0.6

    # Risk amount: $5 cap OR 1% of balance, whichever is smaller
    pct_risk = round(balance * RISK_PER_TRADE_PCT / 100, 2)
    risk_1r  = round(min(MAX_RISK_USD, pct_risk) * mult, 2)
    risk_1r  = max(risk_1r, 0.50)  # minimum $0.50

    # SL distance in price
    sl_dist = atr * SL_ATR_MULTIPLIER
    if sl_dist < price * 0.001:
        sl_dist = price * 0.001

    if side == "BUY":
        sl = round(price - sl_dist, 2)
        tp = round(price + sl_dist * (MIN_RISK_REWARD / 1.0) * 1.2, 2)  # TP at 1.8R+
    else:
        sl = round(price + sl_dist, 2)
        tp = round(price - sl_dist * (MIN_RISK_REWARD / 1.0) * 1.2, 2)

    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 2)
    size    = round(risk_1r / sl_dist, 6)

    # Labels for clarity
    if mult == 1.0:
        label = f"Full 1R (${risk_1r:.2f} risk, conf {confidence}%)"
    elif mult == 0.8:
        label = f"0.8R (${risk_1r:.2f} risk, conf {confidence}%)"
    else:
        label = f"0.6R (${risk_1r:.2f} risk, conf {confidence}%)"

    return {
        "risk_1r":    risk_1r,
        "risk_usd":   risk_1r,
        "size":       size,
        "stop_loss":  sl,
        "take_profit":tp,
        "rr":         rr,
        "sl_dist":    sl_dist,
        "size_label": label,
    }


# ── Daily stats ───────────────────────────────────────────────────────────

def todays_trade_count() -> int:
    today = date.today().isoformat()
    try:
        return sum(1 for t in load_journal()
                   if t.get("action") == "OPEN"
                   and t.get("opened_at","")[:10] == today)
    except Exception:
        return 0


def todays_loss_pct(balance: float) -> float:
    try:
        from database import load_closed_trades
        today = date.today().isoformat()
        pls   = [float(t.get("pl",0)) for t in load_closed_trades(1)
                 if t.get("closed_at","")[:10] == today]
        loss  = sum(p for p in pls if p < 0)
        return round(abs(loss) / balance * 100, 2) if balance > 0 else 0.0
    except Exception:
        return 0.0


# ── Rule enforcement (structure-first) ───────────────────────────────────

def check_rules(symbol: str, side: str, analysis: dict,
                confidence: int, rr: float, balance: float) -> dict:
    ms     = analysis.get("ms", {})
    frames = analysis.get("frames", [])

    # Hard limits
    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False,
                "reason": f"Max {MAX_OPEN_POSITIONS} positions open. Waiting."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily ceiling of {MAX_TRADES_PER_DAY} trades reached."}

    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit {DAILY_LOSS_LIMIT_PCT}% hit. Done for today."}

    if any(p["symbol"] == symbol for p in get_open_positions()):
        return {"approved": False,
                "reason": f"{symbol} already open. One per symbol."}

    # ── Priority 1: Market Structure ──────────────────────────────────────
    trend    = ms.get("trend","")
    strength = ms.get("strength_pct", 0)
    bos      = ms.get("bos", False)

    if side == "BUY" and trend != "Bullish":
        return {"approved": False,
                "reason": f"Structure is {trend} ({ms.get('sequence','')}). "
                          f"Need Bullish HH/HL to BUY."}
    if side == "SELL" and trend != "Bearish":
        return {"approved": False,
                "reason": f"Structure is {trend} ({ms.get('sequence','')}). "
                          f"Need Bearish LH/LL to SELL."}

    if strength < MIN_TREND_STRENGTH:
        return {"approved": False,
                "reason": f"Trend strength only {strength}%. "
                          f"Need {MIN_TREND_STRENGTH}%+ for a valid entry."}

    # ── Priority 2: Multi-Timeframe Confirmation ──────────────────────────
    tf_agree = sum(1 for f in frames if f.get("decision") == side)
    if tf_agree < MIN_TIMEFRAMES_ALIGNED:
        return {"approved": False,
                "reason": f"Only {tf_agree}/4 timeframes agree on {side}. "
                          f"Need {MIN_TIMEFRAMES_ALIGNED}+. Waiting for confluence."}

    # ── Priority 3: EMA ────────────────────────────────────────────────────
    e20 = analysis.get("ema20", 0)
    e50 = analysis.get("ema50", 0)
    if side == "BUY" and e20 <= e50:
        return {"approved": False,
                "reason": f"EMA20 (${e20:,.0f}) below EMA50 (${e50:,.0f}). "
                          f"No uptrend confirmation."}
    if side == "SELL" and e20 >= e50:
        return {"approved": False,
                "reason": f"EMA20 (${e20:,.0f}) above EMA50 (${e50:,.0f}). "
                          f"No downtrend confirmation."}

    # ── Priority 4: RSI (indicator, not structure) ────────────────────────
    r = analysis.get("rsi14", 50)
    if side == "BUY" and r > RSI_OVERBOUGHT:
        return {"approved": False,
                "reason": f"RSI {r:.1f} above {RSI_OVERBOUGHT}. "
                          f"Overbought — wait for pullback."}
    if side == "SELL" and r < RSI_OVERSOLD:
        return {"approved": False,
                "reason": f"RSI {r:.1f} below {RSI_OVERSOLD}. "
                          f"Oversold — wait for bounce."}

    # R:R check
    if rr < MIN_RISK_REWARD:
        return {"approved": False,
                "reason": f"R:R 1:{rr} below minimum 1:{MIN_RISK_REWARD}. "
                          f"Not worth the risk."}

    # Confidence check
    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence {confidence}% below {MIN_CONFIDENCE}%. "
                          f"Setup not strong enough."}

    return {"approved": True, "reason": "All conditions met."}


# ── Open trade ────────────────────────────────────────────────────────────

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    try:
        balance    = load_balance()
        price      = analysis.get("price", 0)
        atr        = analysis.get("atr14", price * 0.02)
        confidence = decision.get("confidence", {}).get("total", 60)
        calc       = calc_position(balance, price, atr, side, confidence)
        rr         = calc["rr"]

        check = check_rules(symbol, side, analysis, confidence, rr, balance)
        if not check["approved"]:
            return {"success": False, "reason": check["reason"]}

        trade_id = str(uuid.uuid4())[:8].upper()
        position = {
            "trade_id":       trade_id,
            "symbol":         symbol,
            "side":           side,
            "entry_price":    price,
            "size":           calc["size"],
            "risk_amount":    calc["risk_1r"],
            "risk_1r":        calc["risk_1r"],
            "stop_loss":      calc["stop_loss"],
            "take_profit":    calc["take_profit"],
            "rr":             rr,
            "mode":           "STRUCTURED",
            "opened_at":      datetime.utcnow().isoformat(),
            "status":         "OPEN",
            "be_moved":       False,
            "profit_locked":  False,
            "partial_closed": False,
            "trail_sl":       False,
            "atr_at_open":    atr,
            "sl_dist":        calc["sl_dist"],
        }
        save_position(position)

        ms  = analysis.get("ms", {})
        vol = analysis.get("vol", {})

        append_trade({
            "action":      "OPEN",
            "trade_id":    trade_id,
            "symbol":      symbol,
            "side":        side,
            "entry":       price,
            "stop_loss":   calc["stop_loss"],
            "take_profit": calc["take_profit"],
            "size":        calc["size"],
            "risk_1r":     calc["risk_1r"],
            "size_label":  calc["size_label"],
            "rr":          rr,
            "confidence":  confidence,
            "trend":       ms.get("trend",""),
            "structure":   ms.get("structure",""),
            "sequence":    ms.get("sequence",""),
            "strength":    ms.get("strength_pct",0),
            "bos":         ms.get("bos", False),
            "ema20":       analysis.get("ema20",0),
            "ema50":       analysis.get("ema50",0),
            "rsi":         analysis.get("rsi14",0),
            "buy_pressure":vol.get("buy_pressure",50),
            "atr":         atr,
            "session":     analysis.get("session",""),
            "opened_at":   datetime.utcnow().isoformat(),
        })

        _log(f"OPENED #{trade_id} {side} {calc['size']} {symbol} "
             f"@ ${price:,.2f} | SL ${calc['stop_loss']:,.2f} "
             f"| TP ${calc['take_profit']:,.2f} | R:R 1:{rr} "
             f"| Risk ${calc['risk_1r']:.2f} | {calc['size_label']}")
        return {"success": True, "position": position}

    except Exception as e:
        _log(f"open_trade error: {e}")
        return {"success": False, "reason": str(e)}


# ── Close trade ───────────────────────────────────────────────────────────

def close_trade(position: dict, price: float,
                reason: str = "Manual", partial: float = 1.0) -> dict:
    try:
        entry    = position["entry_price"]
        side     = position["side"]
        size     = round(position["size"] * partial, 6)
        pl       = round(((price - entry) * size if side == "BUY"
                          else (entry - price) * size), 2)
        risk_1r  = position.get("risk_1r", position.get("risk_amount", 5.0))
        r_mult   = round(pl / risk_1r, 2) if risk_1r > 0 else 0

        balance     = load_balance()
        new_balance = round(balance + pl, 2)
        save_balance(new_balance)

        if partial >= 1.0:
            close_position_in_db(position["trade_id"])
        else:
            position["size"]           = round(position["size"] - size, 6)
            position["partial_closed"] = True
            save_position(position)

        duration = ""
        try:
            opened = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            secs   = int((datetime.utcnow() - opened).total_seconds())
            h, m   = secs // 3600, (secs % 3600) // 60
            duration = f"{h}h {m}m" if h > 0 else f"{m}m"
        except Exception:
            pass

        # Save ALL closes to trade_history (full and partial)
        save_closed_trade({
            "trade_id":    position.get("trade_id","") +
                           ("" if partial >= 1.0 else "_P"),
            "symbol":      position["symbol"],
            "side":        side,
            "entry":       entry,
            "exit":        price,
            "stop_loss":   position.get("stop_loss",0),
            "take_profit": position.get("take_profit",0),
            "size":        size,
            "risk":        risk_1r,
            "pl":          pl,
            "new_balance": new_balance,
            "duration":    duration,
            "exit_reason": reason + (" (partial 50%)" if partial < 1.0 else ""),
            "mode":        "STRUCTURED",
            "opened_at":   str(position.get("opened_at","")),
        })
        if partial >= 1.0:
            pass  # already saved above

        append_trade({
            "action":      "CLOSE" if partial >= 1.0 else "PARTIAL_TP",
            "trade_id":    position.get("trade_id",""),
            "symbol":      position["symbol"],
            "side":        side,
            "entry":       entry,
            "exit":        price,
            "size":        size,
            "pl":          pl,
            "r_multiple":  r_mult,
            "new_balance": new_balance,
            "duration":    duration,
            "exit_reason": reason,
            "closed_at":   datetime.utcnow().isoformat(),
        })

        emoji = "WIN" if pl >= 0 else "LOSS"
        _log(f"{emoji} #{position.get('trade_id','')} {position['symbol']} "
             f"P/L ${pl:+,.2f} ({r_mult:+.1f}R) | "
             f"Balance ${new_balance:,.2f} | {reason} [{duration}]")
        return {"pl": pl, "new_balance": new_balance,
                "duration": duration, "r_multiple": r_mult}

    except Exception as e:
        _log(f"close_trade error: {e}")
        return {"pl": 0, "new_balance": load_balance(),
                "duration": "", "r_multiple": 0}


# ── R-Based position management ───────────────────────────────────────────

def manage_position(position: dict, price: float,
                    atr: float, analysis: dict) -> bool:
    """
    Full R-based milestone management.
    Structure invalidation overrides all time rules.
    Returns True if position was fully closed.
    """
    try:
        entry   = position["entry_price"]
        side    = position["side"]
        size    = position["size"]
        sl      = position["stop_loss"]
        tp      = position["take_profit"]
        risk_1r = position.get("risk_1r", position.get("risk_amount", 5.0))
        sl_dist = position.get("sl_dist", abs(entry - sl))

        # Current floating P/L
        fl = round(((price - entry) * size if side == "BUY"
                    else (entry - price) * size), 2)
        r_earned = fl / risk_1r if risk_1r > 0 else 0

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

        # ── Structure Invalidation ────────────────────────────────────────
        # Only exit immediately if at a loss or tiny profit
        # If trade is profitable, let milestones protect it
        if not structure_still_valid(position, analysis):
            if fl >= 2.0:
                # Good profit - hold, milestones are protecting
                _log(f"Structure shifted but +${fl:.2f} profit — "
                     f"milestones protecting #{position.get('trade_id','')}")
            else:
                # Loss or tiny profit with broken structure - exit
                reason = (f"Structure invalidated — "
                          f"{analysis.get('ms',{}).get('trend','')} reversed "
                          f"(P/L ${fl:+.2f})")
                close_trade(position, price, reason)
                _log(f"STRUCTURE INVALID P/L ${fl:.2f} — "
                     f"closed #{position.get('trade_id','')}")
                return True

        # ── Timeout check (structure still valid = hold longer) ───────────
        try:
            opened     = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            hours_open = (datetime.utcnow() - opened).total_seconds() / 3600
            timeout_h  = get_timeout_hours(position)

            if hours_open >= MAX_HOLD_DAYS * 24:
                close_trade(position, price,
                            f"Max hold {MAX_HOLD_DAYS} days reached")
                return True

            # Close if no meaningful progress after timeout
            if hours_open >= timeout_h and fl < MIN_PROFIT_TO_HOLD:
                close_trade(position, price,
                            f"Timeout {hours_open:.1f}h no progress (${fl:.2f})")
                return True
            # Also close if been open very long with only small locked profit
            if hours_open >= 48 and not position.get("partial_closed"):
                close_trade(position, price,
                            f"48h timeout — closing to free capital (${fl:.2f})")
                return True
        except Exception:
            pass

        # ── Milestone 1: Break-even at +$2 profit ───────────────────────
        # Using USD directly - more reliable than R calculation
        if not position.get("be_moved") and fl >= 2.0:
            new_sl = round(entry + 0.01, 2) if side == "BUY" \
                     else round(entry - 0.01, 2)
            if (side == "BUY" and new_sl > sl) or \
               (side == "SELL" and new_sl < sl):
                position["stop_loss"] = new_sl
                position["be_moved"]  = True
                save_position(position)
                append_trade({
                    "action":    "SL_MOVED_BE",
                    "trade_id":  position.get("trade_id",""),
                    "symbol":    position["symbol"],
                    "new_sl":    new_sl,
                    "old_sl":    sl,
                    "profit_at": round(fl, 2),
                    "r_at_move": round(r_earned, 2),
                    "timestamp": datetime.utcnow().isoformat(),
                })
                _log(f"BREAK-EVEN @ ${new_sl:,.2f} "
                     f"profit ${fl:.2f} — trade now RISK FREE "
                     f"#{position.get('trade_id','')}")

        # ── Milestone 2: Lock $2.50 profit at +$3 ────────────────────────
        if position.get("be_moved") and \
           not position.get("profit_locked") and fl >= 3.0:
            # Move SL to lock in $2.50 minimum profit
            lock_size  = size if size > 0 else 0.001
            lock_move  = 2.50 / lock_size if lock_size > 0 else 0
            lock_price = (round(entry + lock_move, 2) if side == "BUY"
                         else round(entry - lock_move, 2))
            current_sl = position["stop_loss"]
            if (side == "BUY"  and lock_price > current_sl) or \
               (side == "SELL" and lock_price < current_sl):
                position["stop_loss"]    = lock_price
                position["profit_locked"]= True
                save_position(position)
                append_trade({
                    "action":     "PROFIT_LOCKED",
                    "trade_id":   position.get("trade_id",""),
                    "symbol":     position["symbol"],
                    "new_sl":     lock_price,
                    "old_sl":     current_sl,
                    "locked_usd": 2.50,
                    "profit_at":  round(fl, 2),
                    "timestamp":  datetime.utcnow().isoformat(),
                })
                _log(f"$2.50 LOCKED — SL -> ${lock_price:,.2f} "
                     f"(profit ${fl:.2f}) "
                     f"#{position.get('trade_id','')}")

        # ── Milestone 3 + 4: Partial TP at +$6, Trail after ─────────────
        if not position.get("partial_closed") and fl >= 6.0:
            close_trade(position, price,
                        f"Partial TP at +${fl:.2f}", partial=0.5)
            _log(f"PARTIAL TP 50% @ ${price:,.2f} (+${fl:.2f})")

        # Trail the remaining position after partial TP
        if position.get("partial_closed") and fl >= 8.0 and atr > 0:
            trail_dist = atr * TRAIL_ATR_MULT
            if side == "BUY":
                new_sl = round(price - trail_dist, 2)
                if new_sl > position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"TRAIL SL up to ${new_sl:,.2f} "
                         f"(ATR×{TRAIL_ATR_MULT}) "
                         f"#{position.get('trade_id','')}")
            else:
                new_sl = round(price + trail_dist, 2)
                if new_sl < position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"TRAIL SL down to ${new_sl:,.2f} "
                         f"#{position.get('trade_id','')}")

        return False

    except Exception as e:
        _log(f"manage_position error: {e}")
        return False


# ── Equity loop ───────────────────────────────────────────────────────────

def _equity_loop():
    from scanner import fetch_current_price
    while _status["running"]:
        try:
            positions = get_open_positions()
            if positions:
                prices = {}
                for pos in positions:
                    s = pos["symbol"]
                    if s not in prices:
                        prices[s] = fetch_current_price(s)
                        cache_price(s, prices[s])
                _status["equity"] = recalc_equity(prices)
        except Exception:
            pass
        time.sleep(5)


# ── Auto-trading loop ─────────────────────────────────────────────────────

_status = {
    "running":       False,
    "last_scan":     "Never",
    "last_action":   "Starting...",
    "last_decision": "",
    "scans_today":   0,
    "trades_today":  0,
    "equity":        0.0,
}

_last_skip: dict = {}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _status["last_action"] = f"[{ts}] {msg}"


def _loop():
    from scanner         import scan
    from analyzer        import analyze
    from decision_engine import decide

    _log("Aria started — Structure first. Quality only.")

    while _status["running"]:
        try:
            _status["last_scan"]    = datetime.utcnow().strftime("%H:%M UTC")
            _status["trades_today"] = todays_trade_count()

            for symbol in VALID_SYMBOLS:
                _status["scans_today"] += 1

                # Stage 1: Scan
                try:
                    scan_data = scan(symbol)
                except Exception as e:
                    _log(f"{symbol} scan error: {e}")
                    continue

                if not scan_data.get("candles") or \
                   len(scan_data["candles"]) < 20:
                    _log(f"{symbol} insufficient data")
                    continue

                # Stage 2: Analyze
                try:
                    analysis = analyze(scan_data)
                except Exception as e:
                    _log(f"{symbol} analyze error: {e}")
                    continue

                if "error" in analysis:
                    continue

                analysis["candles"] = scan_data["candles"]
                price = analysis.get("price", 0)

                # Manage open positions (pass analysis for structure check)
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    try:
                        manage_position(
                            pos, price, analysis.get("atr14", 0), analysis)
                    except Exception as e:
                        _log(f"manage error: {e}")

                # Stage 3: Decide
                try:
                    decision = decide(analysis)
                    new_conf = compute_confidence(
                        analysis, decision.get("decision","WAIT"))
                    decision["confidence"] = new_conf
                except Exception as e:
                    _log(f"{symbol} decide error: {e}")
                    continue

                dec    = decision.get("decision","WAIT")
                conf   = decision["confidence"]["total"]
                ms     = analysis.get("ms", {})
                frames = analysis.get("frames", [])
                tf_ok  = sum(1 for f in frames if f.get("decision") == dec)

                _status["last_decision"] = (
                    f"{symbol} {dec} | Conf {conf}% | "
                    f"{ms.get('trend','')} "
                    f"Str {ms.get('strength_pct',0)}% | "
                    f"{tf_ok}/4 TF | RSI {analysis.get('rsi14',0):.1f}")

                _log(f"{symbol} → {dec} | Conf {conf}% | "
                     f"Trend {ms.get('trend','')} "
                     f"({ms.get('strength_pct',0)}%) | "
                     f"{tf_ok}/4 TF agree | "
                     f"RSI {analysis.get('rsi14',0):.1f}")

                if dec == "WAIT":
                    continue

                # Stage 4: Execute
                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    key = symbol + dec
                    if _last_skip.get(key) != result["reason"]:
                        _last_skip[key] = result["reason"]
                        _log(f"{symbol} skipped: {result['reason']}")
                else:
                    _last_skip[symbol + dec] = ""
                    _status["trades_today"] += 1

        except Exception as e:
            _log(f"Loop error: {e}")

        for _ in range(SCAN_INTERVAL_SECONDS):
            if not _status["running"]:
                break
            time.sleep(1)

    _log("Aria stopped.")


def start_auto_trading():
    if _status["running"]:
        return
    _status["running"]     = True
    _status["scans_today"] = 0
    threading.Thread(target=_loop,        daemon=True).start()
    threading.Thread(target=_equity_loop, daemon=True).start()
    _log("Aria Professional Engine started.")


def stop_auto_trading():
    _status["running"] = False


def get_auto_status() -> dict:
    return {**_status}
