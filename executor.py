"""
executor.py - Stage 4: EXECUTE
================================
Trading rules:
  - Max 3 open positions at once (BTC + ETH + scale-in on same symbol)
  - Max 6 trades per day total
  - Daily loss limit 3% - stops all trading for the day
  - 1 position per symbol UNLESS scaling in:
      First entry:  confidence 60-70% → 20% of risk
      Scale-in:     confidence rises to 70-85% → add 30% more
      Final scale:  confidence above 85% → add remaining 50%
  - WAIT is never logged to journal - only trades and closes
  - Equity recalculated from ALL open positions every 5 seconds
  - SL/TP auto-closes save permanently to PostgreSQL
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
)


# ══════════════════════════════════════════════
#  CONFIDENCE-BASED POSITION SIZING
#  60-70%  → 20% of base risk  (small test entry)
#  70-85%  → 50% of base risk  (half position)
#  85%+    → 100% of base risk (full position)
# ══════════════════════════════════════════════

def confidence_multiplier(confidence: int) -> tuple:
    """Returns (multiplier, label)"""
    if confidence >= 85:
        return 1.0, "Full (85%+ conf)"
    if confidence >= 70:
        return 0.5, "Half (70-84% conf)"
    return 0.2, "Test 20% (60-69% conf)"


def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    mult, label = confidence_multiplier(confidence)
    base_risk   = round(balance * RISK_PER_TRADE_PCT / 100, 2)
    actual_risk = round(base_risk * mult, 2)

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
        "multiplier": mult,
        "size_label": label,
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

    # Safety gates
    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False,
                "reason": f"Max {MAX_OPEN_POSITIONS} positions open. Waiting."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily limit {MAX_TRADES_PER_DAY} trades reached."}

    daily_loss = todays_loss_pct(balance)
    if daily_loss >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit {DAILY_LOSS_LIMIT_PCT}% hit. Paused."}

    # Scale-in: allow up to 2 positions per symbol (initial + 1 scale-in)
    # Each scale-in needs higher confidence than the minimum
    open_positions = get_open_positions()
    sym_positions  = [p for p in open_positions
                     if p["symbol"] == symbol and p["side"] == side]
    if len(sym_positions) >= 2:
        return {"approved": False,
                "reason": f"Already have 2 {symbol} {side} positions. Max per symbol reached."}
    if len(sym_positions) == 1:
        # Scale-in: only if confidence is solid (70%+)
        if confidence < 70:
            return {"approved": False,
                    "reason": f"Scale-in requires 70%+ confidence. Currently {confidence}%."}

    # Market structure
    if ms["trend"] != cond["market_structure"]:
        return {"approved": False,
                "reason": f"Structure {ms['trend']} ({ms['sequence']}). "
                          f"Need {cond['market_structure']} for {side}."}

    # Trend strength
    if ms["strength_pct"] < 30:
        return {"approved": False,
                "reason": f"Trend too weak ({ms['strength_pct']}%). Waiting."}

    # EMA
    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY"  and not ema_bull:
        return {"approved": False, "reason": "EMA20 below EMA50. No uptrend confirmed."}
    if side == "SELL" and not ema_bear:
        return {"approved": False, "reason": "EMA20 above EMA50. No downtrend confirmed."}

    # RSI extremes
    r = analysis["rsi14"]
    if side == "BUY"  and r > 75:
        return {"approved": False, "reason": f"RSI overbought ({r}). Skip."}
    if side == "SELL" and r < 25:
        return {"approved": False, "reason": f"RSI oversold ({r}). Skip."}

    # Volume (only block if strongly against - 45% threshold)
    if side == "BUY"  and vol["buy_pressure"]  < 45:
        return {"approved": False,
                "reason": f"Volume favors sellers ({vol['sell_pressure']}%). Skip."}
    if side == "SELL" and vol["sell_pressure"] < 45:
        return {"approved": False,
                "reason": f"Volume favors buyers ({vol['buy_pressure']}%). Skip."}

    # R:R
    if rr < MIN_RISK_REWARD:
        return {"approved": False, "reason": f"R:R 1:{rr} below min 1:{MIN_RISK_REWARD}."}

    # Confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence {confidence}% below min {MIN_CONFIDENCE}%."}

    # At least 1 timeframe agrees
    frames   = analysis.get("frames", [])
    tf_match = sum(1 for f in frames if f["decision"] == side)
    if tf_match < 1:
        return {"approved": False,
                "reason": f"No timeframe confirms {side}."}

    return {"approved": True, "reason": "All conditions met."}


# ══════════════════════════════════════════════
#  OPEN TRADE
# ══════════════════════════════════════════════

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]
    pos_calc   = calc_position(balance, price, atr, side, confidence)
    rr         = pos_calc["rr"]

    check = check_all_rules(symbol, side, analysis, confidence, rr, balance)
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
        "trail_sl":    False,
    }

    save_position(position)

    # Determine if this is initial entry or scale-in
    open_positions = get_open_positions()
    sym_count = len([p for p in open_positions
                     if p["symbol"] == symbol and p["side"] == side])
    entry_type = "SCALE_IN" if sym_count > 1 else "OPEN"

    append_trade({
        "action":      entry_type,
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
        "confidence":  confidence,
        "trend":       decision["trend"],
        "structure":   analysis["ms"]["structure"],
        "sequence":    analysis["ms"]["sequence"],
        "ema20":       analysis["ema20"],
        "ema50":       analysis["ema50"],
        "rsi":         analysis["rsi14"],
        "rsi_label":   analysis["rsi_label"],
        "volume_label":analysis["vol"]["label"],
        "patterns":    [p["name"] for p in analysis["patterns"]],
        "confidence_breakdown": decision["confidence"]["breakdown"],
        "reasoning":   decision["reasons"],
        "session":     analysis.get("session", ""),
        "opened_at":   datetime.utcnow().isoformat(),
    })

    _log(f"✅ {entry_type} #{trade_id} - {side} {pos_calc['size']} {symbol} "
         f"@ ${price:,.2f} | {pos_calc['size_label']} | Conf {confidence}%")
    return {"success": True, "position": position}


# ══════════════════════════════════════════════
#  CLOSE TRADE
# ══════════════════════════════════════════════

def close_trade(position: dict, current_price: float, reason: str = "Manual") -> dict:
    entry = position["entry_price"]
    size  = position["size"]
    side  = position["side"]

    pl = ((current_price - entry) * size if side == "BUY"
          else (entry - current_price) * size)
    pl = round(pl, 2)

    balance     = load_balance()
    new_balance = round(balance + pl, 2)

    save_balance(new_balance)
    close_position_in_db(position["trade_id"])

    duration = ""
    try:
        opened   = datetime.fromisoformat(
            str(position["opened_at"]).replace(" ", "T")[:19]
        )
        dur      = datetime.utcnow() - opened
        h, rem   = divmod(int(dur.total_seconds()), 3600)
        duration = f"{h}h {rem // 60}m"
    except Exception:
        pass

    save_closed_trade({
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
        "opened_at":   str(position.get("opened_at", "")),
    })

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

    emoji = "🟢" if pl >= 0 else "🔴"
    _log(f"{emoji} CLOSED #{position.get('trade_id', '')} - "
         f"P/L ${pl:,.2f} | Balance ${new_balance:,.2f} | {reason}")
    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ══════════════════════════════════════════════
#  POSITION CHECK - SL / TP / Break-Even
# ══════════════════════════════════════════════

def check_position(position: dict, current_price: float, atr: float) -> bool:
    """Returns True if position was closed."""
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

    # Move SL to break-even when profit >= 1 ATR
    if atr > 0 and not position.get("be_moved"):
        entry  = position["entry_price"]
        profit = ((current_price - entry) if side == "BUY"
                  else (entry - current_price))
        if profit >= atr:
            position["stop_loss"] = entry
            position["be_moved"]  = True
            save_position(position)
            append_trade({
                "action":    "SL_MOVED_BE",
                "trade_id":  position.get("trade_id", ""),
                "symbol":    position["symbol"],
                "new_sl":    entry,
                "timestamp": datetime.utcnow().isoformat(),
            })
            _log(f"SL → Break-Even @ ${entry:,.2f} "
                 f"for #{position.get('trade_id', '')}")

    return False


# ══════════════════════════════════════════════
#  EQUITY UPDATER - every 5 seconds
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
                equity = recalc_equity(prices)
                _auto_status["equity"] = equity
        except Exception:
            pass
        time.sleep(5)


# ══════════════════════════════════════════════
#  AUTO-TRADING LOOP - every 60 seconds
# ══════════════════════════════════════════════

_auto_status = {
    "running":       False,
    "last_scan":     "Never",
    "last_action":   "Waiting for first scan...",
    "last_decision": "",
    "scans_today":   0,
    "equity":        0.0,
}

# Suppress repeated identical reasons from flooding logs
_last_reason: dict = {}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _auto_status["last_action"] = f"[{ts}] {msg}"


def _auto_loop():
    from scanner         import scan, fetch_current_price
    from analyzer        import analyze
    from decision_engine import decide

    _log("Auto-trading started - BTC + ETH - max 3 open - 6 trades/day")

    while _auto_status["running"]:
        try:
            _auto_status["last_scan"] = datetime.utcnow().strftime("%H:%M UTC")

            for symbol in VALID_SYMBOLS:
                _auto_status["scans_today"] += 1

                # ── Stage 1: Scan ──────────────────────
                scan_data = scan(symbol)
                if not scan_data["candles"]:
                    _log(f"{symbol} - No candle data")
                    continue

                # ── Stage 2: Analyze ───────────────────
                analysis = analyze(scan_data)
                if "error" in analysis:
                    continue

                current_price = analysis["price"]

                # ── Check all open positions for SL/TP ─
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    check_position(pos, current_price, analysis["atr14"])

                # ── Stage 3: Decide ────────────────────
                decision = decide(analysis)
                dec      = decision["decision"]
                conf     = decision["confidence"]["total"]

                _auto_status["last_decision"] = (
                    f"{symbol} → {dec} | Conf {conf}% | "
                    f"{analysis['ms']['trend']} {analysis['ms']['sequence']}"
                )

                # ── Stage 4: Execute ───────────────────
                if dec == "WAIT":
                    continue   # Never log WAIT to journal - no spam

                _log(f"{symbol} - {dec} | Conf {conf}% | "
                     f"{analysis['ms']['trend']}")

                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    # Only log rejection if message changed
                    key = f"{symbol}_rej"
                    if _last_reason.get(key) != result["reason"]:
                        _last_reason[key] = result["reason"]
                        _log(f"{symbol} - Skipped: {result['reason']}")
                else:
                    _last_reason[f"{symbol}_rej"] = ""  # reset on success

        except Exception as e:
            _log(f"Error: {e}")

        # 60-second wait
        for _ in range(60):
            if not _auto_status["running"]:
                break
            time.sleep(1)

    _log("Auto-trading stopped.")


# ══════════════════════════════════════════════
#  START / STOP / STATUS
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
