from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import requests
import json
import os
from openai import OpenAI

app = FastAPI(title="Aria AI Trading Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AI_API_KEY = os.getenv("AI_API_KEY")
client = OpenAI(api_key=AI_API_KEY) if AI_API_KEY and AI_API_KEY.startswith("sk-") else None

MAX_RISK_PERCENT = 1.0
VALID_SYMBOLS    = ["BTCUSD", "ETHUSD"]
KRAKEN_PAIRS     = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}

# ══════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════

def load_balance() -> float:
    try:
        if os.path.exists("paper_balance.txt"):
            with open("paper_balance.txt") as f:
                return float(f.read().strip())
    except Exception:
        pass
    return 500.0

def save_balance(b: float):
    with open("paper_balance.txt", "w") as f:
        f.write(str(round(b, 2)))

def load_position():
    try:
        if os.path.exists("paper_position.json"):
            with open("paper_position.json") as f:
                d = json.load(f)
                return d if d else None
    except Exception:
        pass
    return None

def save_position(p):
    with open("paper_position.json", "w") as f:
        json.dump(p, f)

def save_to_journal(e: dict):
    with open("trade_journal.txt", "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

# ══════════════════════════════════════════════
#  MARKET DATA
# ══════════════════════════════════════════════

def fetch_candles_kraken(symbol: str, interval_min: int = 1440, limit: int = 120) -> list:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get("https://api.kraken.com/0/public/OHLC",
                         params={"pair": pair, "interval": interval_min}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("error"): return []
        result = data.get("result", {})
        key = [k for k in result if k != "last"]
        if not key: return []
        raw = result[key[0]]
        return [{"open": float(c[1]), "high": float(c[2]),
                 "low": float(c[3]), "close": float(c[4]),
                 "volume": float(c[6])} for c in raw][-limit:]
    except Exception:
        return []

def fetch_candles_coingecko(symbol: str) -> list:
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": 100, "interval": "daily"},
            timeout=15, headers={"Accept": "application/json"})
        r.raise_for_status()
        prices = r.json().get("prices", [])
        if len(prices) < 55: return []
        candles = []
        for i in range(1, len(prices)):
            prev, curr = float(prices[i-1][1]), float(prices[i][1])
            candles.append({"open": prev, "high": max(prev, curr),
                            "low": min(prev, curr), "close": curr, "volume": 0.0})
        return candles[-100:]
    except Exception:
        return []

def fetch_candles(symbol: str, interval_min: int = 1440) -> list:
    c = fetch_candles_kraken(symbol, interval_min)
    if len(c) >= 55: return c
    if interval_min == 1440:
        c = fetch_candles_coingecko(symbol)
        if len(c) >= 55: return c
    return []

def fetch_current_price(symbol: str) -> float:
    pair = KRAKEN_PAIRS.get(symbol, "XBTUSD")
    try:
        r = requests.get("https://api.kraken.com/0/public/Ticker",
                         params={"pair": pair}, timeout=10)
        r.raise_for_status()
        result = r.json().get("result", {})
        key = list(result.keys())[0] if result else None
        if key: return float(result[key]["c"][0])
    except Exception:
        pass
    coin = "bitcoin" if "BTC" in symbol else "ethereum"
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd",
            timeout=10)
        return float(r.json()[coin]["usd"])
    except Exception:
        return 62000.0 if "BTC" in symbol else 3400.0

# ══════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════

def ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return round(sum(closes) / len(closes), 2)
    subset = closes[-period:]
    k = 2 / (period + 1)
    val = subset[0]
    for p in subset[1:]:
        val = p * k + val * (1 - k)
    return round(val, 2)

def rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1: return 50.0
    rel = closes[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(rel)):
        d = rel[i] - rel[i - 1]
        (gains if d >= 0 else losses).append(abs(d))
    ag = sum(gains) / period if gains else 0.0
    al = sum(losses) / period if losses else 1e-9
    return round(100 - (100 / (1 + ag / al)), 2)

def rsi_label(r: float) -> str:
    if r >= 70: return "Overbought"
    if r <= 30: return "Oversold"
    return "Neutral"

def macd_calc(closes: list):
    if len(closes) < 26: return 0.0, 0.0, 0.0
    e12  = ema(closes, 12)
    e26  = ema(closes, 26)
    line = round(e12 - e26, 2)
    sig  = round(line * 0.2 + line * 0.8, 2)
    hist = round(line - sig, 2)
    return line, sig, hist

def atr_calc(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        pc = candles[i - 1]["close"]
        tr = max(candles[i]["high"] - candles[i]["low"],
                 abs(candles[i]["high"] - pc),
                 abs(candles[i]["low"] - pc))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 2)

def volume_analysis(candles: list) -> dict:
    if len(candles) < 20:
        return {"current": 0, "avg20": 0, "buy_pressure": 50,
                "sell_pressure": 50, "relative": 1.0}
    recent   = candles[-20:]
    current  = round(candles[-1]["volume"], 2)
    avg20    = round(sum(c["volume"] for c in recent) / 20, 2)
    relative = round(current / avg20, 2) if avg20 > 0 else 1.0
    buy_vol  = sum(c["volume"] for c in recent if c["close"] >= c["open"])
    sell_vol = sum(c["volume"] for c in recent if c["close"] < c["open"])
    total    = buy_vol + sell_vol if (buy_vol + sell_vol) > 0 else 1
    return {
        "current":       current,
        "avg20":         avg20,
        "relative":      relative,
        "buy_pressure":  round(buy_vol / total * 100, 1),
        "sell_pressure": round(sell_vol / total * 100, 1),
    }

