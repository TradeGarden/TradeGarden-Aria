"""
executor.py — Stage 4: EXECUTE
================================
All state now persisted to PostgreSQL via database.py.
Balance and positions survive restarts, sleep cycles, and redeploys.

Auto-trading loop:
  - Runs every 60 seconds in a background thread
  - Scans BTC and ETH
  - Enforces every rule before opening a trade
  - Logs every decision including WHY it waited
  - Equity updates every second while a trade is open
"""

import threading, time, uuid
from datetime import datetime, date
from database import (
    load_balance, save_balance,
    load_position, save_position, clear_position, close_position_in_db,
    save_closed_trade, append_trade, load_journal,
    update_equity, cache_price,
)
from config import (
    RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
    BUY_CONDITIONS, SELL_CONDITIONS,
)


# ══════════════════════════════════════════════
#  DAILY STATS  (from DB journal)
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
#  POSITION SIZING
# ══════════════════════════════════════════════

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
    return {"risk_usd": risk_usd, "size": size, "stop_loss": sl, "take_profit": tp, "rr": rr}


# ══════════════════════════════════════════════
#  RULE ENFORCEMENT
# ══════════════════════════════════════════════

def check_all_rules(side: str, analysis: dict, confidence: int, rr: float, balance: float) -> dict:
    ms   = analysis["ms"]
    vol  = analysis["vol"]
    cond = BUY_CONDITIONS if side == "BUY" else SELL_CONDITIONS

    from database import get_open_positions_count
    from config import MAX_OPEN_POSITIONS
    open_count = get_open_positions_count()
    if open_count >= MAX_OPEN_POSITIONS:
        return {"approved": False, "reason": f"Max {MAX_OPEN_POSITIONS} open positions reached ({open_count} open). Waiting for one to close."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False, "reason": f"Daily trade limit reached ({MAX_TRADES_PER_DAY}). Done for today."}

    daily_loss = todays_loss_pct(balance)
    if daily_loss >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False, "reason": f"Daily loss limit hit ({daily_loss:.1f}%). Trading paused for today."}

    if ms["trend"] != cond["market_structure"]:
        return {"approved": False, "reason": f"Structure is {ms['trend']} ({ms['sequence']}). Need {cond['market_structure']} for {side}."}

    if ms["strength_pct"] < 30:
        return {"approved": False, "reason": f"Trend strength too weak ({ms['strength_pct']}%). Waiting for clearer structure."}

    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY" and not ema_bull:
        return {"approved": False, "reason": f"EMA20 below EMA50. Trend not confirmed."}
    if side == "SELL" and not ema_bear:
        return {"approved": False, "reason": f"EMA20 above EMA50. Trend not confirmed."}

    r = analysis["rsi14"]
    if side == "BUY"  and r > 75:
        return {"approved": False, "reason": f"RSI overbought ({r}). Risky to buy here."}
    if side == "SELL" and r < 25:
        return {"approved": False, "reason": f"RSI oversold ({r}). Risky to sell here."}

    if side == "BUY"  and vol["buy_pressure"]  < 48:
        return {"approved": False, "reason": f"Volume strongly favors sellers ({vol['sell_pressure']}%). Waiting."}
    if side == "SELL" and vol["sell_pressure"] < 48:
        return {"approved": False, "reason": f"Volume strongly favors buyers ({vol['buy_pressure']}%). Waiting."}

    if rr < MIN_RISK_REWARD:
        return {"approved": False, "reason": f"R:R is 1:{rr} — below minimum 1:{MIN_RISK_REWARD}. Skipping."}

    if confidence < MIN_CONFIDENCE:
        return {"approved": False, "reason": f"Confidence {confidence}% below minimum {MIN_CONFIDENCE}%."}

    frames   = analysis.get("frames", [])
    tf_buys  = sum(1 for f in frames if f["decision"] == "BUY")
    tf_sells = sum(1 for f in frames if f["decision"] == "SELL")
    if side == "BUY"  and tf_buys  < 1:
        return {"approved": False, "reason": f"No timeframe agrees on BUY ({tf_buys}/4). Waiting."}
    if side == "SELL" and tf_sells < 1:
        return {"approved": False, "reason": f"No timeframe agrees on SELL ({tf_sells}/4). Waiting."}

    return {"approved": True, "reason": "All conditions met."}


