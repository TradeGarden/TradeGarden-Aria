"""
executor.py — Stage 4: EXECUTE
================================
Fixes in this version:
  - Equity calculated from ALL open positions (not just one)
  - Auto SL/TP checks ALL open positions every scan
  - Confidence-based position sizing (scale in)
  - Per-symbol max 1 open position (BTC separate from ETH)
  - Max 2 total open positions, 6 trades per day
  - Every closed trade saved permanently to PostgreSQL
"""

import threading, time, uuid
from datetime import datetime, date
from database import (
    load_balance, save_balance,
    load_position, save_position,
    close_position_in_db, clear_position,
    save_closed_trade, append_trade, load_journal,
    get_open_positions, get_open_positions_count,
    update_equity, recalc_equity, cache_price,
)
from config import (
    RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, MAX_OPEN_POSITIONS, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
    BUY_CONDITIONS, SELL_CONDITIONS,
)


# ══════════════════════════════════════════════
#  CONFIDENCE-BASED POSITION SIZING
#  Small position on weak signals, full on strong
# ══════════════════════════════════════════════

def confidence_size_multiplier(confidence: int) -> float:
    """
    Scale position size based on confidence score.
    60–69% → 20% of normal size  (weak signal, test the water)
    70–84% → 50% of normal size  (moderate signal)
    85%+   → 100% of normal size (strong signal, full position)
    """
    if confidence >= 85: return 1.0    # Full position
    if confidence >= 70: return 0.5    # Half position
    return 0.2                          # Small test position


def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    """
    Risk-based position sizing scaled by confidence.
    base_risk = 1% of balance
    actual_risk = base_risk × confidence_multiplier
    size = actual_risk / distance_to_stop_loss
    """
    multiplier   = confidence_size_multiplier(confidence)
    base_risk    = round(balance * RISK_PER_TRADE_PCT / 100, 2)
    actual_risk  = round(base_risk * multiplier, 2)

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    size    = round(actual_risk / sl_dist, 6) if sl_dist > 0 else 0.0

    return {
        "risk_usd":   actual_risk,
        "base_risk":  base_risk,
        "size":       size,
        "stop_loss":  sl,
        "take_profit":tp,
        "rr":         rr,
        "multiplier": multiplier,
    }


# ══════════════════════════════════════════════
#  DAILY STATS
# ══════════════════════════════════════════════

def todays_trade_count() -> int:
    today = date.today().isoformat()
    return sum(
        1 for t in load_journal()
        if t.get("action") == "OPEN"
        and t.get("opened_at", "")[:10] == today
    )


def todays_loss_pct(balance: float) -> float:
    from database import load_closed_trades
    today    = date.today().isoformat()
    closed   = [t for t in load_closed_trades(1) if t.get("closed_at","")[:10] == today]
    total_pl = sum(t.get("pl", 0) for t in closed)
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

    # Max total open positions
    total_open = get_open_positions_count()
    if total_open >= MAX_OPEN_POSITIONS:
        return {"approved": False,
                "reason": f"Max {MAX_OPEN_POSITIONS} open positions reached. Waiting for one to close."}

    # Max 1 open position per symbol
    open_positions = get_open_positions()
    sym_open = [p for p in open_positions if p["symbol"] == symbol]
    if sym_open:
        return {"approved": False,
                "reason": f"Already have an open {symbol} position. One per symbol at a time."}

    # Daily trade limit
    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily limit reached ({MAX_TRADES_PER_DAY} trades). Done for today."}

    # Daily loss limit
    daily_loss = todays_loss_pct(balance)
    if daily_loss >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit hit ({daily_loss:.1f}%). Trading paused for today."}

    # Market structure
    if ms["trend"] != cond["market_structure"]:
        return {"approved": False,
                "reason": f"Structure is {ms['trend']} ({ms['sequence']}). "
                          f"Need {cond['market_structure']} for {side}."}

    # Trend strength
    if ms["strength_pct"] < 30:
        return {"approved": False,
                "reason": f"Trend strength too weak ({ms['strength_pct']}%). Waiting."}

    # EMA alignment
    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY"  and not ema_bull:
        return {"approved": False, "reason": "EMA20 below EMA50. Trend not confirmed."}
    if side == "SELL" and not ema_bear:
        return {"approved": False, "reason": "EMA20 above EMA50. Trend not confirmed."}

    # RSI extremes
    r = analysis["rsi14"]
    if side == "BUY"  and r > 75:
        return {"approved": False, "reason": f"RSI overbought ({r}). Risky to buy."}
    if side == "SELL" and r < 25:
        return {"approved": False, "reason": f"RSI oversold ({r}). Risky to sell."}

    # Volume (relaxed to 45%)
    if side == "BUY"  and vol["buy_pressure"]  < 45:
        return {"approved": False,
                "reason": f"Volume strongly against BUY ({vol['sell_pressure']}% sellers). Waiting."}
    if side == "SELL" and vol["sell_pressure"] < 45:
        return {"approved": False,
                "reason": f"Volume strongly against SELL ({vol['buy_pressure']}% buyers). Waiting."}

    # R:R
    if rr < MIN_RISK_REWARD:
        return {"approved": False,
                "reason": f"R:R 1:{rr} below minimum 1:{MIN_RISK_REWARD}. Skipping."}

    # Minimum confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence {confidence}% below minimum {MIN_CONFIDENCE}%."}

    # At least 1 timeframe aligned
    frames   = analysis.get("frames", [])
    tf_match = sum(1 for f in frames if f["decision"] == side)
    if tf_match < 1:
        return {"approved": False,
                "reason": f"No timeframe confirms {side}. Waiting for alignment."}

    return {"approved": True, "reason": "All conditions met."}


