"""
executor.py - Aria Scalper Engine
Fast in, fast out. Targets 20-100 trades/day.
Tight stops = bigger sizes = faster visible profit.

Trade lifecycle:
  OPEN -> Break-Even at +$2 -> Partial TP 50% at +$4
  -> Trail remainder after +$6 -> Auto-close at 60min if stuck
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
    BREAKEVEN_USD, PARTIAL_TP_USD, TRAIL_AFTER_USD,
    TIMEOUT_MINUTES, MIN_PROFIT_USD, SCAN_INTERVAL_SECONDS,
    MIN_TREND_STRENGTH,
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
    if body / rng > 0.5:                              score += 5
    if c["close"] > p["high"] or c["close"] < p["low"]: score += 5
    if (c["close"] > ema20 and c["close"] > c["open"]) or \
       (c["close"] < ema20 and c["close"] < c["open"]):  score += 5
    if avg_vol > 0 and c["volume"] > avg_vol * 1.3:   score += 5
    return min(score, 20)


# ── Confidence scoring ────────────────────────────────────────────────────

def compute_confidence(analysis: dict, decision: str) -> dict:
    scores = {"Market Structure": 0, "EMA Alignment": 0,
              "RSI": 0, "Candle Strength": 0, "Volume": 0}
    ms  = analysis["ms"]
    e20 = analysis["ema20"]
    e50 = analysis["ema50"]
    r   = analysis["rsi14"]
    vol = analysis["vol"]

    d = decision if decision in ("BUY","SELL") else (
        "BUY" if ms["trend"] == "Bullish" else "SELL")
    buy  = d == "BUY"
    sell = d == "SELL"

    # Structure
    if (buy and ms["trend"]=="Bullish") or (sell and ms["trend"]=="Bearish"):
        scores["Market Structure"] = 25
    elif ms["trend"] in ("Bullish","Bearish"):
        scores["Market Structure"] = 12

    # EMA
    if (buy and e20 > e50) or (sell and e20 < e50):
        scores["EMA Alignment"] = 25

    # RSI
    if buy:
        if 35 < r < 72:  scores["RSI"] = 15
        elif r <= 35:     scores["RSI"] = 10
        elif r < 80:      scores["RSI"] = 5
    else:
        if 28 < r < 65:  scores["RSI"] = 15
        elif r >= 65:     scores["RSI"] = 10
        elif r > 20:      scores["RSI"] = 5

    # Candle
    candles = analysis.get("candles", [])
    avg_vol = vol.get("avg20", 0)
    scores["Candle Strength"] = candle_strength(candles, e20, avg_vol)

    # Volume
    bp = vol.get("buy_pressure", 50)
    sp = vol.get("sell_pressure", 50)
    if buy:
        scores["Volume"] = 15 if bp > 52 else (8 if bp > 48 else 3)
    else:
        scores["Volume"] = 15 if sp > 52 else (8 if sp > 48 else 3)

    return {"breakdown": scores, "total": sum(scores.values())}


# ── Position sizing ───────────────────────────────────────────────────────

def calc_position(balance: float, price: float, atr: float,
                  side: str, confidence: int, symbol: str) -> dict:
    # Scale size by confidence
    if confidence >= 85:   mult = 1.0
    elif confidence >= 70: mult = 0.7
    else:                  mult = 0.4

    risk_usd = round(balance * RISK_PER_TRADE_PCT / 100 * mult, 2)

    if side == "BUY":
        sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    else:
        sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    # Min stop distance to avoid zero-size
    if sl_dist < price * 0.001:
        sl_dist = price * 0.001

    size = round(risk_usd / sl_dist, 6)

    label = (f"Full ({confidence}%)" if mult == 1.0
             else f"70% ({confidence}%)" if mult == 0.7
             else f"40% ({confidence}%)")

    return {"risk_usd": risk_usd, "size": size, "stop_loss": sl,
            "take_profit": tp, "rr": rr, "size_label": label}


# ── Daily stats ───────────────────────────────────────────────────────────

def todays_trade_count() -> int:
    today = date.today().isoformat()
    return sum(1 for t in load_journal()
               if t.get("action") == "OPEN"
               and t.get("opened_at","")[:10] == today)


def todays_loss_pct(balance: float) -> float:
    from database import load_closed_trades
    today = date.today().isoformat()
    pls   = [float(t.get("pl",0)) for t in load_closed_trades(1)
             if t.get("closed_at","")[:10] == today]
    loss  = sum(p for p in pls if p < 0)
    return round(abs(loss) / balance * 100, 2) if balance > 0 else 0.0


# ── Rule check ────────────────────────────────────────────────────────────

def check_rules(symbol: str, side: str, analysis: dict,
                confidence: int, rr: float, balance: float) -> dict:
    ms  = analysis["ms"]
    vol = analysis["vol"]
    cond = BUY_CONDITIONS if side == "BUY" else SELL_CONDITIONS

    if get_open_positions_count() >= MAX_OPEN_POSITIONS:
        return {"approved": False, "reason": "Max positions open."}

    if todays_trade_count() >= MAX_TRADES_PER_DAY:
        return {"approved": False, "reason": f"Daily limit {MAX_TRADES_PER_DAY}."}

    if todays_loss_pct(balance) >= DAILY_LOSS_LIMIT_PCT:
        return {"approved": False, "reason": f"Daily loss limit hit."}

    # No duplicate symbol+side
    if any(p["symbol"]==symbol and p["side"]==side
           for p in get_open_positions()):
        return {"approved": False, "reason": f"{symbol} {side} already open."}

    # Structure
    if ms["trend"] != cond["market_structure"]:
        return {"approved": False,
                "reason": f"Structure {ms['trend']}. Need {cond['market_structure']}."}

    # Minimum trend strength (low threshold for scalping)
    if ms["strength_pct"] < MIN_TREND_STRENGTH:
        return {"approved": False, "reason": f"Trend weak ({ms['strength_pct']}%)."}

    # EMA
    if side=="BUY"  and analysis["ema20"] <= analysis["ema50"]:
        return {"approved": False, "reason": "EMA not aligned for BUY."}
    if side=="SELL" and analysis["ema20"] >= analysis["ema50"]:
        return {"approved": False, "reason": "EMA not aligned for SELL."}

    # RSI - only block extremes
    r = analysis["rsi14"]
    if side=="BUY"  and r > 82: return {"approved": False, "reason": f"RSI too high ({r})."}
    if side=="SELL" and r < 18: return {"approved": False, "reason": f"RSI too low ({r})."}

    # R:R
    if rr < MIN_RISK_REWARD:
        return {"approved": False, "reason": f"R:R {rr} < {MIN_RISK_REWARD}."}

    # Confidence
    if confidence < MIN_CONFIDENCE:
        return {"approved": False, "reason": f"Confidence {confidence}% < {MIN_CONFIDENCE}%."}

    return {"approved": True, "reason": "OK"}


# ── Open trade ────────────────────────────────────────────────────────────

def open_trade(symbol: str, side: str, analysis: dict, decision: dict) -> dict:
    balance    = load_balance()
    price      = analysis["price"]
    atr        = analysis["atr14"]
    confidence = decision["confidence"]["total"]
    calc       = calc_position(balance, price, atr, side, confidence, symbol)

    check = check_rules(symbol, side, analysis, confidence, calc["rr"], balance)
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
        "rr":             calc["rr"],
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
        "rr":          calc["rr"],
        "confidence":  confidence,
        "trend":       decision.get("trend",""),
        "structure":   analysis["ms"]["structure"],
        "sequence":    analysis["ms"]["sequence"],
        "ema20":       analysis["ema20"],
        "ema50":       analysis["ema50"],
        "rsi":         analysis["rsi14"],
        "session":     analysis.get("session",""),
        "opened_at":   datetime.utcnow().isoformat(),
    })

    _log(f"OPENED #{trade_id} {side} {calc['size']} {symbol} "
         f"@ ${price:,.2f} SL ${calc['stop_loss']:,.2f} "
         f"TP ${calc['take_profit']:,.2f} | {calc['size_label']}")
    return {"success": True, "position": position}


# ── Close trade ───────────────────────────────────────────────────────────

def close_trade(position: dict, price: float,
                reason: str = "Manual", partial: float = 1.0) -> dict:
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
            "stop_loss":   position["stop_loss"],
            "take_profit": position["take_profit"],
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

    e = "WIN" if pl >= 0 else "LOSS"
    _log(f"{e} #{position.get('trade_id','')} {position['symbol']} "
         f"P/L ${pl:,.2f} | Balance ${new_balance:,.2f} | {reason} [{duration}]")
    return {"pl": pl, "new_balance": new_balance, "duration": duration}


# ── Manage open position ──────────────────────────────────────────────────

def manage_position(position: dict, price: float, atr: float) -> bool:
    """Returns True if fully closed."""
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

    # Timeout - stuck trade killer
    try:
        opened = datetime.fromisoformat(
            str(position["opened_at"]).replace(" ","T")[:19])
        mins_open = (datetime.utcnow()-opened).total_seconds() / 60
        if mins_open >= TIMEOUT_MINUTES and fl < MIN_PROFIT_USD:
            close_trade(position, price,
                        f"Timeout {mins_open:.0f}min profit ${fl:.2f}")
            return True
    except Exception:
        pass

    # Break-even at +$2
    if not position.get("be_moved") and fl >= BREAKEVEN_USD:
        be = entry + 0.01 if side=="BUY" else entry - 0.01
        if (side=="BUY" and be > sl) or (side=="SELL" and be < sl):
            position["stop_loss"] = be
            position["be_moved"]  = True
            save_position(position)
            append_trade({"action": "SL_MOVED_BE",
                          "trade_id": position.get("trade_id",""),
                          "symbol": position["symbol"],
                          "new_sl": be, "profit_at": fl,
                          "timestamp": datetime.utcnow().isoformat()})
            _log(f"BE @ ${be:,.2f} profit ${fl:.2f} "
                 f"#{position.get('trade_id','')}")

    # Partial TP 50% at +$4
    if not position.get("partial_closed") and fl >= PARTIAL_TP_USD:
        close_trade(position, price, f"Partial TP +${fl:.2f}", partial=0.5)
        _log(f"Partial TP 50% @ ${price:,.2f}")

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


# ── Equity loop (every 5s) ────────────────────────────────────────────────

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
    "wins_today":    0,
    "losses_today":  0,
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

    _log("Scalper started - scanning every 60s")

    while _status["running"]:
        try:
            _status["last_scan"] = datetime.utcnow().strftime("%H:%M UTC")
            _status["trades_today"] = todays_trade_count()

            for symbol in VALID_SYMBOLS:
                _status["scans_today"] += 1

                # Scan
                scan_data = scan(symbol)
                if not scan_data["candles"] or len(scan_data["candles"]) < 10:
                    _log(f"{symbol} no data")
                    continue

                # Analyze
                analysis = analyze(scan_data)
                if "error" in analysis:
                    continue
                analysis["candles"] = scan_data["candles"]

                price = analysis["price"]

                # Manage open positions
                for pos in [p for p in get_open_positions()
                            if p["symbol"] == symbol]:
                    manage_position(pos, price, analysis["atr14"])

                # Decide
                decision    = decide(analysis)
                new_conf    = compute_confidence(analysis, decision["decision"])
                decision["confidence"] = new_conf

                dec  = decision["decision"]
                conf = new_conf["total"]
                ms   = analysis["ms"]

                _status["last_decision"] = (
                    f"{symbol} {dec} | Conf {conf}% | "
                    f"{ms['trend']} {ms['sequence']}")

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
            _log(f"Error: {e}")

        # Wait for next scan
        for _ in range(SCAN_INTERVAL_SECONDS):
            if not _status["running"]:
                break
            time.sleep(1)

    _log("Scalper stopped.")


def start_auto_trading():
    if _status["running"]:
        return
    _status["running"]     = True
    _status["scans_today"] = 0
    threading.Thread(target=_loop,         daemon=True).start()
    threading.Thread(target=_equity_loop,  daemon=True).start()
    _log("Aria Scalper started.")


def stop_auto_trading():
    _status["running"] = False


def get_auto_status() -> dict:
    return {**_status}


# Legacy aliases used by main.py
def todays_loss_pct_public(balance: float) -> float:
    return todays_loss_pct(balance)