def detect_market_structure(candles: list) -> dict:
    recent = candles[-40:] if len(candles) >= 40 else candles
    wins   = 5
    size   = max(len(recent) // wins, 1)
    swing_highs, swing_lows = [], []
    for i in range(wins):
        chunk = recent[i * size:(i + 1) * size] if i < wins - 1 else recent[i * size:]
        if not chunk: continue
        swing_highs.append(max(c["high"] for c in chunk))
        swing_lows.append(min(c["low"]  for c in chunk))

    seq = []
    for i in range(1, len(swing_highs)):
        ph, pl = swing_highs[i - 1], swing_lows[i - 1]
        ch, cl = swing_highs[i],     swing_lows[i]
        if ch > ph:   seq.append("HH")
        elif ch < ph: seq.append("LH")
        if cl > pl:   seq.append("HL")
        elif cl < pl: seq.append("LL")

    sequence = " → ".join(seq) if seq else "Forming"

    fh, sh = swing_highs[0], swing_highs[-1]
    fl, sl = swing_lows[0],  swing_lows[-1]

    if sh > fh and sl > fl:   structure, trend = "HH / HL", "Bullish"
    elif sh < fh and sl < fl: structure, trend = "LH / LL", "Bearish"
    elif sh > fh and sl < fl: structure, trend = "HH / LL", "Neutral"
    else:                     structure, trend = "LH / HL", "Neutral"

    hd = abs(sh - fh) / (fh + 1e-9) * 100
    ld = abs(sl - fl) / (fl + 1e-9) * 100
    sp = min(int((hd + ld) * 5), 100)
    sl_label = "Strong" if sp >= 70 else ("Moderate" if sp >= 40 else "Weak")

    # BOS / CHoCH
    bos   = sh > fh and trend == "Bullish" and swing_highs[-1] > max(swing_highs[:-1])
    choch = (trend == "Bullish" and sl < fl) or (trend == "Bearish" and sh > fh)

    return {
        "structure":      structure,
        "trend":          trend,
        "sequence":       sequence,
        "swing_high":     round(sh, 2),
        "swing_low":      round(sl, 2),
        "strength_pct":   sp,
        "strength_label": sl_label,
        "bos":            bos,
        "choch":          choch,
    }

def detect_candlestick_patterns(candles: list) -> list:
    patterns = []
    if len(candles) < 3: return patterns
    c0, c1, c2 = candles[-3], candles[-2], candles[-1]

    def body(c):        return abs(c["close"] - c["open"])
    def rng(c):         return c["high"] - c["low"]
    def bull(c):        return c["close"] > c["open"]
    def bear(c):        return c["close"] < c["open"]
    def uw(c):          return c["high"] - max(c["close"], c["open"])
    def lw(c):          return min(c["close"], c["open"]) - c["low"]

    b2, r2 = body(c2), rng(c2)

    RELIABILITY = {
        "Hammer": "Moderate", "Bullish Engulfing": "High",
        "Morning Star": "High", "Bullish Marubozu": "High",
        "Dragonfly Doji": "Moderate", "Shooting Star": "Moderate",
        "Bearish Engulfing": "High", "Evening Star": "High",
        "Bearish Marubozu": "High", "Gravestone Doji": "Moderate",
        "Doji": "Low",
    }

    def add(name, direction, strength):
        patterns.append({"name": name, "direction": direction,
                         "strength": strength,
                         "reliability": RELIABILITY.get(name, "Moderate")})

    if bull(c2) and lw(c2) > b2 * 2 and uw(c2) < b2 * 0.3 and b2 > 0:
        add("Hammer", "Bullish", "Moderate")
    if bear(c1) and bull(c2) and c2["open"] < c1["close"] and c2["close"] > c1["open"]:
        add("Bullish Engulfing", "Bullish", "Strong")
    if bear(c0) and body(c1) < body(c0) * 0.3 and bull(c2) and c2["close"] > (c0["open"] + c0["close"]) / 2:
        add("Morning Star", "Bullish", "Strong")
    if bull(c2) and uw(c2) < b2 * 0.1 and lw(c2) < b2 * 0.1 and b2 > r2 * 0.85:
        add("Bullish Marubozu", "Bullish", "Strong")
    if b2 < r2 * 0.05 and lw(c2) > r2 * 0.7:
        add("Dragonfly Doji", "Bullish", "Moderate")
    if bear(c2) and uw(c2) > b2 * 2 and lw(c2) < b2 * 0.3 and b2 > 0:
        add("Shooting Star", "Bearish", "Moderate")
    if bull(c1) and bear(c2) and c2["open"] > c1["close"] and c2["close"] < c1["open"]:
        add("Bearish Engulfing", "Bearish", "Strong")
    if bull(c0) and body(c1) < body(c0) * 0.3 and bear(c2) and c2["close"] < (c0["open"] + c0["close"]) / 2:
        add("Evening Star", "Bearish", "Strong")
    if bear(c2) and uw(c2) < b2 * 0.1 and lw(c2) < b2 * 0.1 and b2 > r2 * 0.85:
        add("Bearish Marubozu", "Bearish", "Strong")
    if b2 < r2 * 0.05 and uw(c2) > r2 * 0.7:
        add("Gravestone Doji", "Bearish", "Moderate")
    if b2 < r2 * 0.05 and not any(p["name"] in ("Dragonfly Doji", "Gravestone Doji") for p in patterns):
        add("Doji", "Neutral", "Weak")

    return patterns

def detect_fvg(candles: list) -> list:
    fvgs = []
    window = candles[-30:] if len(candles) >= 30 else candles
    for i in range(1, len(window) - 1):
        cp, cn = window[i - 1], window[i + 1]
        if cp["high"] < cn["low"]:
            fvgs.append({"type": "Bullish", "low": round(cp["high"], 2),
                         "high": round(cn["low"], 2),
                         "midpoint": round((cp["high"] + cn["low"]) / 2, 2)})
        elif cp["low"] > cn["high"]:
            fvgs.append({"type": "Bearish", "low": round(cn["high"], 2),
                         "high": round(cp["low"], 2),
                         "midpoint": round((cp["low"] + cn["high"]) / 2, 2)})
    return fvgs[-3:]

def detect_liquidity(candles: list, current_price: float) -> dict:
    tol    = current_price * 0.002
    recent = candles[-20:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    rh, rl = max(highs), min(lows)
    eq_h   = len([h for h in highs if abs(h - rh) <= tol]) >= 2
    eq_l   = len([l for l in lows  if abs(l - rl) <= tol]) >= 2
    last   = candles[-1]
    ph = max(c["high"] for c in candles[-6:-1]) if len(candles) >= 6 else rh
    pl = min(c["low"]  for c in candles[-6:-1]) if len(candles) >= 6 else rl
    swept_high = last["high"] > ph and last["close"] < ph
    swept_low  = last["low"]  < pl and last["close"] > pl
    sweep = None
    if swept_high:
        sweep = {"direction": "Above previous high", "signal": "Potential bearish reversal"}
    elif swept_low:
        sweep = {"direction": "Below previous low",  "signal": "Potential bullish reversal"}
    return {
        "buy_side_level":     round(rh, 2),
        "sell_side_level":    round(rl, 2),
        "equal_highs":        eq_h,
        "equal_highs_level":  round(rh, 2) if eq_h else None,
        "equal_lows":         eq_l,
        "equal_lows_level":   round(rl, 2) if eq_l else None,
        "sweep":              sweep,
    }

def analyze_levels(candles: list, current_price: float) -> dict:
    tol    = current_price * 0.005
    recent = candles[-50:]
    sl     = round(min(c["low"]  for c in recent), 2)
    rl     = round(max(c["high"] for c in recent), 2)
    st     = sum(1 for c in recent if abs(c["low"]  - sl) <= tol)
    rt     = sum(1 for c in recent if abs(c["high"] - rl) <= tol)
    def lbl(t): return "High" if t >= 4 else ("Moderate" if t >= 2 else "Low")
    return {"support": sl, "support_touches": st, "support_strength": lbl(st),
            "resistance": rl, "resistance_touches": rt, "resistance_strength": lbl(rt)}

def multi_timeframe(symbol: str) -> list:
    frames = [("15m", 15), ("1H", 60), ("4H", 240), ("Daily", 1440)]
    results = []
    for lbl, mins in frames:
        candles = fetch_candles(symbol, mins)
        if len(candles) < 55:
            results.append({"label": lbl, "decision": "N/A",
                            "structure": "N/A", "trend": "N/A", "rsi": 0})
            continue
        closes = [c["close"] for c in candles]
        e20    = ema(closes, 20)
        e50    = ema(closes, 50)
        r      = rsi(closes, 14)
        ms     = detect_market_structure(candles)
        bull   = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
        bear   = e20 < e50 and r > 30 and ms["trend"] == "Bearish"
        dec    = "BUY" if bull else ("SELL" if bear else "HOLD")
        results.append({"label": lbl, "decision": dec,
                        "structure": ms["structure"], "trend": ms["trend"], "rsi": r})
    return results

def mtf_summary(frames: list) -> str:
    buys  = sum(1 for f in frames if f["decision"] == "BUY")
    sells = sum(1 for f in frames if f["decision"] == "SELL")
    if buys >= 3:    return "Long-term Bullish"
    if sells >= 3:   return "Long-term Bearish"
    if buys > sells: return "Short-term Bearish · Long-term Bullish — Wait for confirmation"
    if sells > buys: return "Short-term Bullish · Long-term Bearish — Wait for confirmation"
    return "Mixed — No clear bias. Wait for confirmation."

# ══════════════════════════════════════════════
#  CONFIDENCE SCORE  (max 100)
# ══════════════════════════════════════════════

def compute_confidence(ms: dict, e20: float, e50: float, r: float,
                        macd_line: float, macd_sig: float,
                        patterns: list, vol: dict, decision: str) -> dict:
    scores = {
        "Market Structure": 0,
        "EMA Alignment":    0,
        "RSI":              0,
        "MACD":             0,
        "Candlestick":      0,
        "Volume":           0,
    }
    is_buy  = "BUY"  in decision
    is_sell = "SELL" in decision

    if (is_buy  and ms["trend"] == "Bullish") or \
       (is_sell and ms["trend"] == "Bearish"):
        scores["Market Structure"] = 20

    if (is_buy and e20 > e50) or (is_sell and e20 < e50):
        scores["EMA Alignment"] = 15

    if is_buy  and 40 < r < 65: scores["RSI"] = 10
    elif is_sell and 35 < r < 60: scores["RSI"] = 10
    elif is_buy  and r <= 40:   scores["RSI"] = 5
    elif is_sell and r >= 65:   scores["RSI"] = 5

    if (is_buy and macd_line > macd_sig) or (is_sell and macd_line < macd_sig):
        scores["MACD"] = 15

    matching = [p for p in patterns if
                (is_buy  and p["direction"] == "Bullish") or
                (is_sell and p["direction"] == "Bearish")]
    if matching:
        scores["Candlestick"] = 20 if any(p["strength"] == "Strong" for p in matching) else 10

    if vol["relative"] >= 1.2:
        if (is_buy  and vol["buy_pressure"]  > 55) or \
           (is_sell and vol["sell_pressure"] > 55):
            scores["Volume"] = 20
        else:
            scores["Volume"] = 10
    elif vol["relative"] >= 0.8:
        scores["Volume"] = 5

    return {"breakdown": scores, "total": sum(scores.values())}

# ══════════════════════════════════════════════
#  FULL ANALYSIS
# ══════════════════════════════════════════════

def full_analysis(candles: list, current_price: float, symbol: str) -> dict:
    closes   = [c["close"] for c in candles] + [current_price]
    e20      = ema(closes, 20)
    e50      = ema(closes, 50)
    r        = rsi(closes, 14)
    atr14    = atr_calc(candles, 14)
    ml, ms_m, mh = macd_calc(closes)
    ms       = detect_market_structure(candles)
    vol      = volume_analysis(candles)
    patterns = detect_candlestick_patterns(candles)
    fvgs     = detect_fvg(candles)
    liq      = detect_liquidity(candles, current_price)
    levels   = analyze_levels(candles, current_price)
    frames   = multi_timeframe(symbol)
    bias     = mtf_summary(frames)

    # Decision
    bull = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
    bear = e20 < e50 and r > 30 and ms["trend"] == "Bearish"

    if bull:
        decision    = "BUY"
        stop_loss   = round(current_price - atr14 * 1.5, 2)
        take_profit = round(current_price + atr14 * 3.0, 2)
    elif bear:
        decision    = "SELL"
        stop_loss   = round(current_price + atr14 * 1.5, 2)
        take_profit = round(current_price - atr14 * 3.0, 2)
    else:
        decision    = "WAIT"
        stop_loss   = round(current_price - atr14 * 1.5, 2)
        take_profit = round(current_price + atr14 * 1.5, 2)

    sl_dist = abs(current_price - stop_loss)
    tp_dist = abs(take_profit - current_price)
    rr      = round(tp_dist / sl_dist, 1) if sl_dist > 0 else 0.0

    conf = compute_confidence(ms, e20, e50, r, ml, ms_m, patterns, vol, decision)

    # Build reason list — only TRUE reasons
    reasons = []
    if e20 < e50: reasons.append("EMA20 is below EMA50 — bearish trend")
    elif e20 > e50: reasons.append("EMA20 is above EMA50 — bullish trend")
    if ms["trend"] == "Bearish": reasons.append(f"Bearish market structure ({ms['sequence']})")
    elif ms["trend"] == "Bullish": reasons.append(f"Bullish market structure ({ms['sequence']})")
    rsi_lbl = rsi_label(r)
    if rsi_lbl == "Overbought":   reasons.append(f"RSI is overbought ({r}) — momentum weakening")
    elif rsi_lbl == "Oversold":   reasons.append(f"RSI is oversold ({r}) — potential reversal zone")
    else:                          reasons.append(f"RSI is neutral ({r}) — no extreme momentum")
    if vol["sell_pressure"] > 55: reasons.append("Volume favors sellers")
    elif vol["buy_pressure"] > 55: reasons.append("Volume favors buyers")
    else:                          reasons.append("Volume is balanced — no strong conviction")
    bear_pats = [p for p in patterns if p["direction"] == "Bearish"]
    bull_pats = [p for p in patterns if p["direction"] == "Bullish"]
    if bear_pats:
        reasons.append(f"{bear_pats[0]['name']} detected — bearish signal")
    elif bull_pats:
        reasons.append(f"{bull_pats[0]['name']} detected — bullish signal")
    else:
        reasons.append("No significant candlestick pattern detected")
    if ms["bos"]:   reasons.append("Break of Structure (BOS) confirmed")
    if ms["choch"]: reasons.append("Change of Character (CHoCH) — potential trend shift")
    if liq["sweep"]:
        reasons.append(f"Liquidity sweep {liq['sweep']['direction']} — {liq['sweep']['signal']}")

    trend_label = ms["trend"] if ms["trend"] in ("Bullish", "Bearish") else "Sideways"

    return {
        "decision":     decision,
        "trend":        trend_label,
        "confidence":   conf,
        "reasons":      reasons,
        "entry":        current_price,
        "stop_loss":    stop_loss,
        "take_profit":  take_profit,
        "rr":           rr,
        "risk_usd":     0,  # filled later
        "e20": e20, "e50": e50,
        "rsi14": r, "rsi_label": rsi_label(r),
        "atr14": atr14,
        "macd_line": ml, "macd_signal": ms_m, "macd_hist": mh,
        "ms": ms,
        "vol": vol,
        "patterns": patterns,
        "fvgs": fvgs,
        "liq": liq,
        "levels": levels,
        "frames": frames,
        "bias": bias,
    }

# ══════════════════════════════════════════════
#  AI NARRATIVE
# ══════════════════════════════════════════════

def get_ai_narrative(a: dict, symbol: str, price: float) -> str:
    reasons_str = "\n".join(f"- {r}" for r in a["reasons"])
    fallback = (
        f"Signal: {a['decision']} | Confidence: {a['confidence']['total']}%\n\n"
        f"The market is in a {a['trend']} {a['ms']['structure']} structure "
        f"({a['ms']['strength_label']} — {a['ms']['strength_pct']}%). "
        f"EMA20 (${a['e20']:,.2f}) is {'above' if a['e20'] > a['e50'] else 'below'} "
        f"EMA50 (${a['e50']:,.2f}), {'confirming bullish momentum' if a['e20'] > a['e50'] else 'confirming bearish pressure'}. "
        f"RSI at {a['rsi14']} is {a['rsi_label'].lower()}. "
        f"Volume: buyers {a['vol']['buy_pressure']}% / sellers {a['vol']['sell_pressure']}%.\n\n"
        f"Key reasons:\n{reasons_str}\n\n"
        f"Stop Loss: ${a['stop_loss']:,.2f} | Take Profit: ${a['take_profit']:,.2f} | R:R 1:{a['rr']}"
    )
    if not client: return fallback
    try:
        pat_str = ", ".join(f"{p['name']} ({p['direction']}, {p['reliability']} reliability)"
                            for p in a["patterns"]) or "None detected"
        prompt = f"""You are Aria, a professional crypto trading AI. Write a concise market analysis.
DO NOT repeat raw numbers without context. Explain what the data means for the trade.

Symbol: {symbol} | Price: ${price:,.2f}
Decision: {a['decision']} | Confidence: {a['confidence']['total']}%
Trend: {a['trend']} | Structure: {a['ms']['structure']} ({a['ms']['sequence']})
Strength: {a['ms']['strength_label']} ({a['ms']['strength_pct']}%)
EMA20: ${a['e20']:,.2f} | EMA50: ${a['e50']:,.2f}
RSI: {a['rsi14']} ({a['rsi_label']})
MACD: {a['macd_line']:+.2f} vs signal {a['macd_signal']:+.2f}
Volume: buy {a['vol']['buy_pressure']}% / sell {a['vol']['sell_pressure']}% (relative x{a['vol']['relative']})
Patterns: {pat_str}
Stop Loss: ${a['stop_loss']:,.2f} | Take Profit: ${a['take_profit']:,.2f} | R:R 1:{a['rr']}

Write:
SIGNAL: [decision]

REASON:
• [market structure with sequence]
• [EMA insight]
• [RSI insight]
• [volume insight]
• [candlestick insight]

RISK: [Low/Moderate/High] — [one sentence why]

CONCLUSION: [2–3 sentences explaining the full market picture and what to watch for]

Max 180 words. Be direct and specific."""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=280, temperature=0.4)
        return resp.choices[0].message.content.strip()
    except Exception:
        return fallback

# ══════════════════════════════════════════════
#  HTML HELPERS
# ══════════════════════════════════════════════

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0a0a0a; color: #d0d0d0; padding: 20px; max-width: 680px; margin: 0 auto; }
h1 { color: #fff; font-size: 20px; margin-bottom: 2px; }
.sub { color: #444; font-size: 12px; margin-bottom: 20px; }
.card { background: #141414; border: 1px solid #222; padding: 20px; border-radius: 12px; margin: 10px 0; }
.label { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.value { font-size: 22px; font-weight: bold; color: #fff; }
.value-sm { font-size: 15px; font-weight: bold; color: #fff; }
.decision-buy  { color: #2ecc71; font-size: 32px; font-weight: bold; letter-spacing: 2px; }
.decision-sell { color: #e74c3c; font-size: 32px; font-weight: bold; letter-spacing: 2px; }
.decision-wait { color: #f39c12; font-size: 32px; font-weight: bold; letter-spacing: 2px; }
.conf-bar-wrap { background: #1a1a1a; border-radius: 6px; height: 8px; margin-top: 6px; overflow: hidden; }
.row { display: flex; gap: 12px; flex-wrap: wrap; }
.col { flex: 1; min-width: 120px; }
.reason-list { list-style: none; padding: 0; margin: 0; }
.reason-list li { padding: 5px 0; color: #bbb; font-size: 13px; border-bottom: 1px solid #1e1e1e; }
.reason-list li:last-child { border-bottom: none; }
.reason-list li::before { content: "•  "; color: #555; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 8px 0; }
.metric { background: #0f0f0f; border-radius: 8px; padding: 12px; }
.metric .val { font-size: 14px; font-weight: bold; color: #fff; }
.metric .lbl { font-size: 10px; color: #555; margin-top: 3px; text-transform: uppercase; }
.badge { display: inline-block; padding: 4px 12px; border-radius: 16px; font-size: 12px; font-weight: bold; }
.adv-toggle { width: 100%; background: #141414; border: 1px solid #222; color: #888; font-size: 13px; padding: 12px 20px; border-radius: 12px; cursor: pointer; text-align: left; margin-top: 10px; }
.adv-toggle:hover { background: #1a1a1a; color: #ccc; }
.adv-section { display: none; }
.adv-section.open { display: block; }
.adv-card { background: #141414; border: 1px solid #1e1e1e; border-radius: 10px; padding: 16px; margin: 8px 0; }
.adv-card h3 { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
.adv-card .explain { font-size: 12px; color: #555; margin-bottom: 10px; font-style: italic; border-left: 2px solid #222; padding-left: 10px; }
.tf-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #1a1a1a; font-size: 13px; }
.tf-row:last-child { border-bottom: none; }
hr { border: none; border-top: 1px solid #1a1a1a; margin: 14px 0; }
.nav { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.btn { display: inline-block; padding: 9px 18px; border-radius: 8px; background: #1e1e1e; color: #ccc; text-decoration: none; font-size: 13px; border: 1px solid #2a2a2a; }
.btn-buy  { background: #0d2e1a; color: #2ecc71; border-color: #1a5c2e; }
.btn-sell { background: #2e0d0d; color: #e74c3c; border-color: #5c1a1a; }
details summary { cursor: pointer; outline: none; }
"""

def decision_class(d: str) -> str:
    return {"BUY": "decision-buy", "SELL": "decision-sell", "WAIT": "decision-wait"}.get(d, "decision-wait")

def trend_color(t: str) -> str:
    return {"Bullish": "#2ecc71", "Bearish": "#e74c3c"}.get(t, "#f39c12")

def conf_color(c: int) -> str:
    return "#2ecc71" if c >= 65 else ("#f39c12" if c >= 40 else "#e74c3c")

def pattern_text(patterns: list) -> str:
    if not patterns: return "No significant pattern detected"
    p = patterns[0]
    c = "#2ecc71" if p["direction"] == "Bullish" else ("#e74c3c" if p["direction"] == "Bearish" else "#888")
    return (f"<span style='color:{c};font-weight:bold'>{p['name']}</span> "
            f"<span style='color:#555;font-size:12px'>({p['direction']} · {p['reliability']} reliability)</span>")

def adv_patterns_html(patterns: list) -> str:
    if not patterns: return "<p style='color:#555;font-size:13px'>No pattern detected</p>"
    out = ""
    for p in patterns:
        c = "#2ecc71" if p["direction"] == "Bullish" else ("#e74c3c" if p["direction"] == "Bearish" else "#888")
        out += (f"<div style='padding:8px 0;border-bottom:1px solid #1a1a1a'>"
                f"<span style='color:{c};font-weight:bold'>{p['name']}</span>"
                f"<span style='color:#555;font-size:12px;margin-left:8px'>{p['direction']} · {p['strength']} · Reliability: {p['reliability']}</span>"
                f"</div>")
    return out

def adv_fvg_html(fvgs: list) -> str:
    if not fvgs: return "<p style='color:#555;font-size:13px'>No FVG detected in recent candles</p>"
    out = ""
    for g in reversed(fvgs):
        c = "#2ecc71" if g["type"] == "Bullish" else "#e74c3c"
        out += (f"<div style='padding:8px 0;border-bottom:1px solid #1a1a1a'>"
                f"<span style='color:{c};font-weight:bold'>{g['type']} FVG</span>"
                f"<span style='color:#888;font-size:12px;margin-left:8px'>${g['low']:,.2f} – ${g['high']:,.2f}</span>"
                f"<span style='color:#555;font-size:11px;margin-left:8px'>mid ${g['midpoint']:,.2f}</span>"
                f"</div>")
    return out

def adv_liq_html(liq: dict) -> str:
    out = (f"<div style='padding:6px 0;font-size:13px'>"
           f"<span style='color:#2ecc71'>Buy-Side Liquidity (BSL)</span>"
           f"<span style='color:#555;font-size:12px;margin-left:8px'>${liq['buy_side_level']:,.2f}</span></div>"
           f"<div style='padding:6px 0;font-size:13px'>"
           f"<span style='color:#e74c3c'>Sell-Side Liquidity (SSL)</span>"
           f"<span style='color:#555;font-size:12px;margin-left:8px'>${liq['sell_side_level']:,.2f}</span></div>")
    if liq["equal_highs"]:
        out += (f"<div style='padding:6px 0;font-size:13px;color:#f39c12'>"
                f"Equal Highs @ ${liq['equal_highs_level']:,.2f} — Buy-side liquidity resting above</div>")
    if liq["equal_lows"]:
        out += (f"<div style='padding:6px 0;font-size:13px;color:#f39c12'>"
                f"Equal Lows @ ${liq['equal_lows_level']:,.2f} — Sell-side liquidity resting below</div>")
    if liq["sweep"]:
        sw = liq["sweep"]
        out += (f"<div style='padding:8px;margin-top:6px;background:#1a1200;border-radius:6px;"
                f"border:1px solid #f39c12;font-size:13px'>"
                f"<span style='color:#f39c12;font-weight:bold'>⚠️ Liquidity Sweep</span> — "
                f"{sw['direction']} · {sw['signal']}</div>")
    return out

def adv_levels_html(levels: dict) -> str:
    def bar(s):
        w = {"High": 100, "Moderate": 60, "Low": 30}.get(s, 40)
        c = {"High": "#2ecc71", "Moderate": "#f39c12", "Low": "#e74c3c"}.get(s, "#888")
        return (f"<div style='background:#0f0f0f;border-radius:4px;height:5px;margin-top:4px'>"
                f"<div style='width:{w}%;height:100%;background:{c};border-radius:4px'></div></div>")
    out = ""
    for lbl, lvl, touches, strength, color in [
        ("Support",    levels["support"],    levels["support_touches"],    levels["support_strength"],    "#2ecc71"),
        ("Resistance", levels["resistance"], levels["resistance_touches"], levels["resistance_strength"], "#e74c3c"),
    ]:
        out += (f"<div style='padding:8px 0;border-bottom:1px solid #1a1a1a'>"
                f"<div style='display:flex;justify-content:space-between'>"
                f"<span style='color:{color};font-size:13px;font-weight:bold'>{lbl}</span>"
                f"<span style='color:#fff;font-size:13px'>${lvl:,.2f}</span></div>"
                f"<div style='color:#555;font-size:12px;margin-top:2px'>"
                f"Tested {touches}× · Strength: <span style='color:#ccc'>{strength}</span></div>"
                f"{bar(strength)}</div>")
    return out

def adv_conf_html(conf: dict) -> str:
    total = conf["total"]
    c     = conf_color(total)
    rows  = ""
    maxes = {"Market Structure": 20, "EMA Alignment": 15, "RSI": 10,
             "MACD": 15, "Candlestick": 20, "Volume": 20}
    for k, v in conf["breakdown"].items():
        mx  = maxes.get(k, 20)
        pct = int(v / mx * 100) if mx > 0 else 0
        bc  = "#2ecc71" if pct >= 70 else ("#f39c12" if pct >= 40 else "#555")
        rows += (f"<div style='padding:6px 0;border-bottom:1px solid #1a1a1a'>"
                 f"<div style='display:flex;justify-content:space-between;font-size:13px'>"
                 f"<span style='color:#888'>{k}</span>"
                 f"<span style='color:#fff'>{v} / {mx}</span></div>"
                 f"<div style='background:#0f0f0f;border-radius:4px;height:4px;margin-top:4px'>"
                 f"<div style='width:{pct}%;height:100%;background:{bc};border-radius:4px'></div></div>"
                 f"</div>")
    return (f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px'>"
            f"<span style='color:#888;font-size:13px'>Total Confidence</span>"
            f"<span style='color:{c};font-size:22px;font-weight:bold'>{total}%</span></div>"
            f"<div style='background:#0f0f0f;border-radius:6px;height:8px;overflow:hidden;margin-bottom:12px'>"
            f"<div style='width:{total}%;height:100%;background:{c}'></div></div>"
            f"{rows}")

# ══════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine"}


@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS: symbol = "BTCUSD"

    balance       = load_balance()
    position      = load_position()
    candles       = fetch_candles(symbol, 1440)
    current_price = fetch_current_price(symbol)

    if not candles:
        return HTMLResponse(
            f"<html><body style='background:#0a0a0a;color:#e74c3c;font-family:Arial;padding:30px'>"
            f"<h2>⚠️ Market data unavailable</h2>"
            f"<p style='margin-top:10px;color:#888'>Both Kraken and CoinGecko failed. Retry shortly.</p>"
            f"<a href='/analyze' style='color:#555;display:block;margin-top:20px'>↻ Retry</a>"
            f"</body></html>")

    a             = full_analysis(candles, current_price, symbol)
    risk_usd      = round(balance * MAX_RISK_PERCENT / 100, 2)
    a["risk_usd"] = risk_usd
    pos_size      = round(risk_usd / (current_price * 0.02), 6)
    narrative     = get_ai_narrative(a, symbol, current_price)

    dc   = decision_class(a["decision"])
    tc   = trend_color(a["trend"])
    cc   = conf_color(a["confidence"]["total"])
    conf = a["confidence"]["total"]

    # ── Open position block ──
    pl_block = ""
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)
        pc = "#2ecc71" if pl >= 0 else "#e74c3c"
        pl_block = (
            f"<div class='card'>"
            f"<div class='label'>Open Position</div>"
            f"<div style='margin-top:8px;font-size:13px'>"
            f"<span style='color:#ccc'>{position['side']} {position['size']} {symbol[:3]}</span>"
            f"<span style='color:#555;margin:0 8px'>@</span>"
            f"<span style='color:#ccc'>${position['entry_price']:,.2f}</span>"
            f"</div>"
            f"<div style='color:{pc};font-size:20px;font-weight:bold;margin-top:6px'>"
            f"P/L: ${pl:,.2f} ({pl_pct:.1f}%)</div>"
            f"<a href='/close' class='btn btn-sell' style='display:inline-block;margin-top:10px'>Close Position</a>"
            f"</div>"
        )

    # ── Reasons HTML ──
    reasons_html = "".join(f"<li>{r}</li>" for r in a["reasons"])

    # ── Multi-timeframe HTML ──
    tf_rows = ""
    for f in a["frames"]:
        sc = "#2ecc71" if f["decision"] == "BUY" else ("#e74c3c" if f["decision"] == "SELL" else "#888")
        tc2 = trend_color(f["trend"]) if f["trend"] in ("Bullish", "Bearish") else "#888"
        tf_rows += (
            f"<div class='tf-row'>"
            f"<span style='color:#888;width:50px'>{f['label']}</span>"
            f"<span style='color:{sc};font-weight:bold;width:50px'>{f['decision']}</span>"
            f"<span style='color:{tc2};font-size:12px'>{f['structure']}</span>"
            f"<span style='color:#555;font-size:11px'>RSI {f['rsi']}</span>"
            f"</div>"
        )

    bias_color = "#2ecc71" if "Bullish" in a["bias"] else ("#e74c3c" if "Bearish" in a["bias"] else "#f39c12")

    # ── RSI label for main dashboard ──
    rsi_c = "#e74c3c" if a["rsi_label"] == "Overbought" else ("#2ecc71" if a["rsi_label"] == "Oversold" else "#888")

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Aria — {symbol}</title>
<style>{CSS}</style>
</head><body>

<h1>🌱 Aria AI Trading Dashboard</h1>
<p class="sub">{symbol} · {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · Paper Trading</p>

<!-- ═══ MAIN DASHBOARD ═══ -->

<div class="card">
  <div class="label">Current Price</div>
  <div class="value">${current_price:,.2f}</div>
</div>

<div class="card">
  <div class="label">Decision</div>
  <div class="{dc}">{a['decision']}</div>
</div>

<div class="card">
  <div class="label">Confidence</div>
  <div class="value">{conf}%</div>
  <div class="conf-bar-wrap">
    <div style="width:{conf}%;height:100%;background:{cc};border-radius:6px"></div>
  </div>
</div>

<div class="row">
  <div class="card col">
    <div class="label">Trend</div>
    <div class="value-sm" style="color:{tc}">{a['trend']}</div>
  </div>
  <div class="card col">
    <div class="label">Market Structure</div>
    <div class="value-sm" style="color:{tc}">{a['ms']['sequence']}</div>
  </div>
</div>

<div class="card">
  <div class="label">Reason</div>
  <ul class="reason-list" style="margin-top:8px">
    {reasons_html}
  </ul>
</div>

<div class="row">
  <div class="card col">
    <div class="label">Entry Price</div>
    <div class="value-sm">${current_price:,.2f}</div>
  </div>
  <div class="card col">
    <div class="label">Stop Loss</div>
    <div class="value-sm" style="color:#e74c3c">${a['stop_loss']:,.2f}</div>
  </div>
</div>

<div class="row">
  <div class="card col">
    <div class="label">Take Profit</div>
    <div class="value-sm" style="color:#2ecc71">${a['take_profit']:,.2f}</div>
  </div>
  <div class="card col">
    <div class="label">Risk / Reward</div>
    <div class="value-sm">1 : {a['rr']}</div>
  </div>
</div>

<div class="card">
  <div class="label">Risk Amount (1% of balance)</div>
  <div class="value-sm">${risk_usd:.2f}</div>
</div>

{pl_block}

<div class="nav">
  <a href="/execute?symbol={symbol}&side=BUY"  class="btn btn-buy">Execute BUY</a>
  <a href="/execute?symbol={symbol}&side=SELL" class="btn btn-sell">Execute SELL</a>
  <a href="/analyze?symbol=BTCUSD" class="btn">₿ BTC</a>
  <a href="/analyze?symbol=ETHUSD" class="btn">Ξ ETH</a>
  <a href="/journal" class="btn">Journal</a>
</div>

<!-- ═══ ADVANCED ANALYSIS ═══ -->

<button class="adv-toggle" onclick="toggleAdv()">
  ▸ Full Market Analysis — tap to expand
</button>

<div class="adv-section" id="advSection">

  <div class="adv-card">
    <h3>Market Structure</h3>
    <div class="explain">Shows whether the market is making higher highs and higher lows (bullish) or lower highs and lower lows (bearish). The sequence below shows the last 5 swing points.</div>
    <div style="font-size:15px;color:{tc};font-weight:bold;margin-bottom:8px">{a['ms']['sequence']}</div>
    <div class="grid2">
      <div class="metric"><div class="val">{a['ms']['structure']}</div><div class="lbl">Classification</div></div>
      <div class="metric"><div class="val" style="color:{tc}">{a['ms']['trend']}</div><div class="lbl">Trend</div></div>
      <div class="metric"><div class="val">${a['ms']['swing_high']:,.2f}</div><div class="lbl">Swing High</div></div>
      <div class="metric"><div class="val">${a['ms']['swing_low']:,.2f}</div><div class="lbl">Swing Low</div></div>
      <div class="metric"><div class="val">{a['ms']['strength_label']}</div><div class="lbl">Strength</div></div>
      <div class="metric"><div class="val">{a['ms']['strength_pct']}%</div><div class="lbl">Strength %</div></div>
    </div>
    {"<div style='padding:8px;margin-top:6px;background:#0d2e1a;border-radius:6px;color:#2ecc71;font-size:13px'>✅ Break of Structure (BOS) confirmed — trend continuation signal</div>" if a['ms']['bos'] else ""}
    {"<div style='padding:8px;margin-top:6px;background:#2e1a0d;border-radius:6px;color:#f39c12;font-size:13px'>⚠️ Change of Character (CHoCH) detected — possible trend reversal</div>" if a['ms']['choch'] else ""}
  </div>

  <div class="adv-card">
    <h3>EMA — Exponential Moving Average</h3>
    <div class="explain">EMA20 above EMA50 suggests bullish trend. EMA20 below EMA50 suggests bearish trend.</div>
    <div class="grid2">
      <div class="metric"><div class="val">${a['e20']:,.2f}</div><div class="lbl">EMA 20</div></div>
      <div class="metric"><div class="val">${a['e50']:,.2f}</div><div class="lbl">EMA 50</div></div>
    </div>
    <div style="margin-top:8px;font-size:13px;color:#888">
      EMA20 is <b style="color:{'#2ecc71' if a['e20'] > a['e50'] else '#e74c3c'}">
      {'above' if a['e20'] > a['e50'] else 'below'}</b> EMA50 —
      {'bullish trend confirmed' if a['e20'] > a['e50'] else 'bearish trend confirmed'}
    </div>
  </div>

  <div class="adv-card">
    <h3>RSI — Relative Strength Index</h3>
    <div class="explain">Above 70 = Overbought. Below 30 = Oversold. Between 30–70 = Neutral.</div>
    <div class="grid2">
      <div class="metric"><div class="val">{a['rsi14']}</div><div class="lbl">RSI 14</div></div>
      <div class="metric"><div class="val" style="color:{rsi_c}">{a['rsi_label']}</div><div class="lbl">Status</div></div>
    </div>
  </div>

  <div class="adv-card">
    <h3>MACD</h3>
    <div class="explain">When the MACD line crosses above the signal line it is bullish. When it crosses below it is bearish.</div>
    <div class="grid2">
      <div class="metric"><div class="val">{a['macd_line']:+.2f}</div><div class="lbl">MACD Line</div></div>
      <div class="metric"><div class="val">{a['macd_signal']:+.2f}</div><div class="lbl">Signal</div></div>
      <div class="metric"><div class="val">{a['macd_hist']:+.2f}</div><div class="lbl">Histogram</div></div>
      <div class="metric"><div class="val">${a['atr14']:,.2f}</div><div class="lbl">ATR 14</div></div>
    </div>
  </div>

  <div class="adv-card">
    <h3>Volume</h3>
    <div class="explain">High volume confirms stronger moves. Low volume suggests weaker conviction. Buying pressure above 55% favors bulls, selling pressure above 55% favors bears.</div>
    <div class="grid2">
      <div class="metric"><div class="val">{a['vol']['current']:.2f}</div><div class="lbl">Current Volume</div></div>
      <div class="metric"><div class="val">{a['vol']['avg20']:.2f}</div><div class="lbl">20-Candle Avg</div></div>
      <div class="metric"><div class="val">x{a['vol']['relative']}</div><div class="lbl">Relative</div></div>
      <div class="metric"><div class="val" style="color:#2ecc71">{a['vol']['buy_pressure']}%</div><div class="lbl">Buying Pressure</div></div>
      <div class="metric"><div class="val" style="color:#e74c3c">{a['vol']['sell_pressure']}%</div><div class="lbl">Selling Pressure</div></div>
    </div>
  </div>

  <div class="adv-card">
    <h3>Candlestick Pattern</h3>
    <div class="explain">Shows whether a bullish or bearish reversal or continuation pattern has formed on the latest candles.</div>
    {adv_patterns_html(a['patterns'])}
  </div>

  <div class="adv-card">
    <h3>Support & Resistance</h3>
    <div class="explain">Key price levels where price has repeatedly reversed. More touches = stronger level.</div>
    {adv_levels_html(a['levels'])}
  </div>

  <div class="adv-card">
    <h3>Fair Value Gap (FVG)</h3>
    <div class="explain">A Fair Value Gap is an area where price moved so quickly that little trading occurred. Price often returns to these areas before continuing its trend.</div>
    {adv_fvg_html(a['fvgs'])}
  </div>

  <div class="adv-card">
    <h3>Liquidity Analysis</h3>
    <div class="explain">Liquidity shows where stop-loss orders and pending orders are likely clustered. These areas often attract price before reversals or breakouts. Equal highs and lows are especially significant — price is drawn to these levels to trigger orders before moving away.</div>
    {adv_liq_html(a['liq'])}
  </div>

  <div class="adv-card">
    <h3>Multi-Timeframe Analysis</h3>
    <div class="explain">Confirms whether the signal aligns across multiple timeframes. Alignment on 3 or more timeframes increases conviction.</div>
    {tf_rows}
    <div style="margin-top:10px;padding:10px;background:#0f0f0f;border-radius:8px;border-left:3px solid {bias_color}">
      <span style="color:{bias_color};font-size:13px;font-weight:bold">{a['bias']}</span>
    </div>
  </div>

  <div class="adv-card">
    <h3>Confidence Score Breakdown</h3>
    <div class="explain">Confidence is calculated from 6 indicators. Each contributes a maximum number of points. A score above 65% is considered high confidence.</div>
    {adv_conf_html(a['confidence'])}
  </div>

  <div class="adv-card">
    <h3>AI Explanation</h3>
    <div class="explain">Aria's full narrative analysis of the current market setup.</div>
    <div style="font-size:13px;line-height:1.8;color:#ccc;white-space:pre-wrap;margin-top:8px">{narrative}</div>
  </div>

</div>

<script>
function toggleAdv() {{
  var s = document.getElementById('advSection');
  var b = document.querySelector('.adv-toggle');
  if (s.classList.contains('open')) {{
    s.classList.remove('open');
    b.textContent = '▸ Full Market Analysis — tap to expand';
  }} else {{
    s.classList.add('open');
    b.textContent = '▾ Full Market Analysis — tap to collapse';
  }}
}}
</script>

</body></html>"""
    return HTMLResponse(html)


@app.get("/api/analyze")
async def api_analyze(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS: symbol = "BTCUSD"
    balance       = load_balance()
    position      = load_position()
    candles       = fetch_candles(symbol, 1440)
    current_price = fetch_current_price(symbol)
    if not candles: return {"error": "Market data unavailable"}
    a         = full_analysis(candles, current_price, symbol)
    risk_usd  = round(balance * MAX_RISK_PERCENT / 100, 2)
    a["risk_usd"] = risk_usd
    pl, pl_pct = 0.0, 0.0
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)
    return {
        "symbol": symbol, "price": current_price,
        "decision": a["decision"], "trend": a["trend"],
        "confidence": a["confidence"]["total"],
        "confidence_breakdown": a["confidence"]["breakdown"],
        "reasons": a["reasons"],
        "entry": a["entry"], "stop_loss": a["stop_loss"],
        "take_profit": a["take_profit"], "rr": a["rr"],
        "risk_usd": risk_usd,
        "market_structure": a["ms"],
        "ema20": a["e20"], "ema50": a["e50"],
        "rsi": a["rsi14"], "rsi_label": a["rsi_label"],
        "macd_line": a["macd_line"], "macd_signal": a["macd_signal"],
        "atr": a["atr14"], "volume": a["vol"],
        "patterns": a["patterns"], "fvgs": a["fvgs"],
        "liquidity": a["liq"], "levels": a["levels"],
        "multi_timeframe": a["frames"], "bias": a["bias"],
        "account_balance": balance, "open_position": position,
        "unrealized_pl": pl, "unrealized_pl_pct": pl_pct,
    }


def calc_pl(position: dict, current_price: float):
    e, sz, rk = position["entry_price"], position["size"], position["risk_amount"]
    pl = (current_price - e) * sz if position["side"] == "BUY" else (e - current_price) * sz
    return round(pl, 2), round((pl / rk * 100) if rk > 0 else 0, 2)


@app.get("/execute")
async def execute_trade(symbol: str = Query(...), side: str = Query(...)):
    symbol = symbol.upper(); side = side.upper()
    if symbol not in VALID_SYMBOLS:
        return HTMLResponse("<html><body style='background:#0a0a0a;color:#e74c3c;font-family:Arial;padding:30px'><h2>❌ Invalid symbol</h2><a href='/analyze' style='color:#555'>Back</a></body></html>")
    if side not in ("BUY", "SELL"):
        return HTMLResponse("<html><body style='background:#0a0a0a;color:#e74c3c;font-family:Arial;padding:30px'><h2>❌ Invalid side</h2><a href='/analyze' style='color:#555'>Back</a></body></html>")
    balance       = load_balance()
    current_price = fetch_current_price(symbol)
    risk_usd      = round(balance * MAX_RISK_PERCENT / 100, 2)
    size          = round(risk_usd / (current_price * 0.02), 6)
    pos = {"symbol": symbol, "side": side, "entry_price": current_price,
           "size": size, "risk_amount": risk_usd,
           "timestamp": datetime.utcnow().isoformat()}
    save_position(pos)
    save_to_journal({"action": "EXECUTE_TRADE", "symbol": symbol, "side": side,
                     "price": current_price, "size": size,
                     "timestamp": datetime.utcnow().isoformat()})
    color = "#2ecc71" if side == "BUY" else "#e74c3c"
    return HTMLResponse(
        f"<html><body style='background:#0a0a0a;color:#d0d0d0;font-family:Arial;padding:30px'>"
        f"<h2 style='color:{color}'>✅ Trade Executed</h2>"
        f"<p style='margin-top:10px'>{side} {size} {symbol} @ ${current_price:,.2f}</p>"
        f"<p style='color:#555;margin-top:4px'>Risk: ${risk_usd:.2f}</p>"
        f"<a href='/analyze?symbol={symbol}' style='color:#555;display:block;margin-top:20px'>← Back to Dashboard</a>"
        f"</body></html>")


@app.get("/close")
async def close_position():
    position = load_position()
    if not position:
        return HTMLResponse(
            "<html><body style='background:#0a0a0a;color:#d0d0d0;font-family:Arial;padding:30px'>"
            "<h2>No open position</h2>"
            "<a href='/analyze' style='color:#555;display:block;margin-top:20px'>Back</a></body></html>")
    balance       = load_balance()
    current_price = fetch_current_price(position["symbol"])
    pl, _         = calc_pl(position, current_price)
    new_balance   = round(balance + pl, 2)
    closed_symbol = position["symbol"]
    save_balance(new_balance); save_position(None)
    save_to_journal({"action": "CLOSE_POSITION", "symbol": closed_symbol,
                     "entry_price": position["entry_price"], "exit_price": current_price,
                     "pl": pl, "new_balance": new_balance,
                     "timestamp": datetime.utcnow().isoformat()})
    color = "#2ecc71" if pl >= 0 else "#e74c3c"
    return HTMLResponse(
        f"<html><body style='background:#0a0a0a;color:#d0d0d0;font-family:Arial;padding:30px'>"
        f"<h2>✅ Position Closed</h2>"
        f"<p style='margin-top:10px'>P/L: <b style='color:{color}'>${pl:,.2f}</b></p>"
        f"<p style='color:#555;margin-top:4px'>New Balance: ${new_balance:,.2f}</p>"
        f"<a href='/analyze?symbol={closed_symbol}' style='color:#555;display:block;margin-top:20px'>← Back to Dashboard</a>"
        f"</body></html>")


@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-50:]
        entries = [json.loads(l.strip()) for l in lines if l.strip()]
        return {"count": len(entries), "entries": entries}
    except Exception:
        return {"count": 0, "entries": [], "message": "No entries yet"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
