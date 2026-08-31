"""
analyzer.py - Stage 2: ANALYZE
================================
Responsibilities:
  - EMA 20 / 50
  - RSI 14
  - ATR 14
  - MACD
  - Market Structure (HH / HL / LH / LL) with swing sequence
  - Candlestick pattern detection with reliability ratings
  - Support & Resistance with touch count
  - Volume analysis (buy/sell pressure)
  - Fair Value Gap (FVG)
  - Liquidity analysis (BSL / SSL / sweeps / equal highs/lows)
  - Break of Structure (BOS) and Change of Character (CHoCH)
  - Multi-timeframe analysis
  - Trend strength percentage

No decisions are made here. Pure calculation only.
"""


# ──────────────────────────────────────────────
#  EMA
# ──────────────────────────────────────────────

def calc_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return round(sum(closes) / len(closes), 2)
    subset = closes[-period:]
    k = 2 / (period + 1)
    val = subset[0]
    for p in subset[1:]:
        val = p * k + val * (1 - k)
    return round(val, 2)


# ──────────────────────────────────────────────
#  RSI
# ──────────────────────────────────────────────

def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
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


# ──────────────────────────────────────────────
#  MACD
# ──────────────────────────────────────────────

def calc_macd(closes: list) -> tuple:
    """Returns (macd_line, signal_line, histogram)."""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    line   = round(calc_ema(closes, 12) - calc_ema(closes, 26), 2)
    signal = round(line * 0.2 + line * 0.8, 2)
    hist   = round(line - signal, 2)
    return line, signal, hist


# ──────────────────────────────────────────────
#  ATR
# ──────────────────────────────────────────────

def calc_atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        pc  = candles[i - 1]["close"]
        tr  = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - pc),
            abs(candles[i]["low"]  - pc),
        )
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 2)


# ──────────────────────────────────────────────
#  VOLUME
# ──────────────────────────────────────────────

def calc_volume(candles: list) -> dict:
    if len(candles) < 20:
        return {
            "current": 0, "avg20": 0, "relative": 1.0,
            "buy_pressure": 50, "sell_pressure": 50, "label": "Weak",
        }
    recent  = candles[-20:]
    current = round(candles[-1]["volume"], 2)
    avg20   = round(sum(c["volume"] for c in recent) / 20, 2)
    rel     = round(current / avg20, 2) if avg20 > 0 else 1.0
    buy_vol = sum(c["volume"] for c in recent if c["close"] >= c["open"])
    sel_vol = sum(c["volume"] for c in recent if c["close"] < c["open"])
    total   = buy_vol + sel_vol if (buy_vol + sel_vol) > 0 else 1
    bp      = round(buy_vol / total * 100, 1)
    sp      = round(sel_vol / total * 100, 1)
    label   = "Strong" if rel >= 1.3 else ("Moderate" if rel >= 0.8 else "Weak")
    return {
        "current":       current,
        "avg20":         avg20,
        "relative":      rel,
        "buy_pressure":  bp,
        "sell_pressure": sp,
        "label":         label,
    }


# ──────────────────────────────────────────────
#  MARKET STRUCTURE
# ──────────────────────────────────────────────

