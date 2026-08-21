"""
executor.py - Aria Trading Engine
Fast scalper. Clean rules. Trades when conditions align.
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
    TIMEOUT_MINUTES, SCAN_INTERVAL_SECONDS,
)

# ── Candle strength score ─────────────────────────────────────────────────

def candle_strength(candles: list, ema20: float, avg_vol: float) -> int:
    if len(candles) < 2:
        return 0
    c, p = candles[-1], candles[-2]
    body = abs(c["close"] - c["open"])
    rng  = c["high"] - c["low"]
    if rng == 0:
        return 0
    score = 0
    if body / rng > 0.5:                                       score += 5
    if c["close"] > p["high"] or c["close"] < p["low"]:       score += 5
    if (c["close"] > ema20 and c["open"] < c["close"]) or \
       (c["close"] < ema20 and c["open"] > c["close"]):       score += 5
    if avg_vol > 0 and c["volume"] > avg_vol * 1.3:            score += 5
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

    d = decision if decision in ("BUY","SELL") else (
        "BUY" if ms.get("trend") == "Bullish" else "SELL")
    buy = d == "BUY"

    # Market Structure 25pts
    if (buy and ms.get("trend")=="Bullish") or (not buy and ms.get("trend")=="Bearish"):
        scores["Market Structure"] = 25
    elif ms.get("trend") in ("Bullish","Bearish"):
        scores["Market Structure"] = 12

    # EMA 25pts
    if (buy and e20 > e50) or (not buy and e20 < e50):
        scores["EMA Alignment"] = 25

    # RSI 15pts
    if buy:
        if 35 < r < 75:  scores["RSI"] = 15
        elif r <= 35:     scores["RSI"] = 10
        elif r < 82:      scores["RSI"] = 5
    else:
        if 25 < r < 65:  scores["RSI"] = 15
        elif r >= 65:     scores["RSI"] = 10
        elif r > 18:      scores["RSI"] = 5

    # Candle strength 20pts
    candles = analysis.get("candles", [])
    avg_vol = vol.get("avg20", 0)
    scores["Candle Strength"] = candle_strength(candles, e20, avg_vol)

    # Volume 15pts — very relaxed
    bp = vol.get("buy_pressure", 50)
    sp = vol.get("sell_pressure", 50)
    if buy:
        scores["Volume"] = 15 if bp > 55 else 10 if bp > 45 else 5
    else:
        scores["Volume"] = 15 if sp > 55 else 10 if sp > 45 else 5

    return {"breakdown": scores, "total": sum(scores.values())}


# ── Position sizing ───────────────────────────────────────────────────────

def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int) -> dict:
    # Scale by confidence
    mult  = 1.0 if confidence >= 85 else 0.7 if confidence >= 70 else 0.4
    label = f"Full ({confidence}%)" if mult==1.0 else \
            f"70% ({confidence}%)" if mult==0.7 else f"40% ({confidence}%)"

    risk_usd = round(balance * RISK_PER_TRADE_PCT / 100 * mult, 2)
    risk_usd = max(risk_usd, 0.50)  # minimum $0.50 risk

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)

    # Ensure minimum stop distance
    min_dist = price * 0.002
    if sl_dist < min_dist:
        sl_dist = min_dist
        sl = round(price - sl_dist, 2) if side=="BUY" else round(price + sl_dist, 2)

    rr   = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    size = round(risk_usd / sl_dist, 6) if sl_dist > 0 else 0.0

    return {
        "risk_usd": risk_usd, "size": size,
        "stop_loss": sl, "take_profit": tp,
        "rr": rr, "size_label": label,
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


# ── Rule check ────────────────────────────────────────────────────────────

def check_rules(symbol: str, side: str, analysis: dict,
                confidence: int, rr: float, balance: float) -> dict:
    ms  = analysis.get("ms", {})

    # Daily limits
    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False, "reason": f"Max {MAX_OPEN_POSITIONS} positions open."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False, "reason": f"Daily limit {MAX_TRADES_PER_DAY} reached."}

    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False, "reason": f"Daily loss limit hit."}

    # One position per symbol
    if any(p["symbol"]==symbol for p in get_open_positions()):
        return {"approved": False, "reason": f"{symbol} already has open position."}

    # Market structure must exist
    trend = ms.get("trend","")
    if side=="BUY"  and trend != "Bullish":
        return {"approved": False, "reason": f"Structure {trend}. Need Bullish for BUY."}
    if side=="SELL" and trend != "Bearish":
        return {"approved": False, "reason": f"Structure {trend}. Need Bearish for SELL."}

    # EMA alignment
    e20 = analysis.get("ema20",0)
    e50 = analysis.get("ema50",0)
    if side=="BUY"  and e20 <= e50:
        return {"approved": False, "reason": f"EMA20 below EMA50 — no uptrend."}
    if side=="SELL" and e20 >= e50:
        return {"approved": False, "reason": f"EMA20 above EMA50 — no downtrend."}

    # RSI — only block extremes
    r = analysis.get("rsi14", 50)
    if side=="BUY"  and r > 85:
        return {"approved": False, "reason": f"RSI overbought ({r:.1f})."}
    if side=="SELL" and r < 15:
        return {"approved": False, "reason": f"RSI oversold ({r:.1f})."}

    # R:R
    if rr < MIN_RISK_REWARD:
        return {"approved": False, "reason": f"R:R 1:{rr} below min 1:{MIN_RISK_REWARD}."}

    # Confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved": False, "reason": f"Confidence {confidence}% below {MIN_CONFIDENCE}%."}

    return {"approved": True, "reason": "All conditions met."}


# ── Open trade ────────────────────────────────────────────────────────────

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    try:
        balance    = load_balance()
        price      = analysis.get("price", 0)
        atr        = analysis.get("atr14", price * 0.01)
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
            "trend":       analysis.get("ms",{}).get("trend",""),
            "structure":   analysis.get("ms",{}).get("structure",""),
            "sequence":    analysis.get("ms",{}).get("sequence",""),
            "ema20":       analysis.get("ema20",0),
            "ema50":       analysis.get("ema50",0),
            "rsi":         analysis.get("rsi14",0),
            "session":     analysis.get("session",""),
            "opened_at":   datetime.utcnow().isoformat(),
        })

        _log(f"OPENED #{trade_id} {side} {calc['size']} {symbol} "
             f"@ ${price:,.2f} SL ${calc['stop_loss']:,.2f} "
             f"TP ${calc['take_profit']:,.2f} | {calc['size_label']}")
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
        pl    = round(((price-entry)*size if side=="BUY"
                       else (entry-price)*size), 2)

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
            secs     = int((datetime.utcnow()-opened).total_seconds())
            duration = f"{secs//60}m {secs%60}s"
        except Exception:
            pass

        if partial >= 1.0:
            save_closed_trade({
                "trade_id":    position.get("trade_id",""),
                "symbol":      position["symbol"],
                "side":        side,
                "entry":       entry,
                "exit":        price,
                "stop_loss":   position.get("stop_loss",0),
                "take_profit": position.get("take_profit",0),
                "size":        size,
                "risk":        position.get("risk_amount",0),
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
             f"P/L ${pl:,.2f} | Balance ${new_balance:,.2f} | {reason} [{duration}]")
        return {"pl": pl, "new_balance": new_balance, "duration": duration}
    except Exception as e:
        _log(f"close_trade error: {e}")
        return {"pl": 0, "new_balance": load_balance(), "duration": ""}


# ── Manage position ───────────────────────────────────────────────────────

def manage_position(position: dict, price: float, atr: float) -> bool:
    """Returns True if fully closed."""
    try:
        entry = position["entry_price"]
        side  = position["side"]
        size  = position["size"]
        sl    = position["stop_loss"]
        tp    = position["take_profit"]

        fl = round(((price-entry)*size if side=="BUY"
                    else (entry-price)*size), 2)

        # Hard SL/TP
        if side == "BUY":
            if price <= sl:
                close_trade(position, price, "Stop Loss hit"); return True
            if price >= tp:
                close_trade(position, price, "Take Profit reached"); return True
        else:
            if price >= sl:
                close_trade(position, price, "Stop Loss hit"); return True
            if price <= tp:
                close_trade(position, price, "Take Profit reached"); return True

        # Timeout
        try:
            opened     = datetime.fromisoformat(
                str(position["opened_at"]).replace(" ","T")[:19])
            mins_open  = (datetime.utcnow()-opened).total_seconds() / 60
            if mins_open >= TIMEOUT_MINUTES and fl < 0.50:
                close_trade(position, price, f"Timeout {mins_open:.0f}min P/L ${fl:.2f}")
                return True
        except Exception:
            pass

        # Break-even
        if not position.get("be_moved") and fl >= BREAKEVEN_USD:
            be = entry + 0.01 if side=="BUY" else entry - 0.01
            if (side=="BUY" and be > sl) or (side=="SELL" and be < sl):
                position["stop_loss"] = be
                position["be_moved"]  = True
                save_position(position)
                append_trade({
                    "action": "SL_MOVED_BE",
                    "trade_id": position.get("trade_id",""),
                    "symbol": position["symbol"],
                    "new_sl": be, "profit_at": fl,
                    "timestamp": datetime.utcnow().isoformat(),
                })
                _log(f"BE @ ${be:,.2f} profit ${fl:.2f}")

        # Partial TP 50% at +$4
        if not position.get("partial_closed") and fl >= PARTIAL_TP_USD:
            close_trade(position, price, f"Partial TP +${fl:.2f}", partial=0.5)

        # Trail after +$6
        if position.get("partial_closed") and fl >= TRAIL_AFTER_USD and atr > 0:
            trail = atr * 0.3
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
    from scanner         import scan, fetch_current_price
    from analyzer        import analyze
    from decision_engine import decide

    _log("Aria Scalper started")

    while _status["running"]:
        try:
            _status["last_scan"]    = datetime.utcnow().strftime("%H:%M UTC")
            _status["trades_today"] = todays_trade_count()

            for symbol in VALID_SYMBOLS:
                _status["scans_today"] += 1

                # Scan
                try:
                    scan_data = scan(symbol)
                except Exception as e:
                    _log(f"{symbol} scan error: {e}")
                    continue

                if not scan_data.get("candles") or len(scan_data["candles"]) < 10:
                    _log(f"{symbol} no candle data")
                    continue

                # Analyze
                try:
                    analysis = analyze(scan_data)
                except Exception as e:
                    _log(f"{symbol} analyze error: {e}")
                    continue

                if "error" in analysis:
                    _log(f"{symbol} analysis error: {analysis['error']}")
                    continue

                analysis["candles"] = scan_data["candles"]
                price = analysis.get("price", 0)

                # Manage open positions
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    try:
                        manage_position(pos, price, analysis.get("atr14", 0))
                    except Exception as e:
                        _log(f"manage error: {e}")

                # Decide
                try:
                    decision = decide(analysis)
                    new_conf = compute_confidence(analysis, decision.get("decision","WAIT"))
                    decision["confidence"] = new_conf
                except Exception as e:
                    _log(f"{symbol} decide error: {e}")
                    continue

                dec  = decision.get("decision","WAIT")
                conf = decision["confidence"]["total"]

                _status["last_decision"] = (
                    f"{symbol} {dec} | Conf {conf}% | "
                    f"{analysis.get('ms',{}).get('trend','')} "
                    f"{analysis.get('ms',{}).get('sequence','')}")

                _log(f"{symbol} {dec} | Conf {conf}% | "
                     f"EMA20={analysis.get('ema20',0):,.0f} "
                     f"EMA50={analysis.get('ema50',0):,.0f} "
                     f"RSI={analysis.get('rsi14',0):.1f}")

                if dec == "WAIT":
                    continue

                result = open_trade(symbol, dec, analysis, decision)
                if not result["success"]:
                    key = symbol + dec
                    if _last_skip.get(key) != result["reason"]:
                        _last_skip[key] = result["reason"]
                        _log(f"{symbol} skip: {result['reason']}")
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
    _log("Aria Scalper + equity updater started.")


def stop_auto_trading():
    _status["running"] = False


def get_auto_status() -> dict:
    return {**_status}
