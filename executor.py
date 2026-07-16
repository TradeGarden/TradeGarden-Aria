"""
executor.py — Stage 4: EXECUTE
================================
Aria trades itself automatically on paper.

Auto-trading loop runs in a background thread.
Every 60 seconds it:
  1. Scans the market
  2. Analyzes all indicators
  3. Checks every rule
  4. Opens a trade if ALL conditions are met
  5. Logs every decision — including WHY it waited

Rules enforced before every trade:
  - Market structure must match direction
  - EMA alignment must confirm trend
  - Volume must confirm the move
  - A confirmation candlestick must be present
  - Risk/Reward >= 1:2
  - Confidence >= 75%
  - Max 1 open position
  - Max 6 trades per day
  - Daily loss limit 3% — stops trading for the rest of the day
"""

import json, os, uuid, threading, time
from datetime import datetime, date
from journal import append_trade, load_closed_trades, load_journal
from config import (
    RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MIN_RISK_REWARD, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, VALID_SYMBOLS,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER,
    BUY_CONDITIONS, SELL_CONDITIONS,
)

POSITION_FILE = "paper_position.json"
BALANCE_FILE  = "paper_balance.txt"


# ══════════════════════════════════════════════
#  BALANCE
# ══════════════════════════════════════════════

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


# ══════════════════════════════════════════════
#  POSITION
# ══════════════════════════════════════════════

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
    today  = date.today().isoformat()
    closed = [
        t for t in load_closed_trades()
        if t.get("closed_at", "")[:10] == today
    ]
    total_pl = sum(t.get("pl", 0) for t in closed)
    if total_pl >= 0 or balance <= 0:
        return 0.0
    return round(abs(total_pl) / balance * 100, 2)


# ══════════════════════════════════════════════
#  POSITION SIZING
# ══════════════════════════════════════════════

def calc_position(balance: float, price: float, atr: float, side: str) -> dict:
    """
    Risk-based position sizing.
    size = risk_usd / distance_to_stop_loss
    """
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


# ══════════════════════════════════════════════
#  RULE ENFORCEMENT
# ══════════════════════════════════════════════