def calc_market_structure(candles: list) -> dict:
    """
    Real swing point detection.
    A swing high is a candle whose high is higher than the 2 candles
    on each side. A swing low is the opposite.
    This finds REAL market structure - not fake window comparisons.

    Sequence shows last 8 swing labels: HH, HL, LH, LL
    Trend determined by the last 2 swing highs and 2 swing lows.
    BOS = price broke above last swing high (bullish) or below last swing low.
    Fakeout filter: require price to CLOSE beyond the swing level, not just wick.
    """
    if len(candles) < 10:
        return {
            "structure": "Forming", "trend": "Neutral",
            "sequence": "Forming", "swing_high": 0, "swing_low": 0,
            "strength_pct": 0, "strength_label": "Weak",
            "bos": False, "choch": False,
        }

    recent = candles[-60:] if len(candles) >= 60 else candles
    n      = len(recent)
    left   = 2  # candles required on each side for a swing

    # Find real swing highs and swing lows
    swing_highs = []  # (index, price)
    swing_lows  = []

    for i in range(left, n - left):
        h = recent[i]["high"]
        l = recent[i]["low"]
        # Swing high: higher than all candles on both sides
        if all(h >= recent[j]["high"] for j in range(i-left, i+left+1) if j != i):
            swing_highs.append((i, h))
        # Swing low: lower than all candles on both sides
        if all(l <= recent[j]["low"] for j in range(i-left, i+left+1) if j != i):
            swing_lows.append((i, l))

    # Need at least 2 swing highs and 2 swing lows for structure
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Fallback to simple comparison
        sh = max(c["high"] for c in recent[-20:])
        sl = min(c["low"]  for c in recent[-20:])
        sh_prev = max(c["high"] for c in recent[-40:-20]) if len(recent) >= 40 else sh
        sl_prev = min(c["low"]  for c in recent[-40:-20]) if len(recent) >= 40 else sl
        trend = ("Bullish" if sh > sh_prev and sl > sl_prev
                 else "Bearish" if sh < sh_prev and sl < sl_prev
                 else "Neutral")
        return {
            "structure": "HH / HL" if trend=="Bullish" else "LH / LL" if trend=="Bearish" else "Mixed",
            "trend":          trend,
            "sequence":       "Forming",
            "swing_high":     round(sh, 2),
            "swing_low":      round(sl, 2),
            "strength_pct":   10,
            "strength_label": "Weak",
            "bos":            False,
            "choch":          False,
        }

    # Label each consecutive pair of swing highs
    sh_labels = []
    for i in range(1, len(swing_highs)):
        prev_h = swing_highs[i-1][1]
        curr_h = swing_highs[i][1]
        sh_labels.append("HH" if curr_h > prev_h else "LH")

    # Label each consecutive pair of swing lows
    sl_labels = []
    for i in range(1, len(swing_lows)):
        prev_l = swing_lows[i-1][1]
        curr_l = swing_lows[i][1]
        sl_labels.append("HL" if curr_l > prev_l else "LL")

    # Build sequence by interleaving (simplified — just show last 4 highs/lows)
    seq_parts = []
    max_len   = max(len(sh_labels), len(sl_labels))
    for i in range(min(max_len, 4)):
        if i < len(sh_labels): seq_parts.append(sh_labels[i])
        if i < len(sl_labels): seq_parts.append(sl_labels[i])
    sequence = " → ".join(seq_parts[-8:]) if seq_parts else "Forming"

    # Determine trend from last 2 swing highs and last 2 swing lows
    last_2_sh = swing_highs[-2:]
    last_2_sl = swing_lows[-2:]

    higher_highs = last_2_sh[-1][1] > last_2_sh[0][1]
    higher_lows  = last_2_sl[-1][1] > last_2_sl[0][1]
    lower_highs  = last_2_sh[-1][1] < last_2_sh[0][1]
    lower_lows   = last_2_sl[-1][1] < last_2_sl[0][1]

    if higher_highs and higher_lows:
        trend, structure = "Bullish", "HH / HL"
    elif lower_highs and lower_lows:
        trend, structure = "Bearish", "LH / LL"
    elif higher_highs and lower_lows:
        trend, structure = "Neutral", "HH / LL"
    else:
        trend, structure = "Neutral", "LH / HL"

    # Strength: based on how consistently the last 4 swings agree
    recent_sh = sh_labels[-4:] if sh_labels else []
    recent_sl = sl_labels[-4:] if sl_labels else []

    if trend == "Bullish":
        agree = sum(1 for x in recent_sh if x == "HH") +                 sum(1 for x in recent_sl if x == "HL")
        total = len(recent_sh) + len(recent_sl)
    elif trend == "Bearish":
        agree = sum(1 for x in recent_sh if x == "LH") +                 sum(1 for x in recent_sl if x == "LL")
        total = len(recent_sh) + len(recent_sl)
    else:
        agree, total = 0, 1

    sp = min(int((agree / max(total, 1)) * 100), 100)
    strength_label = "Strong" if sp >= 70 else ("Moderate" if sp >= 40 else "Weak")

    # BOS: last candle CLOSED beyond the last swing high/low
    # Using CLOSE not just high/low to filter fakeouts
    last_close = recent[-1]["close"]
    last_sh    = swing_highs[-1][1]
    last_sl    = swing_lows[-1][1]
    prev_sh    = swing_highs[-2][1] if len(swing_highs) >= 2 else last_sh

    bos   = (trend == "Bullish" and last_close > prev_sh)
    choch = ((trend == "Bearish" and last_close > last_sh) or
             (trend == "Bullish" and last_close < last_sl))

    return {
        "structure":      structure,
        "trend":          trend,
        "sequence":       sequence,
        "swing_high":     round(last_sh, 2),
        "swing_low":      round(last_sl, 2),
        "strength_pct":   sp,
        "strength_label": strength_label,
        "bos":            bos,
        "choch":          choch,
    }


