from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import requests
import json
import os
from openai import OpenAI

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

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
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

def load_balance() -> float:
    try:
        if os.path.exists("paper_balance.txt"):
            with open("paper_balance.txt", "r") as f:
                return float(f.read().strip())
    except Exception:
        pass
    return 500.0

def save_balance(b: float):
    with open("paper_balance.txt", "w") as f: f.write(str(round(b, 2)))

def load_position():
    try:
        if os.path.exists("paper_position.json"):
            with open("paper_position.json", "r") as f:
                d = json.load(f)
                return d if d else None
    except Exception:
        pass
    return None

def save_position(p):
    with open("paper_position.json", "w") as f: json.dump(p, f)

def save_to_journal(e: dict):
    with open("trade_journal.txt", "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

# ─────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────

KRAKEN_PAIRS = {"BTCUSD": "XBTUSD", "ETHUSD": "ETHUSD"}
KRAKEN_INTERVALS = {"15m": 15, "1H": 60, "4H": 240, "Daily": 1440}

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
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd", timeout=10)
        return float(r.json()[coin]["usd"])
    except Exception:
        return 62000.0 if "BTC" in symbol else 3400.0

# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────

def ema(closes: list, period: int) -> float:
    if len(closes) < period: return round(sum(closes)/len(closes), 2)
    subset = closes[-period:]
    k = 2/(period+1); val = subset[0]
    for p in subset[1:]: val = p*k + val*(1-k)
    return round(val, 2)

def rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period+1: return 50.0
    rel = closes[-(period+1):]
    gains, losses = [], []
    for i in range(1, len(rel)):
        d = rel[i]-rel[i-1]
        (gains if d >= 0 else losses).append(abs(d))
    ag = sum(gains)/period if gains else 0.0
    al = sum(losses)/period if losses else 1e-9
    return round(100 - (100/(1+ag/al)), 2)

def macd(closes: list):
    if len(closes) < 26: return 0.0, 0.0, 0.0
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    line  = round(ema12 - ema26, 2)
    # signal = 9-period EMA of macd line (approximated)
    signal = round(line * (2/10) + line * (8/10), 2)
    hist   = round(line - signal, 2)
    return line, signal, hist

def atr(candles: list, period: int = 14) -> float:
    if len(candles) < period+1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        pc = candles[i-1]["close"]
        tr = max(candles[i]["high"]-candles[i]["low"],
                 abs(candles[i]["high"]-pc), abs(candles[i]["low"]-pc))
        trs.append(tr)
    return round(sum(trs[-period:])/period, 2)

def volume_analysis(candles: list) -> dict:
    if len(candles) < 20:
        return {"current": 0, "avg20": 0, "buy_pressure": 50, "sell_pressure": 50, "relative": 1.0}
    recent   = candles[-20:]
    current  = round(candles[-1]["volume"], 2)
    avg20    = round(sum(c["volume"] for c in recent)/20, 2)
    relative = round(current/avg20, 2) if avg20 > 0 else 1.0
    # Buy/sell pressure: candles where close > open = buying
    buy_vol  = sum(c["volume"] for c in recent if c["close"] >= c["open"])
    sell_vol = sum(c["volume"] for c in recent if c["close"] < c["open"])
    total    = buy_vol + sell_vol if (buy_vol + sell_vol) > 0 else 1
    return {
        "current":       current,
        "avg20":         avg20,
        "relative":      relative,
        "buy_pressure":  round(buy_vol/total*100, 1),
        "sell_pressure": round(sell_vol/total*100, 1),
    }

def detect_market_structure(candles: list) -> dict:
    recent = candles[-20:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    mid    = len(recent)//2
    fh, sh = max(highs[:mid]), max(highs[mid:])
    fl, sl = min(lows[:mid]),  min(lows[mid:])

    hh = sh > fh; hl = sl > fl
    lh = sh < fh; ll = sl < fl

    if hh and hl:   structure, trend = "HH / HL", "Bullish"
    elif lh and ll: structure, trend = "LH / LL", "Bearish"
    elif hh and ll: structure, trend = "HH / LL", "Neutral"
    else:           structure, trend = "LH / HL", "Neutral"

    # Trend strength: how decisive is the move?
    high_delta = abs(sh-fh)/(fh+1e-9)*100
    low_delta  = abs(sl-fl)/(fl+1e-9)*100
    strength_pct = min(int((high_delta+low_delta)*5), 100)

    if strength_pct >= 70:   strength_label = "Strong"
    elif strength_pct >= 40: strength_label = "Moderate"
    else:                    strength_label = "Weak"

    return {
        "structure":      structure,
        "trend":          trend,
        "swing_high":     round(sh, 2),
        "swing_low":      round(sl, 2),
        "strength_pct":   strength_pct,
        "strength_label": strength_label,
    }

# ─────────────────────────────────────────────
# CANDLESTICK PATTERN DETECTION
# ─────────────────────────────────────────────

def detect_candlestick_patterns(candles: list) -> list:
    """Detect common single and two-candle reversal/continuation patterns."""
    patterns = []
    if len(candles) < 3: return patterns

    c0 = candles[-3]  # 3 candles ago
    c1 = candles[-2]  # previous candle
    c2 = candles[-1]  # current/last candle

    def body(c):   return abs(c["close"] - c["open"])
    def range_(c): return c["high"] - c["low"]
    def is_bull(c): return c["close"] > c["open"]
    def is_bear(c): return c["close"] < c["open"]
    def upper_wick(c): return c["high"] - max(c["close"], c["open"])
    def lower_wick(c): return min(c["close"], c["open"]) - c["low"]

    b2, r2 = body(c2), range_(c2)

    # ── Bullish patterns ──────────────────────
    # Hammer: small body at top, long lower wick (>2x body), tiny upper wick
    if is_bull(c2) and lower_wick(c2) > body(c2)*2 and upper_wick(c2) < body(c2)*0.3 and b2 > 0:
        patterns.append({"name": "Hammer", "direction": "Bullish", "strength": "Moderate"})

    # Bullish Engulfing
    if is_bear(c1) and is_bull(c2) and c2["open"] < c1["close"] and c2["close"] > c1["open"]:
        patterns.append({"name": "Bullish Engulfing", "direction": "Bullish", "strength": "Strong"})

    # Morning Star (3-candle)
    if is_bear(c0) and body(c1) < body(c0)*0.3 and is_bull(c2) and c2["close"] > (c0["open"]+c0["close"])/2:
        patterns.append({"name": "Morning Star", "direction": "Bullish", "strength": "Strong"})

    # Bullish Marubozu: big bull candle, tiny wicks
    if is_bull(c2) and upper_wick(c2) < b2*0.1 and lower_wick(c2) < b2*0.1 and b2 > r2*0.85:
        patterns.append({"name": "Bullish Marubozu", "direction": "Bullish", "strength": "Strong"})

    # Dragonfly Doji: open ≈ close ≈ high, long lower wick
    if b2 < r2*0.05 and lower_wick(c2) > r2*0.7:
        patterns.append({"name": "Dragonfly Doji", "direction": "Bullish", "strength": "Moderate"})

    # ── Bearish patterns ──────────────────────
    # Shooting Star: small body at bottom, long upper wick
    if is_bear(c2) and upper_wick(c2) > body(c2)*2 and lower_wick(c2) < body(c2)*0.3 and b2 > 0:
        patterns.append({"name": "Shooting Star", "direction": "Bearish", "strength": "Moderate"})

    # Bearish Engulfing
    if is_bull(c1) and is_bear(c2) and c2["open"] > c1["close"] and c2["close"] < c1["open"]:
        patterns.append({"name": "Bearish Engulfing", "direction": "Bearish", "strength": "Strong"})

    # Evening Star (3-candle)
    if is_bull(c0) and body(c1) < body(c0)*0.3 and is_bear(c2) and c2["close"] < (c0["open"]+c0["close"])/2:
        patterns.append({"name": "Evening Star", "direction": "Bearish", "strength": "Strong"})

    # Bearish Marubozu
    if is_bear(c2) and upper_wick(c2) < b2*0.1 and lower_wick(c2) < b2*0.1 and b2 > r2*0.85:
        patterns.append({"name": "Bearish Marubozu", "direction": "Bearish", "strength": "Strong"})

    # Gravestone Doji: open ≈ close ≈ low, long upper wick
    if b2 < r2*0.05 and upper_wick(c2) > r2*0.7:
        patterns.append({"name": "Gravestone Doji", "direction": "Bearish", "strength": "Moderate"})

    # ── Neutral ──────────────────────────────
    # Doji: tiny body
    if b2 < r2*0.05:
        if not any(p["name"] in ("Dragonfly Doji", "Gravestone Doji") for p in patterns):
            patterns.append({"name": "Doji", "direction": "Neutral", "strength": "Weak"})

    return patterns

# ─────────────────────────────────────────────
# MULTI-TIMEFRAME
# ─────────────────────────────────────────────

def analyze_timeframe(symbol: str, interval_min: int, label: str) -> dict:
    candles = fetch_candles(symbol, interval_min)
    if len(candles) < 55:
        return {"label": label, "decision": "N/A", "structure": "N/A", "trend": "N/A",
                "rsi": 0, "ema20": 0, "ema50": 0}
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20); e50 = ema(closes, 50)
    r   = rsi(closes, 14)
    ms  = detect_market_structure(candles)

    bull = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
    bear = e20 < e50 and r > 30 and ms["trend"] == "Bearish"
    dec  = "BUY" if bull else ("SELL" if bear else "HOLD")

    return {"label": label, "decision": dec, "structure": ms["structure"],
            "trend": ms["trend"], "rsi": r, "ema20": e20, "ema50": e50}

def multi_timeframe(symbol: str) -> list:
    frames = [("15m", 15), ("1H", 60), ("4H", 240), ("Daily", 1440)]
    return [analyze_timeframe(symbol, mins, lbl) for lbl, mins in frames]

def mtf_summary(frames: list) -> dict:
    buys  = sum(1 for f in frames if f["decision"] == "BUY")
    sells = sum(1 for f in frames if f["decision"] == "SELL")
    if buys >= 3:   bias, wait = "Long-term Bullish",  False
    elif sells >= 3: bias, wait = "Long-term Bearish",  False
    elif buys > sells: bias, wait = "Short-term Bearish, Long-term Bullish", True
    elif sells > buys: bias, wait = "Short-term Bullish, Long-term Bearish", True
    else:              bias, wait = "Mixed — No clear bias", True
    return {"bias": bias, "wait_for_confirmation": wait, "buys": buys, "sells": sells}

# ─────────────────────────────────────────────
# CONFIDENCE SCORE
# ─────────────────────────────────────────────

def compute_confidence(ms: dict, closes: list, candles: list,
                        e20: float, e50: float, r: float,
                        macd_line: float, macd_sig: float,
                        patterns: list, vol: dict, decision: str) -> dict:
    scores = {
        "Market Structure": 0,
        "EMA Alignment":   0,
        "RSI":             0,
        "MACD":            0,
        "Candlestick":     0,
        "Volume":          0,
    }
    is_buy  = "BUY"  in decision
    is_sell = "SELL" in decision

    # Market Structure (20 pts)
    if (is_buy  and ms["trend"] == "Bullish") or \
       (is_sell and ms["trend"] == "Bearish"):
        scores["Market Structure"] = 20

    # EMA Alignment (15 pts)
    if (is_buy  and e20 > e50) or (is_sell and e20 < e50):
        scores["EMA Alignment"] = 15

    # RSI (10 pts)
    if is_buy  and 40 < r < 65: scores["RSI"] = 10
    elif is_sell and 35 < r < 60: scores["RSI"] = 10
    elif is_buy  and r < 40:    scores["RSI"] = 5   # oversold, potential bounce
    elif is_sell and r > 65:    scores["RSI"] = 5   # overbought, potential drop

    # MACD (15 pts)
    if (is_buy  and macd_line > macd_sig) or \
       (is_sell and macd_line < macd_sig):
        scores["MACD"] = 15

    # Candlestick (20 pts)
    matching = [p for p in patterns if
                (is_buy  and p["direction"] == "Bullish") or
                (is_sell and p["direction"] == "Bearish")]
    if matching:
        strong = any(p["strength"] == "Strong" for p in matching)
        scores["Candlestick"] = 20 if strong else 10

    # Volume (20 pts)
    if vol["relative"] >= 1.2:
        if (is_buy  and vol["buy_pressure"]  > 55) or \
           (is_sell and vol["sell_pressure"] > 55):
            scores["Volume"] = 20
        else:
            scores["Volume"] = 10
    elif vol["relative"] >= 0.8:
        scores["Volume"] = 5

    total = sum(scores.values())
    return {"breakdown": scores, "total": total}

# ─────────────────────────────────────────────
# MAIN COMPUTE
# ─────────────────────────────────────────────

def compute_all(candles: list, current_price: float) -> dict:
    closes = [c["close"] for c in candles] + [current_price]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r   = rsi(closes, 14)
    atr14 = atr(candles, 14)
    ml, ms_macd, mh = macd(closes)
    ms  = detect_market_structure(candles)
    vol = volume_analysis(candles)
    patterns = detect_candlestick_patterns(candles)

    support    = round(min(c["low"]  for c in candles[-20:]), 2)
    resistance = round(max(c["high"] for c in candles[-20:]), 2)

    bull = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
    bear = e20 < e50 and r > 30 and ms["trend"] == "Bearish"

    if bull:
        decision    = "BUY (Long)"
        stop_loss   = round(current_price - atr14*1.5, 2)
        take_profit = round(current_price + atr14*3.0, 2)
    elif bear:
        decision    = "SELL (Short)"
        stop_loss   = round(current_price + atr14*1.5, 2)
        take_profit = round(current_price - atr14*3.0, 2)
    else:
        decision    = "HOLD"
        stop_loss   = round(current_price - atr14*1.5, 2)
        take_profit = round(current_price + atr14*1.5, 2)

    sl_dist = abs(current_price - stop_loss)
    tp_dist = abs(take_profit - current_price)
    rr = round(tp_dist/sl_dist, 2) if sl_dist > 0 else 0.0

    conf = compute_confidence(ms, closes, candles, e20, e50, r,
                              ml, ms_macd, patterns, vol, decision)

    return {
        "decision":    decision,
        "ema20":       e20, "ema50": e50, "rsi14": r,
        "atr14":       atr14,
        "macd_line":   ml, "macd_signal": ms_macd, "macd_hist": mh,
        "support":     support, "resistance": resistance,
        "stop_loss":   stop_loss, "take_profit": take_profit,
        "rr_ratio":    rr,
        "structure":   ms["structure"],
        "trend":       ms["trend"],
        "strength_pct":   ms["strength_pct"],
        "strength_label": ms["strength_label"],
        "swing_high":  ms["swing_high"],
        "swing_low":   ms["swing_low"],
        "volume":      vol,
        "patterns":    patterns,
        "confidence":  conf,
    }

# ─────────────────────────────────────────────
# AI REASONING
# ─────────────────────────────────────────────

def get_ai_reasoning(symbol: str, price: float, ind: dict) -> str:
    pat_str = ", ".join(p["name"] for p in ind["patterns"]) if ind["patterns"] else "None detected"
    vol = ind["volume"]
    conf_total = ind["confidence"]["total"]

    fallback = f"""Signal: {ind['decision']} | Confidence: {conf_total}%

Market is in a {ind['trend']} {ind['structure']} structure ({ind['strength_label']} — {ind['strength_pct']}%).
EMA20 (${ind['ema20']:,.2f}) is {'above' if ind['ema20'] > ind['ema50'] else 'below'} EMA50 (${ind['ema50']:,.2f}), {'confirming bullish momentum' if ind['ema20'] > ind['ema50'] else 'confirming bearish pressure'}.
RSI at {ind['rsi14']} — {'neutral, room to run' if 40 < ind['rsi14'] < 60 else 'overbought, caution' if ind['rsi14'] > 65 else 'oversold, watch for reversal'}.
MACD line {ind['macd_line']:+.2f} vs signal {ind['macd_signal']:+.2f} ({'bullish crossover' if ind['macd_line'] > ind['macd_signal'] else 'bearish crossover'}).
Candlestick: {pat_str}.
Volume: current {vol['current']:.2f} vs 20-avg {vol['avg20']:.2f} (x{vol['relative']}). Buy pressure {vol['buy_pressure']}% / Sell pressure {vol['sell_pressure']}%.

Key reasons:
• {ind['structure']} market structure {'confirms uptrend' if ind['trend']=='Bullish' else 'confirms downtrend' if ind['trend']=='Bearish' else 'signals consolidation'}
• EMA alignment {'supports longs' if ind['ema20'] > ind['ema50'] else 'supports shorts'}
• RSI at {ind['rsi14']} {'not overbought, momentum intact' if ind['rsi14'] < 65 else 'overbought — risk of pullback'}
• {'Volume confirms move' if vol['relative'] >= 1.2 else 'Volume below average — low conviction'}
• Candlestick: {pat_str}

Stop Loss: ${ind['stop_loss']:,.2f} | Take Profit: ${ind['take_profit']:,.2f} | R:R 1:{ind['rr_ratio']}"""

    if not client:
        return fallback

    try:
        pat_dir = ", ".join(f"{p['name']} ({p['direction']}, {p['strength']})" for p in ind["patterns"]) or "None"
        conf_bd = "\n".join(f"  {k}: {v}/max" for k, v in ind["confidence"]["breakdown"].items())
        prompt = f"""You are Aria, a professional crypto trading AI. Write a structured market analysis report.
DO NOT just list numbers — explain what they mean together. Be concise but insightful.

Symbol: {symbol} | Price: ${price:,.2f}
Signal: {ind['decision']} | Confidence: {conf_total}%
Structure: {ind['structure']} ({ind['strength_label']} — {ind['strength_pct']}%) | Trend: {ind['trend']}
EMA20: ${ind['ema20']:,.2f} | EMA50: ${ind['ema50']:,.2f}
RSI14: {ind['rsi14']} | ATR14: ${ind['atr14']:,.2f}
MACD Line: {ind['macd_line']} | MACD Signal: {ind['macd_signal']} | Hist: {ind['macd_hist']}
Support: ${ind['support']:,.2f} | Resistance: ${ind['resistance']:,.2f}
Stop Loss: ${ind['stop_loss']:,.2f} | Take Profit: ${ind['take_profit']:,.2f} | R:R 1:{ind['rr_ratio']}
Volume: current {vol['current']:.1f} vs avg {vol['avg20']:.1f} (x{vol['relative']}) | Buy {vol['buy_pressure']}% / Sell {vol['sell_pressure']}%
Candlestick Patterns: {pat_dir}
Confidence Breakdown:
{conf_bd}

Write the response in this format:
SIGNAL: [decision]
REASON:
• [market structure insight with numbers]
• [EMA insight with numbers]
• [RSI insight]
• [MACD insight]
• [volume insight]
• [candlestick insight]
RISK ASSESSMENT: [Low/Moderate/High] — [one sentence why]
CONCLUSION: [2-3 sentence narrative explaining the overall market picture and what to watch for]
Max 200 words."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return fallback

# ─────────────────────────────────────────────
# HTML HELPERS
# ─────────────────────────────────────────────

def conf_bar_html(total: int, breakdown: dict) -> str:
    color = "#2ecc71" if total >= 70 else ("#f39c12" if total >= 45 else "#e74c3c")
    rows  = "".join(
        f"<tr><td style='color:#888;padding:2px 8px 2px 0'>{k}</td>"
        f"<td style='color:#fff'>{v} pts</td></tr>"
        for k, v in breakdown.items()
    )
    return f"""
<div style='margin:10px 0'>
  <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px'>
    <span style='font-size:12px;color:#888'>Confidence Score</span>
    <span style='font-weight:bold;color:{color};font-size:16px'>{total}%</span>
  </div>
  <div style='background:#111;border-radius:6px;overflow:hidden;height:10px'>
    <div style='width:{total}%;height:100%;background:{color}'></div>
  </div>
  <details style='margin-top:8px;font-size:12px;color:#888;cursor:pointer'>
    <summary>Score breakdown</summary>
    <table style='margin-top:6px'>{rows}</table>
  </details>
</div>"""

def pattern_badges(patterns: list) -> str:
    if not patterns: return "<span style='color:#555;font-size:13px'>No pattern detected</span>"
    out = ""
    for p in patterns:
        c = "#2ecc71" if p["direction"]=="Bullish" else ("#e74c3c" if p["direction"]=="Bearish" else "#888")
        out += f"<span style='display:inline-block;margin:3px;padding:4px 10px;border-radius:16px;border:1px solid {c};color:{c};font-size:12px'>{p['name']} · {p['strength']}</span>"
    return out

def mtf_html(frames: list, summary: dict) -> str:
    rows = ""
    for f in frames:
        sc = "#2ecc71" if f["decision"]=="BUY" else ("#e74c3c" if f["decision"]=="SELL" else "#888")
        tc = "#2ecc71" if f["trend"]=="Bullish" else ("#e74c3c" if f["trend"]=="Bearish" else "#888")
        rows += f"""
<div style='background:#111;border-radius:8px;padding:12px;margin:6px 0;display:flex;justify-content:space-between;align-items:center'>
  <span style='color:#aaa;font-weight:bold;width:50px'>{f['label']}</span>
  <span style='color:{sc};font-weight:bold;width:60px'>{f['decision']}</span>
  <span style='color:{tc};font-size:12px'>{f['structure']}</span>
  <span style='color:#555;font-size:11px'>RSI {f['rsi']}</span>
</div>"""

    bias_color = "#2ecc71" if "Bullish" in summary["bias"] else ("#e74c3c" if "Bearish" in summary["bias"] else "#f39c12")
    wait_note  = "<p style='color:#f39c12;font-size:12px;margin-top:8px'>⚠️ Wait for confirmation — timeframes conflict</p>" if summary["wait_for_confirmation"] else ""
    return f"""
<div class='card'>
  <h3>🕐 Multi-Timeframe Analysis</h3>
  {rows}
  <div style='margin-top:12px;padding:10px;background:#111;border-radius:8px;border-left:3px solid {bias_color}'>
    <span style='color:{bias_color};font-weight:bold'>{summary['bias']}</span>
    {wait_note}
  </div>
</div>"""

def calc_pl(position: dict, current_price: float):
    e, sz, rk = position["entry_price"], position["size"], position["risk_amount"]
    pl = (current_price-e)*sz if position["side"]=="BUY" else (e-current_price)*sz
    return round(pl,2), round((pl/rk*100) if rk>0 else 0, 2)

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

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
        return HTMLResponse("""<html><body style='background:#111;color:#e74c3c;font-family:Arial;padding:30px'>
        <h2>⚠️ Market data unavailable</h2><p>Kraken and CoinGecko both failed. Retry shortly.</p>
        <a href='/analyze' style='color:#aaa'>↻ Retry</a></body></html>""")

    ind   = compute_all(candles, current_price)
    ai    = get_ai_reasoning(symbol, current_price, ind)
    frames= multi_timeframe(symbol)
    summ  = mtf_summary(frames)

    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    position_size = round(risk_amount / (current_price * 0.02), 6)

    vol = ind["volume"]
    conf= ind["confidence"]
    sig_color   = "#2ecc71" if "BUY" in ind["decision"] else ("#e74c3c" if "SELL" in ind["decision"] else "#f39c12")
    trend_color = "#2ecc71" if ind["trend"]=="Bullish" else ("#e74c3c" if ind["trend"]=="Bearish" else "#f39c12")

    pl_block = ""
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)
        pc = "#2ecc71" if pl >= 0 else "#e74c3c"
        pl_block = (f"<div class='card'><h3>📊 Open Position</h3>"
                    f"<p>Side: <b>{position['side']}</b> | Entry: <b>${position['entry_price']:,.2f}</b> | Size: {position['size']} {symbol[:3]}</p>"
                    f"<p style='color:{pc};font-size:18px;font-weight:bold'>P/L: ${pl:,.2f} ({pl_pct:.1f}%)</p>"
                    f"<a href='/close' class='btn btn-sell'>❌ Close Position</a></div>")
    else:
        pl_block = "<div class='card'><p style='color:#555'>No open position</p></div>"

    html = f"""<!DOCTYPE html>
<html><head><title>Aria Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#0d0d0d;color:#ddd;padding:24px}}
  h1{{color:#fff;margin-bottom:4px}}
  .sub{{color:#444;font-size:12px;margin-bottom:20px}}
  .card{{background:#1a1a1a;border:1px solid #252525;padding:20px;border-radius:12px;margin:12px 0}}
  .card h2{{color:#fff;margin-bottom:14px}}
  .card h3{{color:#999;margin-bottom:10px;font-size:14px;text-transform:uppercase;letter-spacing:1px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:12px 0}}
  .metric{{background:#111;border-radius:8px;padding:12px;text-align:center}}
  .metric .val{{font-size:14px;font-weight:bold;color:#fff}}
  .metric .lbl{{font-size:10px;color:#555;margin-top:4px;text-transform:uppercase}}
  .badge{{display:inline-block;padding:6px 18px;border-radius:20px;font-weight:bold;font-size:15px;color:#fff;background:{sig_color}}}
  .struct{{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;border:1px solid {trend_color};color:{trend_color}}}
  .reason{{background:#111;border-radius:8px;padding:16px;font-size:13px;line-height:1.8;color:#ccc;white-space:pre-wrap;margin-top:10px}}
  .nav{{margin-top:16px;display:flex;gap:10px;flex-wrap:wrap}}
  .btn{{display:inline-block;padding:9px 18px;border-radius:8px;background:#2a2a2a;color:#ddd;text-decoration:none;font-size:13px}}
  .btn-buy{{background:#1a5c2e;color:#2ecc71}}
  .btn-sell{{background:#5c1a1a;color:#e74c3c}}
  hr{{border:none;border-top:1px solid #1e1e1e;margin:14px 0}}
  details summary{{outline:none}}
</style>
</head><body>
<h1>🌱 Aria AI Trading Dashboard</h1>
<p class="sub">Paper Trading · {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · Data: Kraken / CoinGecko</p>

<div class="card">
  <h2>{symbol} · Real-Time Analysis</h2>
  <p style="margin-bottom:10px">
    Signal: <span class="badge">{ind['decision']}</span>
    &nbsp;
    Structure: <span class="struct">{ind['structure']}</span>
    &nbsp;
    <span style="color:{trend_color};font-size:13px">Trend Strength: <b>{ind['strength_label']} ({ind['strength_pct']}%)</b></span>
  </p>

  {conf_bar_html(conf['total'], conf['breakdown'])}

  <div class="grid">
    <div class="metric"><div class="val">${current_price:,.2f}</div><div class="lbl">Price</div></div>
    <div class="metric"><div class="val">${ind['ema20']:,.2f}</div><div class="lbl">EMA 20</div></div>
    <div class="metric"><div class="val">${ind['ema50']:,.2f}</div><div class="lbl">EMA 50</div></div>
    <div class="metric"><div class="val">{ind['rsi14']}</div><div class="lbl">RSI 14</div></div>
    <div class="metric"><div class="val">{ind['macd_line']:+.2f}</div><div class="lbl">MACD</div></div>
    <div class="metric"><div class="val">${ind['atr14']:,.2f}</div><div class="lbl">ATR 14</div></div>
    <div class="metric"><div class="val">${ind['swing_high']:,.2f}</div><div class="lbl">Swing High</div></div>
    <div class="metric"><div class="val">${ind['swing_low']:,.2f}</div><div class="lbl">Swing Low</div></div>
    <div class="metric"><div class="val">${ind['support']:,.2f}</div><div class="lbl">Support</div></div>
    <div class="metric"><div class="val">${ind['resistance']:,.2f}</div><div class="lbl">Resistance</div></div>
    <div class="metric"><div class="val">${ind['stop_loss']:,.2f}</div><div class="lbl">Stop Loss</div></div>
    <div class="metric"><div class="val">${ind['take_profit']:,.2f}</div><div class="lbl">Take Profit</div></div>
    <div class="metric"><div class="val">1:{ind['rr_ratio']}</div><div class="lbl">Risk/Reward</div></div>
    <div class="metric"><div class="val">${risk_amount:.2f}</div><div class="lbl">Risk (1%)</div></div>
    <div class="metric"><div class="val">{position_size}</div><div class="lbl">Size ({symbol[:3]})</div></div>
    <div class="metric"><div class="val">${balance:,.2f}</div><div class="lbl">Balance</div></div>
  </div>

  <hr>
  <h3>📊 Volume</h3>
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(110px,1fr))">
    <div class="metric"><div class="val">{vol['current']:.2f}</div><div class="lbl">Current Vol</div></div>
    <div class="metric"><div class="val">{vol['avg20']:.2f}</div><div class="lbl">20-Avg Vol</div></div>
    <div class="metric"><div class="val">x{vol['relative']}</div><div class="lbl">Relative</div></div>
    <div class="metric"><div class="val" style="color:#2ecc71">{vol['buy_pressure']}%</div><div class="lbl">Buy Pressure</div></div>
    <div class="metric"><div class="val" style="color:#e74c3c">{vol['sell_pressure']}%</div><div class="lbl">Sell Pressure</div></div>
  </div>

  <hr>
  <h3>🕯️ Candlestick Pattern</h3>
  <div style="margin:8px 0">{pattern_badges(ind['patterns'])}</div>

  <hr>
  <h3>🤖 AI Reasoning</h3>
  <div class="reason">{ai}</div>
</div>

{mtf_html(frames, summ)}

{pl_block}

<div class="nav">
  <a href="/execute?symbol={symbol}&side=BUY" class="btn btn-buy">🟢 Execute BUY</a>
  <a href="/execute?symbol={symbol}&side=SELL" class="btn btn-sell">🔴 Execute SELL</a>
  <a href="/analyze?symbol=BTCUSD" class="btn">₿ BTC</a>
  <a href="/analyze?symbol=ETHUSD" class="btn">Ξ ETH</a>
  <a href="/journal" class="btn">📖 Journal</a>
</div>
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
    ind    = compute_all(candles, current_price)
    ai     = get_ai_reasoning(symbol, current_price, ind)
    frames = multi_timeframe(symbol)
    summ   = mtf_summary(frames)
    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    position_size = round(risk_amount / (current_price * 0.02), 6)
    pl, pl_pct = 0.0, 0.0
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)
    return {
        "symbol": symbol, "price": current_price,
        "indicators": ind, "decision": ind["decision"],
        "confidence": ind["confidence"]["total"],
        "confidence_breakdown": ind["confidence"]["breakdown"],
        "multi_timeframe": frames, "mtf_summary": summ,
        "patterns": ind["patterns"],
        "ai_reasoning": ai,
        "risk_amount_usd": risk_amount, "position_size": position_size,
        "account_balance": balance, "open_position": position,
        "unrealized_pl": pl, "unrealized_pl_pct": pl_pct,
    }


@app.get("/execute")
async def execute_trade(symbol: str = Query(...), side: str = Query(...)):
    symbol = symbol.upper(); side = side.upper()
    if symbol not in VALID_SYMBOLS:
        return HTMLResponse("<h2>❌ Invalid symbol</h2><a href='/analyze'>Back</a>")
    if side not in ("BUY", "SELL"):
        return HTMLResponse("<h2>❌ Invalid side</h2><a href='/analyze'>Back</a>")
    balance       = load_balance()
    current_price = fetch_current_price(symbol)
    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    size          = round(risk_amount / (current_price * 0.02), 6)
    pos = {"symbol": symbol, "side": side, "entry_price": current_price,
           "size": size, "risk_amount": risk_amount, "timestamp": datetime.utcnow().isoformat()}
    save_position(pos)
    save_to_journal({"action": "EXECUTE_TRADE", "symbol": symbol, "side": side,
                     "price": current_price, "size": size, "timestamp": datetime.utcnow().isoformat()})
    return HTMLResponse(
        f"<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
        f"<h2 style='color:#2ecc71'>✅ Paper Trade Executed</h2>"
        f"<p>{side} {size} {symbol} at ${current_price:,.2f}</p><p>Risk: ${risk_amount:.2f}</p>"
        f"<a href='/analyze?symbol={symbol}' style='color:#aaa'>← Back to Dashboard</a></body></html>")


@app.get("/close")
async def close_position():
    position = load_position()
    if not position:
        return HTMLResponse("<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
                            "<h2>No open position</h2><a href='/analyze' style='color:#aaa'>Back</a></body></html>")
    balance       = load_balance()
    current_price = fetch_current_price(position["symbol"])
    pl, _         = calc_pl(position, current_price)
    new_balance   = round(balance + pl, 2)
    closed_symbol = position["symbol"]
    save_balance(new_balance); save_position(None)
    save_to_journal({"action": "CLOSE_POSITION", "symbol": closed_symbol,
                     "entry_price": position["entry_price"], "exit_price": current_price,
                     "pl": pl, "new_balance": new_balance, "timestamp": datetime.utcnow().isoformat()})
    color = "#2ecc71" if pl >= 0 else "#e74c3c"
    return HTMLResponse(
        f"<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
        f"<h2>✅ Position Closed</h2>"
        f"<p>P/L: <b style='color:{color}'>${pl:,.2f}</b></p>"
        f"<p>New Balance: <b>${new_balance:,.2f}</b></p>"
        f"<a href='/analyze?symbol={closed_symbol}' style='color:#aaa'>← Back</a></body></html>")


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