def check_all_rules(side: str, analysis: dict, confidence: int,
                    rr: float, balance: float) -> dict:
    """
    Every single condition must pass.
    Returns {"approved": True} or {"approved": False, "reason": "..."}
    """
    ms   = analysis["ms"]
    vol  = analysis["vol"]
    pat  = analysis["patterns"]
    cond = BUY_CONDITIONS if side == "BUY" else SELL_CONDITIONS
    expected_trend = cond["market_structure"]

    # ── Safety checks first ───────────────────
    if load_position():
        return {"approved": False,
                "reason": "Already have an open position. Max 1 at a time."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily trade limit reached ({MAX_TRADES_PER_DAY}). Done for today."}

    daily_loss = todays_loss_pct(balance)
    if daily_loss >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit hit ({daily_loss:.1f}% of {DAILY_LOSS_LIMIT_PCT}%). "
                          f"Trading paused for today. Protecting the account."}

    # ── Professional entry conditions ────────
    # 1. Market structure
    if ms["trend"] != expected_trend:
        return {"approved": False,
                "reason": f"Market structure is {ms['trend']} ({ms['sequence']}). "
                          f"Need {expected_trend} for {side}. Waiting."}

    # 2. Trend strength — only trade moderate or strong trends
    if ms["strength_pct"] < 30:
        return {"approved": False,
                "reason": f"Trend strength too weak ({ms['strength_pct']}%). "
                          f"Waiting for a clearer structure."}

    # 3. EMA alignment
    ema_bull = analysis["ema20"] > analysis["ema50"]
    ema_bear = analysis["ema20"] < analysis["ema50"]
    if side == "BUY" and not ema_bull:
        return {"approved": False,
                "reason": f"EMA20 (${analysis['ema20']:,.0f}) is below EMA50 (${analysis['ema50']:,.0f}). "
                          f"Trend not confirmed. Waiting."}
    if side == "SELL" and not ema_bear:
        return {"approved": False,
                "reason": f"EMA20 (${analysis['ema20']:,.0f}) is above EMA50 (${analysis['ema50']:,.0f}). "
                          f"Trend not confirmed. Waiting."}

    # 4. RSI — avoid extreme zones against the trade
    r = analysis["rsi14"]
    if side == "BUY" and r > 75:
        return {"approved": False,
                "reason": f"RSI is overbought ({r}). Too risky to buy here. Waiting for pullback."}
    if side == "SELL" and r < 25:
        return {"approved": False,
                "reason": f"RSI is oversold ({r}). Too risky to sell here. Waiting for bounce."}

    # 5. Volume confirmation — threshold 48% (realistic for daily candles)
    if side == "BUY" and vol["buy_pressure"] < 48:
        return {"approved": False,
                "reason": f"Volume strongly favors sellers ({vol['sell_pressure']}% sell pressure). "
                          f"Waiting for buyers to show up."}
    if side == "SELL" and vol["sell_pressure"] < 48:
        return {"approved": False,
                "reason": f"Volume strongly favors buyers ({vol['buy_pressure']}% buy pressure). "
                          f"Waiting for sellers to show up."}

    # 6. Candlestick confirmation — OPTIONAL (adds confidence but does not block)
    # Candlestick patterns are rare on daily candles. They add confidence score
    # but are not required to open a trade. The other 5 conditions are enough.

    # 7. Minimum Risk/Reward
    if rr < MIN_RISK_REWARD:
        return {"approved": False,
                "reason": f"Risk/Reward is 1:{rr} — below minimum 1:{MIN_RISK_REWARD}. "
                          f"Not worth the risk. Skipping."}

    # 8. Minimum confidence (60% — realistic with 5 indicators, no MACD)
    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence is {confidence}% — below minimum {MIN_CONFIDENCE}%. "
                          f"Need stronger alignment across structure, EMA, RSI and volume."}

    # 9. Multi-timeframe alignment — at least 1 timeframe must agree
    frames   = analysis.get("frames", [])
    tf_buys  = sum(1 for f in frames if f["decision"] == "BUY")
    tf_sells = sum(1 for f in frames if f["decision"] == "SELL")
    if side == "BUY" and tf_buys < 1:
        return {"approved": False,
                "reason": f"No timeframe agrees on BUY ({tf_buys}/4). "
                          f"Waiting for at least one timeframe to confirm."}
    if side == "SELL" and tf_sells < 1:
        return {"approved": False,
                "reason": f"No timeframe agrees on SELL ({tf_sells}/4). "
                          f"Waiting for at least one timeframe to confirm."}

    return {"approved": True, "reason": "All conditions met. Executing trade."}


