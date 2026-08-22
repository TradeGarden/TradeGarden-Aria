"""
executor.py - Aria Trading Engine
Professional rules. Quality over quantity.
3-8 good trades per day. Not 100 random ones.

Entry requires ALL of:
  1. Market structure aligned (Bullish for BUY, Bearish for SELL)
  2. EMA20 above EMA50 (BUY) or below (SELL)
  3. RSI not extreme
  4. At least 2 timeframes agreeing
  5. Confidence >= 70%
  6. R:R >= 1.5
  7. Under daily limits
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
    BREAKEVEN_USD, PARTIAL_TP_USD, TRAIL_AFTER_USD,
    TIMEOUT_MINUTES, MIN_PROFIT_USD, SCAN_INTERVAL_SECONDS,
    MIN_TREND_STRENGTH, MIN_TIMEFRAMES_ALIGNED,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
)


# ── Candle strength score (0-20) ──────────────────────────────────────────

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

    # Market Structure (25pts)
    if (buy and ms.get("trend") == "Bullish") or \
       (not buy and ms.get("trend") == "Bearish"):
        scores["Market Structure"] = 25
    elif ms.get("trend") in ("Bullish","Bearish"):
        scores["Market Structure"] = 10

    # EMA (25pts)
    if (buy and e20 > e50) or (not buy and e20 < e50):
        scores["EMA Alignment"] = 25

    # RSI (15pts)
    if buy:
        if 40 < r < 70:  scores["RSI"] = 15
        elif r <= 40:     scores["RSI"] = 10
        elif r < 78:      scores["RSI"] = 5
    else:
        if 30 < r < 60:  scores["RSI"] = 15
        elif r >= 60:     scores["RSI"] = 10
        elif r > 22:      scores["RSI"] = 5

    # Candle strength (20pts)
    scores["Candle Strength"] = candle_strength(
        analysis.get("candles", []),
        e20,
        vol.get("avg20", 0)
    )

    # Volume (15pts)
    bp = vol.get("buy_pressure", 50)
    sp = vol.get("sell_pressure", 50)
    if buy:
        scores["Volume"] = 15 if bp > 55 else 10 if bp > 48 else 5
    else:
        scores["Volume"] = 15 if sp > 55 else 10 if sp > 48 else 5

    return {"breakdown": scores, "total": sum(scores.values())}


# ── Position sizing ───────────────────────────────────────────────────────

def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    # Scale by confidence - higher confidence = bigger position
    if confidence >= 85:
        mult, label = 1.0, f"Full position ({confidence}% conf)"
    elif confidence >= 75:
        mult, label = 0.7, f"70% position ({confidence}% conf)"
    else:
        mult, label = 0.5, f"Half position ({confidence}% conf)"

    risk_usd = round(balance * RISK_PER_TRADE_PCT / 100 * mult, 2)
    risk_usd = max(risk_usd, 0.50)

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)

    # Minimum stop distance
    if sl_dist < price * 0.001:
        sl_dist = price * 0.001
        sl = round(price - sl_dist, 2) if side == "BUY" \
             else round(price + sl_dist, 2)

    rr   = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    size = round(risk_usd / sl_dist, 6) if sl_dist > 0 else 0.0

    return {
        "risk_usd":   risk_usd,
        "size":       size,
        "stop_loss":  sl,
        "take_profit":tp,
        "rr":         rr,
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


# ── Rule enforcement ──────────────────────────────────────────────────────

def check_rules(symbol: str, side: str, analysis: dict,
                confidence: int, rr: float, balance: float) -> dict:
    ms     = analysis.get("ms", {})
    frames = analysis.get("frames", [])

    # 1. Daily limits
    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False,
                "reason": f"Max {MAX_OPEN_POSITIONS} positions open. Waiting."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False,
                "reason": f"Daily limit {MAX_TRADES_PER_DAY} trades reached."}

    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False,
                "reason": f"Daily loss limit {DAILY_LOSS_LIMIT_PCT}% reached. Stopped for today."}

    # 2. One position per symbol
    if any(p["symbol"] == symbol for p in get_open_positions()):
        return {"approved": False,
                "reason": f"{symbol} already has open position."}

    # 3. Market structure MUST align
    trend = ms.get("trend","")
    if side == "BUY" and trend != "Bullish":
        return {"approved": False,
                "reason": f"Market structure is {trend}. Need Bullish to BUY."}
    if side == "SELL" and trend != "Bearish":
        return {"approved": False,
                "reason": f"Market structure is {trend}. Need Bearish to SELL."}

    # 4. Trend strength must be meaningful
    strength = ms.get("strength_pct", 0)
    if strength < MIN_TREND_STRENGTH:
        return {"approved": False,
                "reason": f"Trend too weak ({strength}%). Need {MIN_TREND_STRENGTH}%+."}

    # 5. EMA MUST align
    e20 = analysis.get("ema20", 0)
    e50 = analysis.get("ema50", 0)
    if side == "BUY" and e20 <= e50:
        return {"approved": False,
                "reason": f"EMA20 (${e20:,.0f}) below EMA50 (${e50:,.0f}). No uptrend."}
    if side == "SELL" and e20 >= e50:
        return {"approved": False,
                "reason": f"EMA20 (${e20:,.0f}) above EMA50 (${e50:,.0f}). No downtrend."}

    # 6. RSI must not be extreme against trade
    r = analysis.get("rsi14", 50)
    if side == "BUY" and r > RSI_OVERBOUGHT:
        return {"approved": False,
                "reason": f"RSI overbought at {r:.1f}. Waiting for pullback."}
    if side == "SELL" and r < RSI_OVERSOLD:
        return {"approved": False,
                "reason": f"RSI oversold at {r:.1f}. Waiting for bounce."}

    # 7. Multi-timeframe: at least 2 must agree
    tf_agree = sum(1 for f in frames if f.get("decision") == side)
    if tf_agree < MIN_TIMEFRAMES_ALIGNED:
        return {"approved": False,
                "reason": f"Only {tf_agree}/4 timeframes agree on {side}. "
                          f"Need {MIN_TIMEFRAMES_ALIGNED}+."}

    # 8. R:R minimum
    if rr < MIN_RISK_REWARD:
        return {"approved": False,
                "reason": f"R:R 1:{rr} below minimum 1:{MIN_RISK_REWARD}."}

    # 9. Confidence minimum
    if confidence < MIN_CONFIDENCE:
        return {"approved": False,
                "reason": f"Confidence {confidence}% below minimum {MIN_CONFIDENCE}%."}

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
            "risk_amount":    calc["risk_usd"],
            "stop_loss":      calc["stop_loss"],
            "take_profit":    calc["take_profit"],
            "rr":             rr,
            "mode":           "SCALPER",
            "opened_at":      datetime.utcnow().isoformat(),
            "status":         "OPEN",
            "be_moved":       False,
            "partial_closed": False,
            "trail_sl":       False,
            "atr_at_open":    atr,
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
            "risk":        calc["risk_usd"],
            "size_label":  calc["size_label"],
            "rr":          rr,
            "confidence":  confidence,
            "trend":       ms.get("trend",""),
            "structure":   ms.get("structure",""),
            "sequence":    ms.get("sequence",""),
            "strength":    ms.get("strength_pct",0),
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
             f"| TP ${calc['take_profit']:,.2f} | Conf {confidence}% "
             f"| {calc['size_label']}")
        return {"success": True, "position": position}

    except Exception as e:
        _log(f"open_trade error: {e}")
        return {"success": False, "reason": str(e)}


# ── Close trade ───────────────────────────────────────────────────────────

def close_trade(position: dict, price: float,
                reason: str = "Manual", partial: float = 1.0) -> dict:
    try:
        entry = position["entry_price"]
        side  = position["side"]
        size  = round(position["size"] * partial, 6)
        pl    = round(((price - entry) * size if side == "BUY"
                       else (entry - price) * size), 2)

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
            opened   = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            secs     = int((datetime.utcnow() - opened).total_seconds())
            h        = secs // 3600
            m        = (secs % 3600) // 60
            duration = f"{h}h {m}m" if h > 0 else f"{m}m {secs%60}s"
        except Exception:
            pass

        if partial >= 1.0:
            save_closed_trade({
                "trade_id":    position.get("trade_id",""),
                "symbol":      position["symbol"],
                "side":        side,
                "entry":       entry,
                "exit":        price,
                "stop_loss":   position.get("stop_loss", 0),
                "take_profit": position.get("take_profit", 0),
                "size":        size,
                "risk":        position.get("risk_amount", 0),
                "pl":          pl,
                "new_balance": new_balance,
                "duration":    duration,
                "exit_reason": reason,
                "mode":        "SCALPER",
                "opened_at":   str(position.get("opened_at","")),
            })

        append_trade({
            "action":      "CLOSE" if partial >= 1.0 else "PARTIAL_TP",
            "trade_id":    position.get("trade_id",""),
            "symbol":      position["symbol"],
            "side":        side,
            "entry":       entry,
            "exit":        price,
            "size":        size,
            "pl":          pl,
            "new_balance": new_balance,
            "duration":    duration,
            "exit_reason": reason,
            "closed_at":   datetime.utcnow().isoformat(),
        })

        emoji = "WIN" if pl >= 0 else "LOSS"
        _log(f"{emoji} #{position.get('trade_id','')} {position['symbol']} "
             f"P/L ${pl:+,.2f} | Balance ${new_balance:,.2f} "
             f"| {reason} [{duration}]")
        return {"pl": pl, "new_balance": new_balance, "duration": duration}

    except Exception as e:
        _log(f"close_trade error: {e}")
        return {"pl": 0, "new_balance": load_balance(), "duration": ""}


# ── Manage open position ──────────────────────────────────────────────────

def manage_position(position: dict, price: float, atr: float) -> bool:
    """Returns True if position was fully closed."""
    try:
        entry = position["entry_price"]
        side  = position["side"]
        size  = position["size"]
        sl    = position["stop_loss"]
        tp    = position["take_profit"]

        fl = round(((price - entry) * size if side == "BUY"
                    else (entry - price) * size), 2)

        # Hard SL/TP check
        if side == "BUY":
            if price <= sl:
                close_trade(position, price, "Stop Loss hit")
                return True
            if price >= tp:
                close_trade(position, price, "Take Profit reached")
                return True
        else:
            if price >= sl:
                close_trade(position, price, "Stop Loss hit")
                return True
            if price <= tp:
                close_trade(position, price, "Take Profit reached")
                return True

        # Timeout check
        try:
            opened    = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            mins_open = (datetime.utcnow() - opened).total_seconds() / 60
            if mins_open >= TIMEOUT_MINUTES and fl < MIN_PROFIT_USD:
                close_trade(position, price,
                            f"Timeout {mins_open:.0f}min P/L ${fl:.2f}")
                return True
        except Exception:
            pass

        # Break-even at +$3
        if not position.get("be_moved") and fl >= BREAKEVEN_USD:
            new_sl = entry + 0.01 if side == "BUY" else entry - 0.01
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
                    "profit_at": fl,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                _log(f"Break-even @ ${new_sl:,.2f} "
                     f"(+${fl:.2f}) #{position.get('trade_id','')}")

        # Partial TP 50% at +$6
        if not position.get("partial_closed") and fl >= PARTIAL_TP_USD:
            close_trade(position, price,
                        f"Partial TP at +${fl:.2f}", partial=0.5)

        # Trail remainder after +$10
        if position.get("partial_closed") and fl >= TRAIL_AFTER_USD and atr > 0:
            trail = atr * 0.5
            if side == "BUY":
                new_sl = round(price - trail, 2)
                if new_sl > position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"Trail SL -> ${new_sl:,.2f}")
            else:
                new_sl = round(price + trail, 2)
                if new_sl < position["stop_loss"]:
                    position["stop_loss"] = new_sl
                    position["trail_sl"]  = True
                    save_position(position)
                    _log(f"Trail SL -> ${new_sl:,.2f}")

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

    _log("Aria started — quality entries only")

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

                if not scan_data.get("candles") or len(scan_data["candles"]) < 20:
                    _log(f"{symbol} insufficient candle data")
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

                # Manage existing positions
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    try:
                        manage_position(pos, price, analysis.get("atr14", 0))
                    except Exception as e:
                        _log(f"manage error: {e}")

                # Stage 3: Decide + score confidence
                try:
                    decision = decide(analysis)
                    new_conf = compute_confidence(
                        analysis, decision.get("decision","WAIT"))
                    decision["confidence"] = new_conf
                except Exception as e:
                    _log(f"{symbol} decide error: {e}")
                    continue

                dec  = decision.get("decision","WAIT")
                conf = decision["confidence"]["total"]
                ms   = analysis.get("ms",{})

                # Log every scan result clearly
                frames     = analysis.get("frames",[])
                tf_agree   = sum(1 for f in frames if f.get("decision")==dec)
                _status["last_decision"] = (
                    f"{symbol} {dec} | Conf {conf}% | "
                    f"{ms.get('trend','')} {ms.get('sequence','')} | "
                    f"{tf_agree}/4 TF agree")

                _log(f"{symbol} → {dec} | Conf {conf}% | "
                     f"Trend {ms.get('trend','')} "
                     f"Strength {ms.get('strength_pct',0)}% | "
                     f"RSI {analysis.get('rsi14',0):.1f} | "
                     f"{tf_agree}/4 TF")

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

        # Wait before next scan
        for _ in range(SCAN_INTERVAL_SECONDS):
            if not _status["running"]:
                break
            time.sleep(1)

    _log("Aria stopped.")


# ── Start / Stop ──────────────────────────────────────────────────────────

def start_auto_trading():
    if _status["running"]:
        return
    _status["running"]     = True
    _status["scans_today"] = 0
    threading.Thread(target=_loop,        daemon=True).start()
    threading.Thread(target=_equity_loop, daemon=True).start()
    _log("Aria started — scanning every 60s")


def stop_auto_trading():
    _status["running"] = False


def get_auto_status() -> dict:
    return {**_status}