# ──────────────────────────────────────────────
#  CANDLESTICK PATTERNS
# ──────────────────────────────────────────────

PATTERN_RELIABILITY = {
    "Hammer":            "Moderate",
    "Bullish Engulfing": "High",
    "Morning Star":      "High",
    "Bullish Marubozu":  "High",
    "Dragonfly Doji":    "Moderate",
    "Shooting Star":     "Moderate",
    "Bearish Engulfing": "High",
    "Evening Star":      "High",
    "Bearish Marubozu":  "High",
    "Gravestone Doji":   "Moderate",
    "Doji":              "Low",
}


def detect_patterns(candles: list) -> list:
    patterns = []
    if len(candles) < 3:
        return patterns

    c0, c1, c2 = candles[-3], candles[-2], candles[-1]

    def bd(c):   return abs(c["close"] - c["open"])
    def rg(c):   return c["high"] - c["low"]
    def bull(c): return c["close"] > c["open"]
    def bear(c): return c["close"] < c["open"]
    def uw(c):   return c["high"] - max(c["close"], c["open"])
    def lw(c):   return min(c["close"], c["open"]) - c["low"]

    b2, r2 = bd(c2), rg(c2)

    def add(name, direction, strength):
        patterns.append({
            "name":        name,
            "direction":   direction,
            "strength":    strength,
            "reliability": PATTERN_RELIABILITY.get(name, "Moderate"),
        })

    # Bullish
    if bull(c2) and lw(c2) > b2 * 2 and uw(c2) < b2 * 0.3 and b2 > 0:
        add("Hammer", "Bullish", "Moderate")
    if bear(c1) and bull(c2) and c2["open"] < c1["close"] and c2["close"] > c1["open"]:
        add("Bullish Engulfing", "Bullish", "Strong")
    if bear(c0) and bd(c1) < bd(c0) * 0.3 and bull(c2) and c2["close"] > (c0["open"] + c0["close"]) / 2:
        add("Morning Star", "Bullish", "Strong")
    if bull(c2) and uw(c2) < b2 * 0.1 and lw(c2) < b2 * 0.1 and b2 > r2 * 0.85:
        add("Bullish Marubozu", "Bullish", "Strong")
    if b2 < r2 * 0.05 and lw(c2) > r2 * 0.7:
        add("Dragonfly Doji", "Bullish", "Moderate")

    # Bearish
    if bear(c2) and uw(c2) > b2 * 2 and lw(c2) < b2 * 0.3 and b2 > 0:
        add("Shooting Star", "Bearish", "Moderate")
    if bull(c1) and bear(c2) and c2["open"] > c1["close"] and c2["close"] < c1["open"]:
        add("Bearish Engulfing", "Bearish", "Strong")
    if bull(c0) and bd(c1) < bd(c0) * 0.3 and bear(c2) and c2["close"] < (c0["open"] + c0["close"]) / 2:
        add("Evening Star", "Bearish", "Strong")
    if bear(c2) and uw(c2) < b2 * 0.1 and lw(c2) < b2 * 0.1 and b2 > r2 * 0.85:
        add("Bearish Marubozu", "Bearish", "Strong")
    if b2 < r2 * 0.05 and uw(c2) > r2 * 0.7:
        add("Gravestone Doji", "Bearish", "Moderate")

    # Neutral
    if b2 < r2 * 0.05 and not any(p["name"] in ("Dragonfly Doji", "Gravestone Doji") for p in patterns):
        add("Doji", "Neutral", "Weak")

    return patterns