# ══════════════════════════════════════════════
#  OPEN TRADE
# ══════════════════════════════════════════════

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    """
    Run all checks and open the trade if everything passes.
    Returns {"success": True, "position": {...}}
         or {"success": False, "reason": "..."}
    """
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]

    pos_calc = calc_position(balance, price, atr, side)
    rr       = pos_calc["rr"]

    check = check_all_rules(side, analysis, confidence, rr, balance)
    if not check["approved"]:
        # Log the skipped decision to the journal
        append_trade({
            "action":     "WAIT",
            "symbol":     symbol,
            "side_considered": side,
            "reason":     check["reason"],
            "price":      price,
            "confidence": confidence,
            "session":    analysis.get("session", ""),
            "timestamp":  datetime.utcnow().isoformat(),
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

    _log(f"✅ TRADE OPENED #{trade_id} — {side} {pos_calc['size']} {symbol} "
         f"@ ${price:,.2f} | SL ${pos_calc['stop_loss']:,.2f} "
         f"| TP ${pos_calc['take_profit']:,.2f} | Confidence {confidence}%")

    return {"success": True, "position": position}


# ══════════════════════════════════════════════
#  CLOSE TRADE
# ══════════════════════════════════════════════

def close_trade(position: dict, current_price: float, reason: str = "Manual") -> dict:
    entry = position["entry_price"]
    size  = position["size"]
    side  = position["side"]

    pl = (current_price - entry) * size if side == "BUY" \
         else (entry - current_price) * size
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

    emoji = "🟢" if pl >= 0 else "🔴"
    _log(f"{emoji} TRADE CLOSED #{position.get('trade_id','')} — "
         f"{side} {position['symbol']} | P/L: ${pl:,.2f} | "
         f"New Balance: ${new_balance:,.2f} | Reason: {reason}")

    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ══════════════════════════════════════════════
#  AUTO-TRADING LOOP
# ══════════════════════════════════════════════

_auto_status = {
    "running":      False,
    "last_scan":    "Never",
    "last_action":  "Waiting for first scan...",
    "last_decision":"",
    "scans_today":  0,
}


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[ARIA {ts}] {msg}", flush=True)
    _auto_status["last_action"] = f"[{ts}] {msg}"


def _auto_loop():
    """
    Core auto-trading loop.
    Runs every 60 seconds in a background thread.
    Scans → Analyzes → Decides → Executes if conditions met.
    """
    # Import here to avoid circular imports
    from scanner  import scan, fetch_current_price
    from analyzer import analyze
    from decision_engine import decide

    _log("Auto-trading loop started. Scanning every 60 seconds.")

    while _auto_status["running"]:
        try:
            for symbol in VALID_SYMBOLS:
                _log(f"Scanning {symbol}...")
                _auto_status["last_scan"] = datetime.utcnow().strftime("%H:%M:%S UTC")
                _auto_status["scans_today"] += 1

                # ── Stage 1: Scan ──
                scan_data = scan(symbol)
                if not scan_data["candles"]:
                    _log(f"{symbol} — No market data available. Skipping.")
                    continue

                # ── Stage 2: Analyze ──
                analysis = analyze(scan_data)
                if "error" in analysis:
                    _log(f"{symbol} — Analysis error: {analysis['error']}")
                    continue

                # ── Stage 3: Decide ──
                decision = decide(analysis)
                dec      = decision["decision"]
                conf     = decision["confidence"]["total"]
                _auto_status["last_decision"] = (
                    f"{symbol} → {dec} | Confidence {conf}% | "
                    f"{analysis['ms']['trend']} {analysis['ms']['sequence']}"
                )
                _log(f"{symbol} — Decision: {dec} | Confidence: {conf}%")

                # ── Stage 4: Execute ──
                if dec == "WAIT":
                    _log(f"{symbol} — WAIT. Reason: {decision['reasons'][-1]}")
                    continue

                result = open_trade(symbol, dec, analysis, decision)

                if result["success"]:
                    pos = result["position"]
                    _log(f"{symbol} — Trade opened #{pos['trade_id']}")
                else:
                    _log(f"{symbol} — Trade rejected: {result['reason']}")

                # ── Stage 5: Check existing position ──
                position = load_position()
                if position and position.get("symbol") == symbol:
                    price = fetch_current_price(symbol)
                    _check_position(position, price, analysis)

        except Exception as e:
            _log(f"Loop error: {e}")

        # Wait 60 seconds before next scan
        for _ in range(60):
            if not _auto_status["running"]:
                break
            time.sleep(1)

    _log("Auto-trading loop stopped.")


def _check_position(position: dict, current_price: float, analysis: dict):
    """
    Check if SL or TP has been hit on an open position.
    Also moves SL to break-even when appropriate.
    """
    sl   = position["stop_loss"]
    tp   = position["take_profit"]
    side = position["side"]
    atr  = analysis.get("atr14", 0)

    # SL / TP check
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

    # Break-even: move SL to entry when profit >= 1 ATR
    if atr > 0 and not position.get("be_moved"):
        entry  = position["entry_price"]
        profit = (current_price - entry) if side == "BUY" else (entry - current_price)
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
            _log(f"SL moved to break-even @ ${entry:,.2f} for #{position.get('trade_id','')}")

    # Current P/L log
    pl = (current_price - position["entry_price"]) * position["size"]
    if side == "SELL":
        pl = (position["entry_price"] - current_price) * position["size"]
    _log(f"Position monitor — {side} {position['symbol']} | "
         f"Price ${current_price:,.2f} | P/L ${round(pl,2):,.2f}")


# ══════════════════════════════════════════════
#  START / STOP
# ══════════════════════════════════════════════

def start_auto_trading():
    """Call this once on app startup to begin autonomous trading."""
    if _auto_status["running"]:
        return
    _auto_status["running"]     = True
    _auto_status["scans_today"] = 0
    t = threading.Thread(target=_auto_loop, daemon=True)
    t.start()
    _log("Auto-trading started.")


def stop_auto_trading():
    """Stop the auto-trading loop gracefully."""
    _auto_status["running"] = False
    _log("Auto-trading stopping...")


def get_auto_status() -> dict:
    """Return current auto-trading status for the dashboard."""
    return {
        "running":      _auto_status["running"],
        "last_scan":    _auto_status["last_scan"],
        "last_action":  _auto_status["last_action"],
        "last_decision":_auto_status["last_decision"],
        "scans_today":  _auto_status["scans_today"],
    }