# ══════════════════════════════════════════════
#  OPEN TRADE
# ══════════════════════════════════════════════

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]

    pos_calc = calc_position(balance, price, atr, side, confidence)
    rr       = pos_calc["rr"]

    check = check_all_rules(symbol, side, analysis, confidence, rr, balance)
    if not check["approved"]:
        append_trade({
            "action": "WAIT", "symbol": symbol, "side_considered": side,
            "reason": check["reason"], "price": price, "confidence": confidence,
            "session": analysis.get("session",""),
            "opened_at": datetime.utcnow().isoformat(),
        })
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
        "trail_sl":    False,
    }

    save_position(position)

    size_label = (
        "Full position" if pos_calc["multiplier"] == 1.0 else
        "Half position" if pos_calc["multiplier"] == 0.5 else
        "Scaled-in (20%)"
    )

    append_trade({
        "action": "OPEN", "trade_id": trade_id, "symbol": symbol, "side": side,
        "entry": price, "stop_loss": pos_calc["stop_loss"],
        "take_profit": pos_calc["take_profit"],
        "size": pos_calc["size"], "risk": pos_calc["risk_usd"],
        "base_risk": pos_calc["base_risk"],
        "size_label": size_label,
        "rr": rr, "confidence": confidence,
        "trend": decision["trend"],
        "structure": analysis["ms"]["structure"],
        "sequence":  analysis["ms"]["sequence"],
        "ema20": analysis["ema20"], "ema50": analysis["ema50"],
        "rsi": analysis["rsi14"], "rsi_label": analysis["rsi_label"],
        "volume_label":  analysis["vol"]["label"],
        "buy_pressure":  analysis["vol"]["buy_pressure"],
        "sell_pressure": analysis["vol"]["sell_pressure"],
        "patterns":  [p["name"] for p in analysis["patterns"]],
        "confidence_breakdown": decision["confidence"]["breakdown"],
        "reasoning": decision["reasons"],
        "session":   analysis.get("session",""),
        "opened_at": datetime.utcnow().isoformat(),
    })

    _log(f"✅ OPENED #{trade_id} — {side} {pos_calc['size']} {symbol} "
         f"@ ${price:,.2f} | {size_label} | Conf {confidence}% | R:R 1:{rr}")
    return {"success": True, "position": position}


# ══════════════════════════════════════════════
#  CLOSE TRADE
# ══════════════════════════════════════════════

def close_trade(position: dict, current_price: float, reason: str = "Manual") -> dict:
    entry = position["entry_price"]
    size  = position["size"]
    side  = position["side"]

    pl = (current_price - entry) * size if side == "BUY" else (entry - current_price) * size
    pl = round(pl, 2)

    balance     = load_balance()
    new_balance = round(balance + pl, 2)

    save_balance(new_balance)
    close_position_in_db(position["trade_id"])

    duration = ""
    try:
        opened   = datetime.fromisoformat(str(position["opened_at"]))
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        duration = f"{h}h {rem//60}m"
    except Exception:
        pass

    save_closed_trade({
        "trade_id":    position.get("trade_id",""),
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
        "opened_at":   str(position.get("opened_at","")),
    })

    append_trade({
        "action": "CLOSE", "trade_id": position.get("trade_id",""),
        "symbol": position["symbol"], "side": side,
        "entry": entry, "exit": current_price,
        "stop_loss": position["stop_loss"], "take_profit": position["take_profit"],
        "size": size, "risk": position.get("risk_amount",0),
        "pl": pl, "new_balance": new_balance,
        "duration": duration, "exit_reason": reason,
        "closed_at": datetime.utcnow().isoformat(),
    })

    emoji = "🟢" if pl >= 0 else "🔴"
    _log(f"{emoji} CLOSED #{position.get('trade_id','')} — "
         f"P/L ${pl:,.2f} | New Balance ${new_balance:,.2f} | {reason}")
    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ══════════════════════════════════════════════