# ──────────────────────────────────────────────
#  SUPPORT & RESISTANCE
# ──────────────────────────────────────────────

def calc_levels(candles: list, current_price: float) -> dict:
    tol    = current_price * 0.005
    recent = candles[-50:]
    sl     = round(min(c["low"]  for c in recent), 2)
    rl     = round(max(c["high"] for c in recent), 2)
    st     = sum(1 for c in recent if abs(c["low"]  - sl) <= tol)
    rt     = sum(1 for c in recent if abs(c["high"] - rl) <= tol)

    def lbl(t): return "High" if t >= 4 else ("Moderate" if t >= 2 else "Low")

    return {
        "support":            sl,
        "support_touches":    st,
        "support_strength":   lbl(st),
        "resistance":         rl,
        "resistance_touches": rt,
        "resistance_strength":lbl(rt),
    }


# ──────────────────────────────────────────────
#  FAIR VALUE GAP
# ──────────────────────────────────────────────

def detect_fvg(candles: list) -> list:
    """
    Bullish FVG: candle[i-1].high < candle[i+1].low  (gap up, unfilled)
    Bearish FVG: candle[i-1].low  > candle[i+1].high (gap down, unfilled)
    Returns up to 3 most recent gaps.
    """
    fvgs   = []
    window = candles[-30:] if len(candles) >= 30 else candles
    for i in range(1, len(window) - 1):
        cp, cn = window[i - 1], window[i + 1]
        if cp["high"] < cn["low"]:
            fvgs.append({
                "type":     "Bullish",
                "low":      round(cp["high"], 2),
                "high":     round(cn["low"], 2),
                "midpoint": round((cp["high"] + cn["low"]) / 2, 2),
            })
        elif cp["low"] > cn["high"]:
            fvgs.append({
                "type":     "Bearish",
                "low":      round(cn["high"], 2),
                "high":     round(cp["low"], 2),
                "midpoint": round((cp["low"] + cn["high"]) / 2, 2),
            })
    return fvgs[-3:]


# ──────────────────────────────────────────────
#  LIQUIDITY
# ──────────────────────────────────────────────

