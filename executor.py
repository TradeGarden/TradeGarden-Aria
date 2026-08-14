"""
executor.py - Aria Scalper + Trend Runner
============================================
Modes:
  FAST_SCALPER - Normal market conditions. Quick entries/exits.
  TREND_RUNNER - Exceptional momentum detected. Trail and let it run.

Position State Machine:
  ENTRY -> INITIAL_RISK -> BREAK_EVEN -> LOCK_PROFIT -> TRAILING -> EXIT

Smart trailing:
  Only moves SL on meaningful structure (new HH/HL confirmed).
  Never on every price tick. Never loosens an existing stop.

Momentum failure exit:
  Closes trade when the setup is no longer valid.
  Does NOT hold stagnant trades indefinitely.

Time-based exit with intelligence:
  Checks momentum before closing due to time.
  If trend is exceptional, continues holding.
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
    RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
    BUY_CONDITIONS, SELL_CONDITIONS,
    SCALPER_BREAKEVEN_USD, SCALPER_PARTIAL_TP_USD,
    SCALPER_TRAIL_USD, SCALPER_TIMEOUT_HOURS, SCALPER_MIN_PROFIT_USD,
    TREND_MIN_CONFIDENCE, TREND_MIN_STRENGTH, TREND_TRAIL_ATR_MULT,
)

# ── Instrument specs (BTC and ETH are different) ──────────────────────────
INSTRUMENT = {
    "BTCUSD": {
        "min_size":  0.0001,
        "size_step": 0.0001,
        "tick_size": 0.01,
        "label":     "BTC",
    },
    "ETHUSD": {
        "min_size":  0.001,
        "size_step": 0.001,
        "tick_size": 0.01,
        "label":     "ETH",
    },
}

# Position state machine stages
STATES = ["ENTRY", "INITIAL_RISK", "BREAK_EVEN",
          "LOCK_PROFIT", "TRAILING", "PARTIAL_PROFIT", "EXIT"]


# ══════════════════════════════════════════════
#  MODE DETECTION
#  TREND_RUNNER requires exceptional conditions
#  Not just high confidence
# ══════════════════════════════════════════════

def detect_mode(analysis: dict, confidence: int) -> str:
    """
    TREND_RUNNER only when ALL of these align:
      - Confidence >= 85%
      - Trend strength >= 70%
      - EMA strongly aligned (gap >= 0.3% of price)
      - Volume expanding (relative >= 1.3)
      - RSI showing momentum (not extreme)
      - BOS confirmed
    Otherwise: FAST_SCALPER
    """
    ms      = analysis["ms"]
    vol     = analysis["vol"]
    e20     = analysis["ema20"]
    e50     = analysis["ema50"]
    r       = analysis["rsi14"]
    price   = analysis["price"]

    if confidence < TREND_MIN_CONFIDENCE:
        return "FAST_SCALPER"
    if ms["strength_pct"] < TREND_MIN_STRENGTH:
        return "FAST_SCALPER"

    # EMA gap must be significant — at least 0.3% of price
    ema_gap_pct = abs(e20 - e50) / price * 100
    if ema_gap_pct < 0.3:
        return "FAST_SCALPER"

    # Volume must be expanding
    if vol["relative"] < 1.3:
        return "FAST_SCALPER"

    # RSI in momentum zone (not overbought/oversold extremes)
    if r > 80 or r < 20:
        return "FAST_SCALPER"

    # BOS adds conviction
    if not ms.get("bos"):
        return "FAST_SCALPER"

    return "TREND_RUNNER"


# ══════════════════════════════════════════════
#  CANDLE STRENGTH (replaces named pattern)
# ══════════════════════════════════════════════

def candle_strength_score(candles: list, ema20: float, avg_volume: float) -> int:
    if len(candles) < 2:
        return 0
    c, prev = candles[-1], candles[-2]
    body = abs(c["close"] - c["open"])
    rng  = c["high"] - c["low"]
    if rng == 0:
        return 0
    score = 0
    # Strong body
    if body / rng > 0.6:
        score += 5
    # Breakout above/below previous candle
    if c["close"] > prev["high"] or c["close"] < prev["low"]:
        score += 5
    # Closed on correct side of EMA20
    if (c["close"] > ema20 and c["close"] > c["open"]) or \
       (c["close"] < ema20 and c["close"] < c["open"]):
        score += 5
    # Volume spike
    if avg_volume > 0 and c["volume"] > avg_volume * 1.5:
        score += 5
    return min(score, 20)


# ══════════════════════════════════════════════
#  CONFIDENCE SCORING
# ══════════════════════════════════════════════

def compute_confidence(analysis: dict, decision: str) -> dict:
    scores = {
        "Market Structure": 0,
        "EMA Alignment":    0,
        "RSI":              0,
        "Candle Strength":  0,
        "Volume":           0,
    }
    ms  = analysis["ms"]
    e20 = analysis["ema20"]
    e50 = analysis["ema50"]
    r   = analysis["rsi14"]
    vol = analysis["vol"]

    direction = decision if decision in ("BUY", "SELL") else (
        "BUY" if ms["trend"] == "Bullish" else "SELL")
    is_buy  = direction == "BUY"
    is_sell = direction == "SELL"

    # Market Structure (25pts)
    if (is_buy and ms["trend"] == "Bullish") or (is_sell and ms["trend"] == "Bearish"):
        scores["Market Structure"] = 25
    elif ms["trend"] in ("Bullish", "Bearish"):
        scores["Market Structure"] = 10

    # EMA Alignment (25pts)
    if (is_buy and e20 > e50) or (is_sell and e20 < e50):
        scores["EMA Alignment"] = 25

    # RSI (15pts)
    if is_buy:
        if 40 < r < 70:    scores["RSI"] = 15
        elif r <= 40:       scores["RSI"] = 10
        elif 70 <= r < 80: scores["RSI"] = 5
    else:
        if 30 < r < 60:    scores["RSI"] = 15
        elif r >= 60:       scores["RSI"] = 10
        elif 20 < r <= 30: scores["RSI"] = 5

    # Candle Strength (20pts)
    candles = analysis.get("candles", [])
    avg_vol = vol.get("avg20", 0)
    scores["Candle Strength"] = candle_strength_score(candles, e20, avg_vol)

    # Volume (15pts)
    if is_buy:
        if vol["buy_pressure"]  > 55: scores["Volume"] = 15
        elif vol["buy_pressure"] > 45: scores["Volume"] = 8
    else:
        if vol["sell_pressure"] > 55: scores["Volume"] = 15
        elif vol["sell_pressure"] > 45: scores["Volume"] = 8

    return {"breakdown": scores, "total": sum(scores.values())}


# ══════════════════════════════════════════════
#  POSITION SIZING (per instrument)
# ══════════════════════════════════════════════

def confidence_multiplier(confidence: int) -> tuple:
    if confidence >= 90: return 1.0, "Full (90%+)"
    if confidence >= 70: return 0.5, "Half (70-89%)"
    return 0.2, "Test 20% (60-69%)"


def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int, symbol: str) -> dict:
    """
    Risk Amount = Equity x Risk%
    Size = Risk Amount / (SL distance in price)
    Validated against per-instrument minimums.
    """
    spec       = INSTRUMENT.get(symbol, INSTRUMENT["BTCUSD"])
    mult, label= confidence_multiplier(confidence)
    base_risk  = round(balance * RISK_PER_TRADE_PCT / 100, 2)
    actual_risk= round(base_risk * mult, 2)

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    # Raw size from risk
    raw_size = actual_risk / sl_dist if sl_dist > 0 else 0.0

    # Round to instrument step size
    step = spec["size_step"]
    size = round(round(raw_size / step) * step, 8)

    # Enforce minimum
    if size < spec["min_size"]:
        size = spec["min_size"]

    return {
        "risk_usd":   round(size * sl_dist, 2),
        "base_risk":  base_risk,
        "size":       size,
        "stop_loss":  sl,
        "take_profit":tp,
        "rr":         rr,
        "multiplier": mult,
        "size_label": label,
    }


# ══════════════════════════════════════════════
#  DAILY STATS
# ══════════════════════════════════════════════

def todays_trade_count() -> int:
    today = date.today().isoformat()
    return sum(1 for t in load_journal()
               if t.get("action") == "OPEN"
               and t.get("opened_at", "")[:10] == today)


def todays_loss_pct(balance: float) -> float:
    from database import load_closed_trades
    today    = date.today().isoformat()
    closed   = [t for t in load_closed_trades(1)
                if t.get("closed_at", "")[:10] == today]
    total_pl = sum(float(t.get("pl", 0)) for t in closed)
    if total_pl >= 0 or balance <= 0:
        return 0.0
    return round(abs(total_pl) / balance * 100, 2)


# ══════════════════════════════════════════════
#  RULE ENFORCEMENT
# ══════════════════════════════════════════════

def check_all_rules(symbol: str, side: str, analysis: dict,
                    confidence: int, rr: float, balance: float) -> dict:
    ms   = analysis["ms"]
    vol  = analysis["vol"]
    cond = BUY_CONDITIONS if side == "BUY" else SELL_CONDITIONS

    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False,
                "reason": f"Max {MAX_OPEN_POSITIONS} positions open."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily limit {MAX_TRADES_PER_DAY} reached."}

    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit {DAILY_LOSS_LIMIT_PCT}% hit."}

    # One position per symbol per direction
    open_pos = get_open_positions()
    if any(p["symbol"] == symbol and p["side"] == side for p in open_pos):
        return {"approved": False,
                "reason": f"Already have {symbol} {side} open."}

    if ms["trend"] != cond["market_structure"]:
        return {"approved": False,
                "reason": f"Structure {ms['trend']} ({ms['sequence']}). "
                          f"Need {cond['market_structure']}."}

    if ms["strength_pct"] < 25:
        return {"approved": False,
                "reason": f"Trend too weak ({ms['strength_pct']}%)."}

    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY"  and not ema_bull:
        return {"approved": False, "reason": "EMA20 below EMA50."}
    if side == "SELL" and not ema_bear:
        return {"approved": False, "reason": "EMA20 above EMA50."}

    r = analysis["rsi14"]
    if side == "BUY"  and r > 78:
        return {"approved": False, "reason": f"RSI overbought ({r})."}
    if side == "SELL" and r < 22:
        return {"approved": False, "reason": f"RSI oversold ({r})."}

    if side == "BUY"  and vol["buy_pressure"]  < 42:
        return {"approved": False, "reason": "Volume against BUY."}
    if side == "SELL" and vol["sell_pressure"] < 42:
        return {"approved": False, "reason": "Volume against SELL."}

    if rr < MIN_RISK_REWARD:
        return {"approved": False,
                "reason": f"R:R 1:{rr} < min 1:{MIN_RISK_REWARD}."}

    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence {confidence}% < min {MIN_CONFIDENCE}%."}

    frames   = analysis.get("frames", [])
    tf_match = sum(1 for f in frames if f["decision"] == side)
    if tf_match < 1:
        return {"approved": False,
                "reason": f"No timeframe confirms {side}."}

    return {"approved": True, "reason": "All conditions met."}


# ══════════════════════════════════════════════
#  MOMENTUM CHECK (for exits)
# ══════════════════════════════════════════════

def momentum_still_valid(position: dict, analysis: dict) -> bool:
    """
    Returns True if the trade setup is still valid.
    Used before momentum-failure exit and time-based exit.
    """
    side = position["side"]
    ms   = analysis["ms"]
    e20  = analysis["ema20"]
    e50  = analysis["ema50"]
    r    = analysis["rsi14"]
    vol  = analysis["vol"]

    # Structure must still match
    if side == "BUY"  and ms["trend"] != "Bullish":
        return False
    if side == "SELL" and ms["trend"] != "Bearish":
        return False

    # EMA must still be aligned
    if side == "BUY"  and e20 < e50:
        return False
    if side == "SELL" and e20 > e50:
        return False

    # RSI must not have reversed to extreme against us
    if side == "BUY"  and r > 80:
        return False
    if side == "SELL" and r < 20:
        return False

    # Volume must not have collapsed
    if vol["relative"] < 0.5:
        return False

    return True


# ══════════════════════════════════════════════
#  SMART TRAILING (structure-based, not tick-based)
# ══════════════════════════════════════════════

def smart_trail(position: dict, current_price: float,
                atr: float, candles: list) -> dict:
    """
    Only moves SL when meaningful structure is confirmed.
    Never loosens an existing stop.
    Uses recent swing lows/highs from last 5 candles.
    """
    side    = position["side"]
    mode    = position.get("mode", "FAST_SCALPER")
    current_sl = position["stop_loss"]

    # Trail multiplier depends on mode
    trail_atr = TREND_TRAIL_ATR_MULT if mode == "TREND_RUNNER" else 0.5

    if side == "BUY":
        # Find the lowest low of the last 3-5 candles as structural support
        recent_lows = [c["low"] for c in candles[-5:]] if len(candles) >= 5 \
                      else [c["low"] for c in candles]
        swing_low   = min(recent_lows)
        # Proposed new SL: below the swing low, with ATR buffer
        new_sl = round(swing_low - atr * trail_atr * 0.3, 2)
        # Only move if it improves (higher than current SL)
        if new_sl > current_sl and new_sl < current_price:
            position["stop_loss"] = new_sl
            position["trail_sl"]  = True
            position["state"]     = "TRAILING"
            save_position(position)
            _log(f"[TRAIL] SL moved up to ${new_sl:,.2f} "
                 f"(swing low ${swing_low:,.2f}) "
                 f"#{position.get('trade_id','')}")

    else:  # SELL
        recent_highs = [c["high"] for c in candles[-5:]] if len(candles) >= 5 \
                       else [c["high"] for c in candles]
        swing_high   = max(recent_highs)
        new_sl = round(swing_high + atr * trail_atr * 0.3, 2)
        if new_sl < current_sl and new_sl > current_price:
            position["stop_loss"] = new_sl
            position["trail_sl"]  = True
            position["state"]     = "TRAILING"
            save_position(position)
            _log(f"[TRAIL] SL moved down to ${new_sl:,.2f} "
                 f"(swing high ${swing_high:,.2f}) "
                 f"#{position.get('trade_id','')}")

    return position


# ══════════════════════════════════════════════
#  OPEN TRADE
# ══════════════════════════════════════════════

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]
    pos_calc   = calc_position(balance, price, atr, side, confidence, symbol)
    rr         = pos_calc["rr"]
    mode       = detect_mode(analysis, confidence)

    check = check_all_rules(symbol, side, analysis, confidence, rr, balance)
    if not check["approved"]:
        return {"success": False, "reason": check["reason"]}

    trade_id = str(uuid.uuid4())[:8].upper()
    position = {
        "trade_id":       trade_id,
        "symbol":         symbol,
        "side":           side,
        "entry_price":    price,
        "size":           pos_calc["size"],
        "risk_amount":    pos_calc["risk_usd"],
        "stop_loss":      pos_calc["stop_loss"],
        "initial_sl":     pos_calc["stop_loss"],   # never changes
        "take_profit":    pos_calc["take_profit"],
        "rr":             rr,
        "mode":           mode,
        "state":          "ENTRY",
        "opened_at":      datetime.utcnow().isoformat(),
        "status":         "OPEN",
        "be_moved":       False,
        "partial_closed": False,
        "trail_sl":       False,
        "atr_at_open":    atr,
        "locked_profit":  0.0,
    }

    save_position(position)

    append_trade({
        "action":      "OPEN",
        "trade_id":    trade_id,
        "symbol":      symbol,
        "side":        side,
        "entry":       price,
        "stop_loss":   pos_calc["stop_loss"],
        "take_profit": pos_calc["take_profit"],
        "size":        pos_calc["size"],
        "risk":        pos_calc["risk_usd"],
        "size_label":  pos_calc["size_label"],
        "rr":          rr,
        "mode":        mode,
        "confidence":  confidence,
        "trend":       decision["trend"],
        "structure":   analysis["ms"]["structure"],
        "sequence":    analysis["ms"]["sequence"],
        "ema20":       analysis["ema20"],
        "ema50":       analysis["ema50"],
        "rsi":         analysis["rsi14"],
        "rsi_label":   analysis["rsi_label"],
        "volume_label":analysis["vol"]["label"],
        "patterns":    [p["name"] for p in analysis.get("patterns", [])],
        "confidence_breakdown": decision["confidence"]["breakdown"],
        "reasoning":   decision["reasons"],
        "session":     analysis.get("session", ""),
        "opened_at":   datetime.utcnow().isoformat(),
    })

    _log(f"[{mode}] OPENED #{trade_id} - {side} {pos_calc['size']} "
         f"{symbol} @ ${price:,.2f} | {pos_calc['size_label']} "
         f"| SL ${pos_calc['stop_loss']:,.2f} | Conf {confidence}%")
    return {"success": True, "position": position}


# ══════════════════════════════════════════════
#  CLOSE TRADE
# ══════════════════════════════════════════════

def close_trade(position: dict, current_price: float,
                reason: str = "Manual", partial: float = 1.0) -> dict:
    entry = position["entry_price"]
    side  = position["side"]
    size  = round(position["size"] * partial, 8)
    spec  = INSTRUMENT.get(position["symbol"], INSTRUMENT["BTCUSD"])

    # Don't partial-close if remaining would be below minimum
    if partial < 1.0:
        remaining = round(position["size"] - size, 8)
        if remaining < spec["min_size"]:
            partial = 1.0
            size    = position["size"]
            reason += " (full close - remaining too small)"

    pl          = round(((current_price - entry) * size if side == "BUY"
                         else (entry - current_price) * size), 2)
    balance     = load_balance()
    new_balance = round(balance + pl, 2)

    save_balance(new_balance)

    if partial >= 1.0:
        close_position_in_db(position["trade_id"])
        position["state"] = "EXIT"
    else:
        position["size"]           = round(position["size"] - size, 8)
        position["partial_closed"] = True
        position["locked_profit"]  = round(
            position.get("locked_profit", 0) + pl, 2)
        position["state"]          = "PARTIAL_PROFIT"
        save_position(position)

    duration = ""
    try:
        opened   = datetime.fromisoformat(
            str(position["opened_at"]).replace(" ", "T")[:19])
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        duration = f"{h}h {rem // 60}m"
    except Exception:
        pass

    if partial >= 1.0:
        save_closed_trade({
            "trade_id":    position.get("trade_id", ""),
            "symbol":      position["symbol"],
            "side":        side,
            "entry":       entry,
            "exit":        current_price,
            "stop_loss":   position.get("initial_sl", position["stop_loss"]),
            "take_profit": position["take_profit"],
            "size":        size,
            "risk":        position.get("risk_amount", 0),
            "pl":          pl,
            "new_balance": new_balance,
            "duration":    duration,
            "exit_reason": reason,
            "mode":        position.get("mode", "FAST_SCALPER"),
            "opened_at":   str(position.get("opened_at", "")),
        })

    append_trade({
        "action":      "CLOSE" if partial >= 1.0 else "PARTIAL_TP",
        "trade_id":    position.get("trade_id", ""),
        "symbol":      position["symbol"],
        "side":        side,
        "entry":       entry,
        "exit":        current_price,
        "size":        size,
        "partial":     partial,
        "pl":          pl,
        "new_balance": new_balance,
        "duration":    duration,
        "exit_reason": reason,
        "closed_at":   datetime.utcnow().isoformat(),
    })

    emoji = "🟢" if pl >= 0 else "🔴"
    label = f"PARTIAL {int(partial*100)}%" if partial < 1.0 else "CLOSED"
    _log(f"{emoji} {label} #{position.get('trade_id', '')} - "
         f"P/L ${pl:,.2f} | Balance ${new_balance:,.2f} | {reason}")
    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ══════════════════════════════════════════════
#  SMART POSITION MANAGEMENT (state machine)
# ══════════════════════════════════════════════

def manage_position(position: dict, current_price: float,
                    atr: float, analysis: dict, candles: list) -> bool:
    """
    Full position lifecycle using state machine.
    Returns True if position was fully closed.

    States:
      ENTRY -> INITIAL_RISK -> BREAK_EVEN -> LOCK_PROFIT
      -> TRAILING -> PARTIAL_PROFIT -> EXIT
    """
    entry  = position["entry_price"]
    side   = position["side"]
    size   = position["size"]
    sl     = position["stop_loss"]
    tp     = position["take_profit"]
    mode   = position.get("mode", "FAST_SCALPER")
    state  = position.get("state", "INITIAL_RISK")

    fl_pl  = round(((current_price - entry) * size if side == "BUY"
                    else (entry - current_price) * size), 2)

    # ── Hard SL/TP (always checked first) ─────────────────────────
    if side == "BUY":
        if current_price <= sl:
            close_trade(position, current_price, "Stop Loss hit")
            return True
        if current_price >= tp and mode == "FAST_SCALPER":
            close_trade(position, current_price, "Take Profit reached")
            return True
    else:
        if current_price >= sl:
            close_trade(position, current_price, "Stop Loss hit")
            return True
        if current_price <= tp and mode == "FAST_SCALPER":
            close_trade(position, current_price, "Take Profit reached")
            return True

    # ── Momentum failure exit ──────────────────────────────────────
    # Only check after trade has had some time to develop
    try:
        opened     = datetime.fromisoformat(
            str(position["opened_at"]).replace(" ", "T")[:19])
        mins_open  = (datetime.utcnow() - opened).total_seconds() / 60
        hours_open = mins_open / 60
    except Exception:
        mins_open  = 0
        hours_open = 0

    if mins_open > 30 and not momentum_still_valid(position, analysis):
        if fl_pl > 0:
            close_trade(position, current_price,
                        f"Momentum failure exit (+${fl_pl:.2f} profit)")
        elif fl_pl > -position.get("risk_amount", 5) * 0.5:
            # Only exit on momentum failure if loss is less than 50% of risk
            close_trade(position, current_price,
                        f"Momentum failure exit (${fl_pl:.2f})")
            return True
        # If at full loss, let SL handle it
        return False if fl_pl <= -position.get("risk_amount", 5) * 0.5 else True

    # ── Time-based exit (intelligent) ─────────────────────────────
    timeout = SCALPER_TIMEOUT_HOURS
    if mode == "TREND_RUNNER":
        timeout = timeout * 3   # much longer for trend runner

    if hours_open >= timeout:
        still_valid = momentum_still_valid(position, analysis)
        trend_strong = analysis["ms"]["strength_pct"] >= 60
        good_profit  = fl_pl >= SCALPER_MIN_PROFIT_USD * 2

        if still_valid and trend_strong and good_profit and mode == "TREND_RUNNER":
            # Exceptional trend - keep running, log the decision
            _log(f"[TREND_RUNNER] Holding past timeout - "
                 f"trend strong {analysis['ms']['strength_pct']}%, "
                 f"profit ${fl_pl:.2f} #{position.get('trade_id','')}")
        elif fl_pl < SCALPER_MIN_PROFIT_USD:
            close_trade(position, current_price,
                        f"Timeout {hours_open:.1f}h, profit ${fl_pl:.2f}")
            return True

    # ── State: INITIAL_RISK -> BREAK_EVEN ────────────────────────
    if not position.get("be_moved") and fl_pl >= SCALPER_BREAKEVEN_USD:
        # Move SL to break-even (slightly above entry to cover costs)
        be_price = round(entry + 0.01, 2) if side == "BUY" \
                   else round(entry - 0.01, 2)

        # NEVER loosen the stop
        if (side == "BUY"  and be_price > position["stop_loss"]) or \
           (side == "SELL" and be_price < position["stop_loss"]):
            position["stop_loss"] = be_price
            position["be_moved"]  = True
            position["state"]     = "BREAK_EVEN"
            save_position(position)
            append_trade({
                "action":    "SL_MOVED_BE",
                "trade_id":  position.get("trade_id", ""),
                "symbol":    position["symbol"],
                "new_sl":    be_price,
                "profit_at": fl_pl,
                "timestamp": datetime.utcnow().isoformat(),
            })
            _log(f"[{mode}] Break-even @ ${be_price:,.2f} "
                 f"(profit ${fl_pl:.2f}) #{position.get('trade_id','')}")

    # ── State: BREAK_EVEN -> LOCK_PROFIT (Partial TP) ────────────
    if not position.get("partial_closed") and \
            fl_pl >= SCALPER_PARTIAL_TP_USD and \
            position.get("be_moved"):
        position["state"] = "LOCK_PROFIT"
        save_position(position)
        close_trade(position, current_price,
                    f"Partial TP at +${fl_pl:.2f}", partial=0.5)
        _log(f"[{mode}] Partial TP 50% @ ${current_price:,.2f} "
             f"(profit ${fl_pl:.2f})")

    # ── State: LOCK_PROFIT -> TRAILING ───────────────────────────
    if position.get("partial_closed") and \
            fl_pl >= SCALPER_TRAIL_USD and \
            atr > 0 and len(candles) >= 5:
        position = smart_trail(position, current_price, atr, candles)

    # TREND_RUNNER: always trail after break-even (no partial TP required)
    if mode == "TREND_RUNNER" and \
            position.get("be_moved") and \
            atr > 0 and len(candles) >= 5:
        position = smart_trail(position, current_price, atr, candles)

    return False


# ══════════════════════════════════════════════
#  EQUITY UPDATER (every 5s)
# ══════════════════════════════════════════════

def _equity_loop():
    from scanner import fetch_current_price
    while _auto_status["running"]:
        try:
            positions = get_open_positions()
            if positions:
                prices = {}
                for pos in positions:
                    sym = pos["symbol"]
                    if sym not in prices:
                        prices[sym] = fetch_current_price(sym)
                        cache_price(sym, prices[sym])
                _auto_status["equity"] = recalc_equity(prices)
        except Exception:
            pass
        time.sleep(5)


# ══════════════════════════════════════════════
#  AUTO-TRADING LOOP (every 60s)
# ══════════════════════════════════════════════

_auto_status = {
    "running":       False,
    "last_scan":     "Never",
    "last_action":   "Waiting...",
    "last_decision": "",
    "scans_today":   0,
    "equity":        0.0,
}
_last_reason: dict = {}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _auto_status["last_action"] = f"[{ts}] {msg}"


def _auto_loop():
    from scanner         import scan, fetch_current_price
    from analyzer        import analyze
    from decision_engine import decide

    _log("Aria started - scanning BTC + ETH every 5 minutes")

    while _auto_status["running"]:
        try:
            _auto_status["last_scan"] = datetime.utcnow().strftime("%H:%M UTC")

            for symbol in VALID_SYMBOLS:
                _auto_status["scans_today"] += 1

                # Stage 1: Scan (Binance primary, Kraken fallback)
                scan_data = scan(symbol)
                if not scan_data["candles"]:
                    _log(f"{symbol} - No candle data from Binance or Kraken")
                    continue

                # Stage 2: Analyze
                analysis = analyze(scan_data)
                if "error" in analysis:
                    continue
                analysis["candles"] = scan_data["candles"]
                candles       = scan_data["candles"]
                current_price = analysis["price"]
                _log(f"{symbol} - Price ${current_price:,.2f} | "
                     f"Source: {scan_data.get('source','?')} | "
                     f"Candles: {len(candles)}")

                # Manage all open positions
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    manage_position(pos, current_price,
                                    analysis["atr14"], analysis, candles)

                # Stage 3: Decide + override confidence with v2 scorer
                decision    = decide(analysis)
                new_conf    = compute_confidence(analysis, decision["decision"])
                decision["confidence"] = new_conf

                dec  = decision["decision"]
                conf = decision["confidence"]["total"]
                mode = detect_mode(analysis, conf)

                _auto_status["last_decision"] = (
                    f"{symbol} [{mode}] -> {dec} | Conf {conf}% | "
                    f"{analysis['ms']['trend']} {analysis['ms']['sequence']}")

                # Stage 4: Execute
                if dec == "WAIT":
                    continue

                _log(f"{symbol} [{mode}] {dec} | Conf {conf}% | "
                     f"{analysis['ms']['trend']}")

                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    key = f"{symbol}_rej"
                    if _last_reason.get(key) != result["reason"]:
                        _last_reason[key] = result["reason"]
                        _log(f"{symbol} - Skipped: {result['reason']}")
                else:
                    _last_reason[f"{symbol}_rej"] = ""

        except Exception as e:
            _log(f"Error: {e}")

        # Scan every 5 minutes for real scalping timeframes
        for _ in range(300):
            if not _auto_status["running"]:
                break
            time.sleep(1)

    _log("Auto-trading stopped.")


def start_auto_trading():
    if _auto_status["running"]:
        return
    _auto_status["running"]     = True
    _auto_status["scans_today"] = 0
    threading.Thread(target=_auto_loop,   daemon=True).start()
    threading.Thread(target=_equity_loop, daemon=True).start()
    _log("Aria v2 Scalper + Trend Runner started.")


def stop_auto_trading():
    _auto_status["running"] = False


def get_auto_status() -> dict:
    return {**_auto_status}