# ══════════════════════════════════════════════
#  OPEN TRADE
# ══════════════════════════════════════════════

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]

    pos_calc = calc_position(balance, price, atr, side)
    rr       = pos_calc["rr"]

    check = check_all_rules(side, analysis, confidence, rr, balance)
    if not check["approved"]:
        append_trade({
            "action": "WAIT", "symbol": symbol, "side_considered": side,
            "reason": check["reason"], "price": price, "confidence": confidence,
            "session": analysis.get("session",""), "opened_at": datetime.utcnow().isoformat(),
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

    save_position(position)   # → PostgreSQL

    append_trade({
        "action": "OPEN", "trade_id": trade_id, "symbol": symbol, "side": side,
        "entry": price, "stop_loss": pos_calc["stop_loss"],
        "take_profit": pos_calc["take_profit"], "size": pos_calc["size"],
        "risk": pos_calc["risk_usd"], "rr": rr,
        "trend": decision["trend"], "structure": analysis["ms"]["structure"],
        "sequence": analysis["ms"]["sequence"],
        "ema20": analysis["ema20"], "ema50": analysis["ema50"],
        "rsi": analysis["rsi14"], "rsi_label": analysis["rsi_label"],
        "volume_label": analysis["vol"]["label"],
        "buy_pressure": analysis["vol"]["buy_pressure"],
        "sell_pressure": analysis["vol"]["sell_pressure"],
        "patterns": [p["name"] for p in analysis["patterns"]],
        "confidence": confidence,
        "confidence_breakdown": decision["confidence"]["breakdown"],
        "reasoning": decision["reasons"],
        "session": analysis.get("session",""),
        "opened_at": datetime.utcnow().isoformat(),
    })

    _log(f"✅ OPENED #{trade_id} — {side} {pos_calc['size']} {symbol} @ ${price:,.2f} | Conf {confidence}%")
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

    save_balance(new_balance)          # → PostgreSQL balance
    close_position_in_db(position["trade_id"])  # → mark CLOSED

    duration = ""
    try:
        opened   = datetime.fromisoformat(position["opened_at"])
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        duration = f"{h}h {rem//60}m"
    except Exception:
        pass

    # Save to permanent trade history
    save_closed_trade({
        "trade_id":   position.get("trade_id",""),
        "symbol":     position["symbol"],
        "side":       side,
        "entry":      entry,
        "exit":       current_price,
        "stop_loss":  position["stop_loss"],
        "take_profit":position["take_profit"],
        "size":       size,
        "risk":       position.get("risk_amount", 0),
        "pl":         pl,
        "new_balance":new_balance,
        "duration":   duration,
        "exit_reason":reason,
        "opened_at":  position.get("opened_at",""),
    })

    # Also log to journal
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
    _log(f"{emoji} CLOSED #{position.get('trade_id','')} — P/L ${pl:,.2f} | Balance ${new_balance:,.2f} | {reason}")
    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ══════════════════════════════════════════════
#  EQUITY UPDATER  (runs every 5 seconds)
# ══════════════════════════════════════════════

def _equity_loop():
    """
    Updates equity every 5 seconds while a trade is open.
    Equity = balance + floating P/L.
    Does NOT change balance — only equity.
    """
    from scanner import fetch_current_price
    while _auto_status["running"]:
        try:
            position = load_position()
            if position:
                price  = fetch_current_price(position["symbol"])
                entry  = position["entry_price"]
                size   = position["size"]
                side   = position["side"]
                fl_pl  = (price - entry) * size if side == "BUY" else (entry - price) * size
                balance= load_balance()
                equity = round(balance + fl_pl, 2)
                update_equity(equity)     # → PostgreSQL, live
                cache_price(position["symbol"], price)
        except Exception as e:
            pass
        time.sleep(5)


# ══════════════════════════════════════════════
#  AUTO-TRADING LOOP  (runs every 60 seconds)
# ══════════════════════════════════════════════

_auto_status = {
    "running":       False,
    "last_scan":     "Never",
    "last_action":   "Waiting for first scan...",
    "last_decision": "",
    "scans_today":   0,
}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _auto_status["last_action"] = f"[{ts}] {msg}"


def _check_position_sl_tp(position: dict, current_price: float, atr: float):
    """Check SL/TP hit and break-even on every scan."""
    sl   = position["stop_loss"]
    tp   = position["take_profit"]
    side = position["side"]

    if side == "BUY":
        if current_price <= sl:
            close_trade(position, current_price, "Stop Loss hit"); return
        if current_price >= tp:
            close_trade(position, current_price, "Take Profit reached"); return
    else:
        if current_price >= sl:
            close_trade(position, current_price, "Stop Loss hit"); return
        if current_price <= tp:
            close_trade(position, current_price, "Take Profit reached"); return

    # Break-even
    if atr > 0 and not position.get("be_moved"):
        entry  = position["entry_price"]
        profit = (current_price-entry) if side=="BUY" else (entry-current_price)
        if profit >= atr:
            position["stop_loss"] = entry
            position["be_moved"]  = True
            save_position(position)
            append_trade({
                "action": "SL_MOVED_BE", "trade_id": position.get("trade_id",""),
                "symbol": position["symbol"], "new_sl": entry,
                "timestamp": datetime.utcnow().isoformat(),
            })
            _log(f"SL → Break-Even @ ${entry:,.2f}")


def _auto_loop():
    from scanner     import scan, fetch_current_price
    from analyzer    import analyze
    from decision_engine import decide

    _log("Auto-trading started. Scanning every 60 seconds.")

    while _auto_status["running"]:
        try:
            for symbol in VALID_SYMBOLS:
                _auto_status["last_scan"]   = datetime.utcnow().strftime("%H:%M:%S UTC")
                _auto_status["scans_today"] += 1

                scan_data = scan(symbol)
                if not scan_data["candles"]:
                    _log(f"{symbol} — No candle data"); continue

                analysis = analyze(scan_data)
                if "error" in analysis:
                    _log(f"{symbol} — Analysis error: {analysis['error']}"); continue

                decision = decide(analysis)
                dec      = decision["decision"]
                conf     = decision["confidence"]["total"]
                _auto_status["last_decision"] = (
                    f"{symbol} → {dec} | Conf {conf}% | "
                    f"{analysis['ms']['trend']} {analysis['ms']['sequence']}"
                )
                _log(f"{symbol} — {dec} | Conf {conf}%")

                # Check all open positions for this symbol
                from database import get_open_positions
                open_positions = get_open_positions()
                sym_positions  = [p for p in open_positions if p.get("symbol") == symbol]

                for pos in sym_positions:
                    price = fetch_current_price(symbol)
                    _check_position_sl_tp(pos, price, analysis["atr14"])

                # Still allow new trade if under MAX_OPEN_POSITIONS
                from config import MAX_OPEN_POSITIONS
                if len(open_positions) >= MAX_OPEN_POSITIONS:
                    _log(f"{symbol} — {len(open_positions)} positions open. Waiting.")
                    continue

                if dec == "WAIT":
                    continue

                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    _log(f"{symbol} — Rejected: {result['reason']}")

        except Exception as e:
            _log(f"Loop error: {e}")

        for _ in range(60):
            if not _auto_status["running"]: break
            time.sleep(1)

    _log("Auto-trading stopped.")


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