def detect_liquidity(candles: list, current_price: float) -> dict:
    """
    - Buy-Side Liquidity (BSL): above equal/recent highs
    - Sell-Side Liquidity (SSL): below equal/recent lows
    - Equal highs / equal lows: multiple candles within 0.2% of extreme
    - Sweep: last candle wick pierced prior level then closed back
    """
    tol    = current_price * 0.002
    recent = candles[-20:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    rh, rl = max(highs), min(lows)

    eq_h = len([h for h in highs if abs(h - rh) <= tol]) >= 2
    eq_l = len([l for l in lows  if abs(l - rl) <= tol]) >= 2

    last = candles[-1]
    ph   = max(c["high"] for c in candles[-6:-1]) if len(candles) >= 6 else rh
    pl   = min(c["low"]  for c in candles[-6:-1]) if len(candles) >= 6 else rl

    sweep = None
    if last["high"] > ph and last["close"] < ph:
        sweep = {"direction": "Above previous high", "signal": "Potential bearish reversal"}
    elif last["low"] < pl and last["close"] > pl:
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


# ──────────────────────────────────────────────
#  MULTI-TIMEFRAME
# ──────────────────────────────────────────────

def analyze_timeframe(candles: list, label: str) -> dict:
    if len(candles) < 55:
        return {"label": label, "decision": "N/A", "structure": "N/A", "trend": "N/A", "rsi": 0}
    closes = [c["close"] for c in candles]
    e20    = calc_ema(closes, 20)
    e50    = calc_ema(closes, 50)
    r      = calc_rsi(closes, 14)
    ms     = calc_market_structure(candles)
    bull   = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
    bear   = e20 < e50 and r > 30 and ms["trend"] == "Bearish"
    return {
        "label":     label,
        "decision":  "BUY" if bull else ("SELL" if bear else "HOLD"),
        "structure": ms["structure"],
        "trend":     ms["trend"],
        "rsi":       r,
    }


def multi_timeframe_analysis(symbol: str) -> list:
    from scanner import scan_timeframes
    tf_data = scan_timeframes(symbol)
    frames = []
    for label in ["15m", "1H", "4H", "Daily"]:
        candles = tf_data.get(label, [])
        if len(candles) >= 10:
            frames.append(analyze_timeframe(candles, label))
    return frames


def mtf_bias(frames: list) -> str:
    buys  = sum(1 for f in frames if f["decision"] == "BUY")
    sells = sum(1 for f in frames if f["decision"] == "SELL")
    if buys >= 3:    return "Long-term Bullish"
    if sells >= 3:   return "Long-term Bearish"
    if buys > sells: return "Short-term Bearish · Long-term Bullish - Wait for confirmation"
    if sells > buys: return "Short-term Bullish · Long-term Bearish - Wait for confirmation"
    return "Mixed - No clear bias. Wait."


# ──────────────────────────────────────────────
#  MAIN ANALYZE FUNCTION
# ──────────────────────────────────────────────

def analyze(scan_data: dict) -> dict:
    """
    Stage 2 - ANALYZE.
    Takes the raw scan snapshot and returns all calculated indicators.
    No decision is made here.
    """
    candles = scan_data["candles"]
    price   = scan_data["price"]
    symbol  = scan_data["symbol"]

    if len(candles) < 55:
        return {"error": "Insufficient candle data", "price": price, "symbol": symbol}

    closes = [c["close"] for c in candles] + [price]

    e20       = calc_ema(closes, 20)
    e50       = calc_ema(closes, 50)
    r14       = calc_rsi(closes, 14)
    atr14     = calc_atr(candles, 14)
    ml, ms_s, mh = calc_macd(closes)
    ms        = calc_market_structure(candles)
    vol       = calc_volume(candles)
    patterns  = detect_patterns(candles)
    fvgs      = detect_fvg(candles)
    liq       = detect_liquidity(candles, price)
    levels    = calc_levels(candles, price)
    frames    = multi_timeframe_analysis(symbol)
    bias      = mtf_bias(frames)

    return {
        "symbol":      symbol,
        "price":       price,
        "session":     scan_data.get("session", ""),
        "scanned_at":  scan_data.get("scanned_at", ""),
        # EMA
        "ema20":       e20,
        "ema50":       e50,
        # RSI
        "rsi14":       r14,
        "rsi_label":   rsi_label(r14),
        # MACD
        "macd_line":   ml,
        "macd_signal": ms_s,
        "macd_hist":   mh,
        # ATR
        "atr14":       atr14,
        # Market structure
        "ms":          ms,
        # Volume
        "vol":         vol,
        # Patterns
        "patterns":    patterns,
        # Levels
        "levels":      levels,
        # FVG
        "fvgs":        fvgs,
        # Liquidity
        "liq":         liq,
        # Multi-timeframe
        "frames":      frames,
        "bias":        bias,
    }