#  SL / TP / BREAK-EVEN CHECK
# ══════════════════════════════════════════════

def check_position(position: dict, current_price: float, atr: float) -> bool:
    """
    Returns True if position was closed (SL/TP hit).
    Also moves SL to break-even when profit >= 1 ATR.
    """
    sl   = position["stop_loss"]
    tp   = position["take_profit"]
    side = position["side"]

    if side == "BUY":
        if current_price <= sl:
            close_trade(position, current_price, "Stop Loss hit")
            return True
        if current_price >= tp:
            close_trade(position, current_price, "Take Profit reached")
            return True
    else:
        if current_price >= sl:
            close_trade(position, current_price, "Stop Loss hit")
            return True
        if current_price <= tp:
            close_trade(position, current_price, "Take Profit reached")
            return True

    # Break-even: move SL to entry when profit >= 1 ATR
    if atr > 0 and not position.get("be_moved"):
        entry  = position["entry_price"]
        profit = (current_price-entry) if side=="BUY" else (entry-current_price)
        if profit >= atr:
            position["stop_loss"] = entry
            position["be_moved"]  = True
            save_position(position)
            append_trade({
                "action": "SL_MOVED_BE",
                "trade_id": position.get("trade_id",""),
                "symbol": position["symbol"],
                "new_sl": entry,
                "timestamp": datetime.utcnow().isoformat(),
            })
            _log(f"SL → Break-Even @ ${entry:,.2f} for #{position.get('trade_id','')}")

    return False


# ══════════════════════════════════════════════
#  EQUITY UPDATER — runs every 5 seconds
# ══════════════════════════════════════════════

def _equity_loop():
    """
    Recalculates equity from ALL open positions every 5 seconds.
    Equity = balance + sum of all floating P/L.
    """
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
                equity = recalc_equity(prices)   # sums ALL positions
                _auto_status["equity"] = equity
        except Exception as e:
            pass
        time.sleep(5)


# ══════════════════════════════════════════════
#  AUTO-TRADING LOOP — runs every 60 seconds
# ══════════════════════════════════════════════

_auto_status = {
    "running":       False,
    "last_scan":     "Never",
    "last_action":   "Waiting for first scan...",
    "last_decision": "",
    "scans_today":   0,
    "equity":        0.0,
}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _auto_status["last_action"] = f"[{ts}] {msg}"


def _auto_loop():
    from scanner     import scan, fetch_current_price
    from analyzer    import analyze
    from decision_engine import decide

    _log("Auto-trading started — scanning BTC + ETH every 60s")

    while _auto_status["running"]:
        try:
            for symbol in VALID_SYMBOLS:
                _auto_status["last_scan"]    = datetime.utcnow().strftime("%H:%M:%S UTC")
                _auto_status["scans_today"] += 1

                # Stage 1: Scan
                scan_data = scan(symbol)
                if not scan_data["candles"]:
                    _log(f"{symbol} — No candle data"); continue

                # Stage 2: Analyze
                analysis = analyze(scan_data)
                if "error" in analysis:
                    _log(f"{symbol} — Analysis error"); continue

                # Stage 3: Decide
                decision = decide(analysis)
                dec      = decision["decision"]
                conf     = decision["confidence"]["total"]

                _auto_status["last_decision"] = (
                    f"{symbol} → {dec} | Conf {conf}% | "
                    f"{analysis['ms']['trend']} {analysis['ms']['sequence']}"
                )
                _log(f"{symbol} — {dec} | Conf {conf}% | "
                     f"{analysis['ms']['trend']} {analysis['ms']['sequence']}")

                # Check ALL open positions for this symbol (SL/TP/BE)
                open_positions = get_open_positions()
                current_price  = fetch_current_price(symbol)

                for pos in open_positions:
                    if pos["symbol"] == symbol:
                        check_position(pos, current_price, analysis["atr14"])

                # Stage 4: Execute if decision is BUY or SELL
                if dec == "WAIT":
                    continue

                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    _log(f"{symbol} — Rejected: {result['reason']}")

        except Exception as e:
            _log(f"Loop error: {e}")

        # Wait 60 seconds
        for _ in range(60):
            if not _auto_status["running"]: break
            time.sleep(1)

    _log("Auto-trading stopped.")


# ══════════════════════════════════════════════
#  START / STOP
# ══════════════════════════════════════════════

def start_auto_trading():
    if _auto_status["running"]:
        return
    _auto_status["running"]     = True
    _auto_status["scans_today"] = 0
    threading.Thread(target=_auto_loop,   daemon=True).start()
    threading.Thread(target=_equity_loop, daemon=True).start()
    _log("Auto-trading + equity updater started.")


def stop_auto_trading():
    _auto_status["running"] = False


def get_auto_status() -> dict:
    return {**_auto_status}
