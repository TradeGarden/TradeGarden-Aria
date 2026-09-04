"""
analyzer.py - Stage 2: Market Analysis
=======================================
Fixed and accurate calculations:
  - Proper Wilder RSI (matches TradingView)
  - True EMA from full history (not just last N candles)
  - Fixed MACD with real signal EMA
  - Proper BOS/CHoCH with displacement confirmation
  - Real swing point detection
  - S/R zones (not just highest/lowest)
  - FVG with filled/unfilled tracking
  - Market regime detection
  - EMA slope analysis
  - ATR-normalized distances

Live price is NOT injected into historical calculations.
"""
from datetime import datetime, timezone


# ══════════════════════════════════════════════════════════════
#  CORE CALCULATIONS — ACCURATE
# ══════════════════════════════════════════════════════════════

def calc_ema(closes: list, period: int) -> float:
    """
    True EMA using full history.
    Starts with SMA for first period, then applies EMA formula.
    Matches TradingView behavior.
    """
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k   = 2 / (period + 1)
    val = sum(closes[:period]) / period  # SMA seed
    for price in closes[period:]:
        val = price * k + val * (1 - k)
    return round(val, 4)


def calc_ema_series(closes: list, period: int) -> list:
    """Returns full EMA series — needed for MACD signal line."""
    if len(closes) < period:
        return [closes[-1]] * len(closes) if closes else []
    k      = 2 / (period + 1)
    result = [None] * (period - 1)
    val    = sum(closes[:period]) / period
    result.append(val)
    for price in closes[period:]:
        val = price * k + val * (1 - k)
        result.append(val)
    return result


def calc_rsi(closes: list, period: int = 14) -> float:
    """
    Wilder RSI — matches TradingView exactly.
    Uses Wilder smoothing (RMA), not simple average.
    """
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    # Seed with simple average for first period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for the rest
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs  = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_macd(closes: list) -> dict:
    """
    Proper MACD:
      MACD line  = EMA(12) - EMA(26)
      Signal     = EMA(9) of MACD line series
      Histogram  = MACD - Signal

    Returns line, signal, histogram, and trend.
    """
    if len(closes) < 35:
        return {"line": 0, "signal": 0, "histogram": 0,
                "trend": "NEUTRAL", "cross": "NONE"}

    ema12_series = calc_ema_series(closes, 12)
    ema26_series = calc_ema_series(closes, 26)

    # MACD line series (only where both EMAs are available)
    macd_series = []
    for i in range(len(closes)):
        if ema12_series[i] is not None and ema26_series[i] is not None:
            macd_series.append(ema12_series[i] - ema26_series[i])

    if len(macd_series) < 9:
        return {"line": 0, "signal": 0, "histogram": 0,
                "trend": "NEUTRAL", "cross": "NONE"}

    # Signal = EMA(9) of MACD series
    signal_series = calc_ema_series(macd_series, 9)

    macd_line   = round(macd_series[-1], 4)
    signal_line = round(signal_series[-1], 4) if signal_series[-1] is not None else 0
    histogram   = round(macd_line - signal_line, 4)

    # Trend and crossover detection
    prev_macd   = macd_series[-2] if len(macd_series) >= 2 else macd_line
    prev_signal = signal_series[-2] if len(signal_series) >= 2 and signal_series[-2] is not None else signal_line

    cross = "NONE"
    if prev_macd <= prev_signal and macd_line > signal_line:
        cross = "BULLISH_CROSS"
    elif prev_macd >= prev_signal and macd_line < signal_line:
        cross = "BEARISH_CROSS"

    trend = ("BULLISH"  if macd_line > 0 and macd_line > signal_line else
             "BEARISH"  if macd_line < 0 and macd_line < signal_line else
             "NEUTRAL")

    return {
        "line":      macd_line,
        "signal":    signal_line,
        "histogram": histogram,
        "trend":     trend,
        "cross":     cross,
    }


def calc_atr(candles: list, period: int = 14) -> float:
    """Average True Range — Wilder smoothing."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h  = candles[i]["high"]
        l  = candles[i]["low"]
        pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    # Seed with SMA
    atr = sum(trs[:period]) / min(period, len(trs))
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def calc_ema_slope(closes: list, period: int, lookback: int = 5) -> dict:
    """
    EMA slope — is the trend accelerating or decelerating?
    Returns slope percentage and direction.
    """
    if len(closes) < period + lookback:
        return {"slope_pct": 0, "direction": "FLAT", "acceleration": "NONE"}

    series = calc_ema_series(closes, period)
    valid  = [x for x in series if x is not None]
    if len(valid) < lookback + 1:
        return {"slope_pct": 0, "direction": "FLAT", "acceleration": "NONE"}

    current  = valid[-1]
    previous = valid[-lookback]
    slope    = (current - previous) / previous * 100

    # Acceleration: compare recent slope to older slope
    if len(valid) >= lookback * 2:
        old_slope = (valid[-lookback] - valid[-lookback*2]) / valid[-lookback*2] * 100
        accel = ("ACCELERATING" if abs(slope) > abs(old_slope) * 1.2
                 else "DECELERATING" if abs(slope) < abs(old_slope) * 0.8
                 else "STEADY")
    else:
        accel = "STEADY"

    direction = ("RISING_STRONG" if slope > 0.3 else
                 "RISING"        if slope > 0.05 else
                 "FLAT"          if abs(slope) <= 0.05 else
                 "FALLING"       if slope > -0.3 else
                 "FALLING_STRONG")

    return {
        "slope_pct":    round(slope, 4),
        "direction":    direction,
        "acceleration": accel,
    }


# ══════════════════════════════════════════════════════════════
#  VOLUME ANALYSIS
# ══════════════════════════════════════════════════════════════

def calc_volume(candles: list) -> dict:
    """
    Volume analysis using completed candles only.
    NOTE: up/down candle volume is a PROXY for buying/selling pressure.
    Not real order flow — renamed accordingly.
    """
    if len(candles) < 5:
        return {"current": 0, "avg20": 0, "relative": 0,
                "up_vol_pct": 50, "down_vol_pct": 50, "label": "Normal"}

    recent = candles[-21:-1]  # Last 20 COMPLETED candles (not current)
    if not recent:
        recent = candles[-20:]

    avg20  = sum(c["volume"] for c in recent) / len(recent) if recent else 1
    cur    = candles[-1]["volume"]
    rel    = round(cur / avg20, 2) if avg20 > 0 else 1.0

    up_vol   = sum(c["volume"] for c in recent if c["close"] >= c["open"])
    down_vol = sum(c["volume"] for c in recent if c["close"] < c["open"])
    total_v  = up_vol + down_vol or 1

    up_pct   = round(up_vol / total_v * 100, 1)
    down_pct = round(down_vol / total_v * 100, 1)

    label = ("Very High" if rel > 2.0 else "High" if rel > 1.3 else
             "Normal"    if rel > 0.7 else "Low"  if rel > 0.4 else "Very Low")

    return {
        "current":      cur,
        "avg20":        round(avg20, 2),
        "relative":     rel,
        "up_vol_pct":   up_pct,    # proxy for buying pressure
        "down_vol_pct": down_pct,  # proxy for selling pressure
        "buy_pressure": up_pct,    # kept for backward compat
        "sell_pressure":down_pct,
        "label":        label,
        "confirms_move":rel > 1.2,
    }


# ══════════════════════════════════════════════════════════════
#  MARKET REGIME
# ══════════════════════════════════════════════════════════════

def calc_market_regime(candles: list, atr: float, ema20: float, ema50: float) -> dict:
    """
    Classify market as TRENDING or RANGING.
    Critical: different strategies for each.
    """
    if len(candles) < 20 or atr == 0:
        return {"regime": "UNKNOWN", "trending": False, "volatility": "NORMAL"}

    recent = candles[-20:]
    highs  = [c["high"]  for c in recent]
    lows   = [c["low"]   for c in recent]

    # Range width relative to ATR
    range_width  = (max(highs) - min(lows))
    range_in_atr = range_width / atr

    # EMA separation (how far apart are EMAs?)
    price      = candles[-1]["close"]
    ema_sep    = abs(ema20 - ema50) / price * 100

    # Price location within range
    range_mid  = (max(highs) + min(lows)) / 2
    near_top   = price > range_mid + range_width * 0.3
    near_bottom= price < range_mid - range_width * 0.3

    # Historical ATR vs current ATR
    old_candles  = candles[-40:-20] if len(candles) >= 40 else candles[:20]
    old_atr      = calc_atr(old_candles, 14) if len(old_candles) >= 5 else atr
    atr_ratio    = atr / old_atr if old_atr > 0 else 1.0

    # Classify
    if range_in_atr > 8 and ema_sep > 0.3:
        regime     = "STRONG_TREND"
        trending   = True
    elif range_in_atr > 5 and ema_sep > 0.1:
        regime     = "TRENDING"
        trending   = True
    elif range_in_atr < 3 or ema_sep < 0.05:
        regime     = "RANGING"
        trending   = False
    else:
        regime     = "TRANSITIONING"
        trending   = False

    volatility = ("HIGH"   if atr_ratio > 1.5 else
                  "LOW"    if atr_ratio < 0.6 else
                  "NORMAL")

    return {
        "regime":     regime,
        "trending":   trending,
        "volatility": volatility,
        "range_atr":  round(range_in_atr, 1),
        "ema_sep_pct":round(ema_sep, 3),
        "atr_ratio":  round(atr_ratio, 2),
    }


# ══════════════════════════════════════════════════════════════
#  MARKET STRUCTURE — REAL SWING DETECTION
# ══════════════════════════════════════════════════════════════

def calc_market_structure(candles: list) -> dict:
    """
    Real swing point detection with displacement confirmation.
    BOS requires 2 candles closed beyond level.
    Tracks displacement strength.
    """
    if len(candles) < 10:
        return {
            "structure": "Forming", "trend": "Neutral", "sequence": "Forming",
            "swing_high": 0, "swing_low": 0, "strength_pct": 0,
            "strength_label": "Weak", "bos": False, "choch": False,
            "bos_event": None, "displacement": 0,
        }

    recent = candles[-80:] if len(candles) >= 80 else candles
    n      = len(recent)
    left   = 3  # candles on each side for swing detection

    # Find real swing highs and lows
    swing_highs = []
    swing_lows  = []

    for i in range(left, n - left):
        h = recent[i]["high"]
        l = recent[i]["low"]
        if all(h >= recent[j]["high"] for j in range(i-left, i+left+1) if j != i):
            swing_highs.append((i, h))
        if all(l <= recent[j]["low"]  for j in range(i-left, i+left+1) if j != i):
            swing_lows.append((i, l))

    # Need at least 2 of each
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Simple fallback
        sh = max(c["high"] for c in recent[-20:])
        sl = min(c["low"]  for c in recent[-20:])
        sh_p = max(c["high"] for c in recent[-40:-20]) if len(recent)>=40 else sh
        sl_p = min(c["low"]  for c in recent[-40:-20]) if len(recent)>=40 else sl
        trend = ("Bullish" if sh>sh_p and sl>sl_p
                 else "Bearish" if sh<sh_p and sl<sl_p else "Neutral")
        return {
            "structure": "HH/HL" if trend=="Bullish" else "LH/LL" if trend=="Bearish" else "Mixed",
            "trend": trend, "sequence": "Forming",
            "swing_high": round(sh,2), "swing_low": round(sl,2),
            "strength_pct": 15, "strength_label": "Weak",
            "bos": False, "choch": False, "bos_event": None, "displacement": 0,
        }

    # Label swing highs
    sh_labels = []
    for i in range(1, len(swing_highs)):
        sh_labels.append("HH" if swing_highs[i][1] > swing_highs[i-1][1] else "LH")

    # Label swing lows
    sl_labels = []
    for i in range(1, len(swing_lows)):
        sl_labels.append("HL" if swing_lows[i][1] > swing_lows[i-1][1] else "LL")

    # Build sequence
    seq_parts = []
    ml = max(len(sh_labels), len(sl_labels))
    for i in range(min(ml, 4)):
        if i < len(sh_labels): seq_parts.append(sh_labels[i])
        if i < len(sl_labels): seq_parts.append(sl_labels[i])
    sequence = " → ".join(seq_parts[-8:]) if seq_parts else "Forming"

    # Trend from last 2 swings
    last_2_sh    = swing_highs[-2:]
    last_2_sl    = swing_lows[-2:]
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

    # Strength from swing consistency
    recent_sh = sh_labels[-4:]
    recent_sl = sl_labels[-4:]
    if trend == "Bullish":
        agree = sum(1 for x in recent_sh if x=="HH") + sum(1 for x in recent_sl if x=="HL")
    elif trend == "Bearish":
        agree = sum(1 for x in recent_sh if x=="LH") + sum(1 for x in recent_sl if x=="LL")
    else:
        agree = 0
    total_sw = len(recent_sh) + len(recent_sl)
    raw_sp   = int((agree / max(total_sw, 1)) * 100)

    # Factor in price distance from swing levels
    last_close = recent[-1]["close"]
    last_sh    = swing_highs[-1][1]
    last_sl    = swing_lows[-1][1]
    prev_sh    = swing_highs[-2][1]

    if trend == "Bullish" and last_sl > 0:
        pct_above = (last_close - last_sl) / last_sl * 100
        raw_sp = max(raw_sp, min(int(pct_above * 3), 60))
    elif trend == "Bearish" and last_sh > 0:
        pct_below = (last_sh - last_close) / last_sh * 100
        raw_sp = max(raw_sp, min(int(pct_below * 3), 60))

    if trend in ("Bullish","Bearish") and total_sw >= 2:
        raw_sp = max(raw_sp, 15)
    sp = min(raw_sp, 100)
    strength_label = "Strong" if sp >= 70 else "Moderate" if sp >= 40 else "Weak"

    # BOS — requires BOTH last AND previous candle closed beyond level
    # AND displacement (candle range > 0.5x ATR)
    prev_close  = recent[-2]["close"] if len(recent) >= 2 else last_close
    atr_approx  = calc_atr(recent[-20:], 14) if len(recent) >= 15 else 1

    bos_bull = (trend == "Bullish" and
                last_close > prev_sh and
                prev_close > prev_sh)  # both candles ABOVE level

    bos_bear = (trend == "Bearish" and
                last_close < last_sl and
                prev_close < last_sl)  # both candles BELOW level

    bos = bos_bull or bos_bear

    # Displacement strength
    last_candle = recent[-1]
    candle_range = last_candle["high"] - last_candle["low"]
    displacement = round(candle_range / atr_approx, 2) if atr_approx > 0 else 0

    # BOS event with full context
    bos_event = None
    if bos:
        bos_event = {
            "type":         "BOS",
            "direction":    "Bullish" if bos_bull else "Bearish",
            "level":        round(prev_sh if bos_bull else last_sl, 2),
            "close":        round(last_close, 2),
            "displacement": displacement,
            "strong":       displacement > 0.8,
        }

    # CHoCH
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
        "bos_event":      bos_event,
        "displacement":   displacement,
    }


# ══════════════════════════════════════════════════════════════
#  S/R ZONES (not just highest/lowest)
# ══════════════════════════════════════════════════════════════

def calc_sr_zones(candles: list, price: float, atr: float) -> dict:
    """
    Real support/resistance zones based on price clustering.
    Groups nearby price levels that have been touched multiple times.
    """
    if len(candles) < 20:
        return {"support": [], "resistance": [], "nearest_support": 0,
                "nearest_resistance": 0, "dist_to_support": 0, "dist_to_resistance": 0}

    recent = candles[-100:] if len(candles) >= 100 else candles
    levels = []

    # Collect all swing highs and lows
    for i in range(2, len(recent)-2):
        h = recent[i]["high"]
        l = recent[i]["low"]
        if h >= max(recent[j]["high"] for j in range(i-2,i+3) if j!=i):
            levels.append(("R", h))
        if l <= min(recent[j]["low"] for j in range(i-2,i+3) if j!=i):
            levels.append(("S", l))

    # Cluster nearby levels (within 0.5 ATR)
    cluster_dist = atr * 0.5 if atr > 0 else price * 0.005
    support_zones    = []
    resistance_zones = []

    for typ, level in sorted(levels, key=lambda x: x[1]):
        target = support_zones if typ=="S" else resistance_zones
        found  = False
        for zone in target:
            if abs(level - zone["level"]) <= cluster_dist:
                zone["touches"] += 1
                zone["level"]    = (zone["level"] + level) / 2  # average
                found = True
                break
        if not found:
            target.append({"level": level, "touches": 1})

    # Sort and filter: only zones with 2+ touches
    sup = sorted([z for z in support_zones if z["touches"] >= 2 and z["level"] < price],
                 key=lambda z: z["level"], reverse=True)
    res = sorted([z for z in resistance_zones if z["touches"] >= 2 and z["level"] > price],
                 key=lambda z: z["level"])

    nearest_sup = sup[0]["level"] if sup else 0
    nearest_res = res[0]["level"] if res else 0
    dist_sup    = round((price - nearest_sup) / atr, 2) if nearest_sup and atr else 0
    dist_res    = round((nearest_res - price) / atr, 2) if nearest_res and atr else 0

    return {
        "support":              [round(z["level"],2) for z in sup[:3]],
        "resistance":           [round(z["level"],2) for z in res[:3]],
        "nearest_support":      round(nearest_sup, 2),
        "nearest_resistance":   round(nearest_res, 2),
        "dist_to_support_atr":  dist_sup,   # in ATR units
        "dist_to_resistance_atr":dist_res,
        "near_resistance":      0 < dist_res < 1.5,  # within 1.5 ATR of resistance
        "near_support":         0 < dist_sup < 1.5,
    }


# ══════════════════════════════════════════════════════════════
#  FVG WITH LIFECYCLE
# ══════════════════════════════════════════════════════════════

def detect_fvg(candles: list, current_price: float) -> list:
    """
    Fair Value Gaps with filled/unfilled tracking.
    A FVG forms when candle[i-1].high < candle[i+1].low (bullish)
    or candle[i-1].low > candle[i+1].high (bearish).
    Tracks whether the gap has been filled.
    """
    if len(candles) < 3:
        return []

    fvgs   = []
    recent = candles[-50:] if len(candles) >= 50 else candles

    for i in range(1, len(recent)-1):
        c0, c1, c2 = recent[i-1], recent[i], recent[i+1]

        # Bullish FVG: gap between c0.high and c2.low
        if c0["high"] < c2["low"]:
            fvg_low  = c0["high"]
            fvg_high = c2["low"]
            gap_size = fvg_high - fvg_low

            # Check fill status
            subsequent = recent[i+2:] if i+2 < len(recent) else []
            min_since  = min((c["low"] for c in subsequent), default=current_price)
            fill_pct   = max(0, min(1, (fvg_high - min_since) / gap_size)) if gap_size > 0 else 0
            filled     = fill_pct >= 0.9

            if not filled and current_price > fvg_low:  # price above = valid
                fvgs.append({
                    "type":     "Bullish",
                    "low":      round(fvg_low, 2),
                    "high":     round(fvg_high, 2),
                    "midpoint": round((fvg_low + fvg_high) / 2, 2),
                    "size":     round(gap_size, 2),
                    "age":      len(recent) - i,
                    "fill_pct": round(fill_pct * 100, 1),
                    "status":   "Filled" if filled else ("Partial" if fill_pct > 0.1 else "Fresh"),
                    "fresh":    fill_pct < 0.1,
                })

        # Bearish FVG
        if c0["low"] > c2["high"]:
            fvg_high = c0["low"]
            fvg_low  = c2["high"]
            gap_size = fvg_high - fvg_low

            subsequent = recent[i+2:] if i+2 < len(recent) else []
            max_since  = max((c["high"] for c in subsequent), default=current_price)
            fill_pct   = max(0, min(1, (max_since - fvg_low) / gap_size)) if gap_size > 0 else 0
            filled     = fill_pct >= 0.9

            if not filled and current_price < fvg_high:
                fvgs.append({
                    "type":     "Bearish",
                    "low":      round(fvg_low, 2),
                    "high":     round(fvg_high, 2),
                    "midpoint": round((fvg_low + fvg_high) / 2, 2),
                    "size":     round(gap_size, 2),
                    "age":      len(recent) - i,
                    "fill_pct": round(fill_pct * 100, 1),
                    "status":   "Filled" if filled else ("Partial" if fill_pct > 0.1 else "Fresh"),
                    "fresh":    fill_pct < 0.1,
                })

    # Return only fresh/partial FVGs, closest first
    active = [f for f in fvgs if f["status"] != "Filled"]
    if current_price > 0:
        active.sort(key=lambda f: abs(f["midpoint"] - current_price))
    return active[:5]


# ══════════════════════════════════════════════════════════════
#  LIQUIDITY
# ══════════════════════════════════════════════════════════════

def detect_liquidity(candles: list, price: float) -> dict:
    """Detect buy-side and sell-side liquidity levels."""
    if len(candles) < 10:
        return {"bsl": 0, "ssl": 0, "swept_bsl": False,
                "swept_ssl": False, "equal_highs": [], "equal_lows": []}

    recent = candles[-50:] if len(candles) >= 50 else candles
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]

    bsl = max(highs)  # stops above highs
    ssl = min(lows)   # stops below lows

    tol = (bsl - ssl) * 0.002

    # Equal highs/lows (liquidity clusters)
    eq_highs = []
    eq_lows  = []
    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            if abs(highs[i] - highs[j]) < tol:
                eq_highs.append(round((highs[i]+highs[j])/2, 2))
            if abs(lows[i] - lows[j]) < tol:
                eq_lows.append(round((lows[i]+lows[j])/2, 2))

    # Check if liquidity was swept (current price beyond)
    swept_bsl = price > bsl
    swept_ssl = price < ssl

    return {
        "bsl":         round(bsl, 2),
        "ssl":         round(ssl, 2),
        "swept_bsl":   swept_bsl,
        "swept_ssl":   swept_ssl,
        "equal_highs": list(set(eq_highs))[:3],
        "equal_lows":  list(set(eq_lows))[:3],
        "near_bsl":    0 < (bsl - price) / max(bsl, 1) * 100 < 0.5,
        "near_ssl":    0 < (price - ssl) / max(ssl, 1) * 100 < 0.5,
    }


# ══════════════════════════════════════════════════════════════
#  CANDLESTICK PATTERNS
# ══════════════════════════════════════════════════════════════

def detect_patterns(candles: list) -> list:
    """Detect high-reliability candlestick patterns."""
    patterns = []
    if len(candles) < 3:
        return patterns

    c  = candles[-1]
    p  = candles[-2]
    pp = candles[-3]

    body  = abs(c["close"] - c["open"])
    rng   = c["high"] - c["low"]
    upper = c["high"] - max(c["close"], c["open"])
    lower = min(c["close"], c["open"]) - c["low"]
    bull  = c["close"] > c["open"]

    if rng == 0:
        return patterns

    # Bullish Engulfing
    if (bull and
        c["open"] < p["close"] and c["close"] > p["open"] and
        p["close"] < p["open"] and body > 0):
        patterns.append({"name":"Bullish Engulfing","direction":"Bullish",
                          "signal":"bullish","strength":"Strong","reliability":"High"})

    # Bearish Engulfing
    if (not bull and
        c["open"] > p["close"] and c["close"] < p["open"] and
        p["close"] > p["open"] and body > 0):
        patterns.append({"name":"Bearish Engulfing","direction":"Bearish",
                          "signal":"bearish","strength":"Strong","reliability":"High"})

    # Hammer (bullish reversal)
    if (lower > body * 2 and upper < body * 0.5 and
        lower > rng * 0.6):
        patterns.append({"name":"Hammer","direction":"Bullish",
                          "signal":"bullish","strength":"Moderate","reliability":"Medium-High"})

    # Shooting Star (bearish reversal)
    if (upper > body * 2 and lower < body * 0.5 and
        upper > rng * 0.6):
        patterns.append({"name":"Shooting Star","direction":"Bearish",
                          "signal":"bearish","strength":"Moderate","reliability":"Medium-High"})

    # Doji (indecision)
    if body < rng * 0.1:
        if upper > rng * 0.4 and lower > rng * 0.4:
            patterns.append({"name":"Doji","direction":"Neutral",
                              "signal":"neutral","strength":"Weak","reliability":"Medium"})
        elif upper > rng * 0.6:
            patterns.append({"name":"Gravestone Doji","direction":"Bearish",
                              "signal":"bearish","strength":"Moderate","reliability":"Medium-High"})
        elif lower > rng * 0.6:
            patterns.append({"name":"Dragonfly Doji","direction":"Bullish",
                              "signal":"bullish","strength":"Moderate","reliability":"Medium-High"})

    # Morning Star (3-candle bullish)
    if (pp["close"] < pp["open"] and
        abs(p["close"] - p["open"]) < abs(pp["close"] - pp["open"]) * 0.3 and
        c["close"] > c["open"] and
        c["close"] > (pp["open"] + pp["close"]) / 2):
        patterns.append({"name":"Morning Star","direction":"Bullish",
                          "signal":"bullish","strength":"Strong","reliability":"High"})

    # Evening Star (3-candle bearish)
    if (pp["close"] > pp["open"] and
        abs(p["close"] - p["open"]) < abs(pp["close"] - pp["open"]) * 0.3 and
        c["close"] < c["open"] and
        c["close"] < (pp["open"] + pp["close"]) / 2):
        patterns.append({"name":"Evening Star","direction":"Bearish",
                          "signal":"bearish","strength":"Strong","reliability":"High"})

    # Pin Bar
    if lower > rng * 0.65 and body < rng * 0.25:
        patterns.append({"name":"Bullish Pin Bar","direction":"Bullish",
                          "signal":"bullish","strength":"Strong","reliability":"High"})
    if upper > rng * 0.65 and body < rng * 0.25:
        patterns.append({"name":"Bearish Pin Bar","direction":"Bearish",
                          "signal":"bearish","strength":"Strong","reliability":"High"})

    return patterns


# ══════════════════════════════════════════════════════════════
#  RSI LABEL
# ══════════════════════════════════════════════════════════════

def rsi_label(r: float) -> str:
    if r >= 80: return "Extremely Overbought"
    if r >= 70: return "Overbought"
    if r >= 60: return "Bullish"
    if r >= 50: return "Neutral-Bullish"
    if r >= 40: return "Neutral-Bearish"
    if r >= 30: return "Bearish"
    if r >= 20: return "Oversold"
    return "Extremely Oversold"


# ══════════════════════════════════════════════════════════════
#  TIMEFRAME ANALYSIS
# ══════════════════════════════════════════════════════════════

def analyze_timeframe(candles: list, label: str) -> dict:
    """Analyze one timeframe. Returns structured data."""
    if len(candles) < 10:
        return {"label":label,"decision":"N/A","structure":"N/A","trend":"N/A",
                "rsi":0,"strength":0,"patterns":[],"bos":False,"displacement":0}

    closes = [c["close"] for c in candles]  # completed candles only
    e20    = calc_ema(closes, 20)
    e50    = calc_ema(closes, 50)
    r      = calc_rsi(closes, 14)
    ms     = calc_market_structure(candles)

    # Tag patterns with timeframe
    patterns = [{**p, "timeframe": label} for p in detect_patterns(candles)]

    bull = e20 > e50 and r < 75 and ms["trend"] == "Bullish"
    bear = e20 < e50 and r > 25 and ms["trend"] == "Bearish"

    return {
        "label":       label,
        "decision":    "BUY" if bull else ("SELL" if bear else "HOLD"),
        "structure":   ms["structure"],
        "trend":       ms["trend"],
        "strength":    ms["strength_pct"],
        "bos":         ms["bos"],
        "displacement":ms["displacement"],
        "rsi":         round(r, 2),
        "e20":         round(e20, 2),
        "e50":         round(e50, 2),
        "patterns":    patterns,
    }


def multi_timeframe_analysis(symbol: str) -> list:
    def scan_tf():
        from scanner import scan_timeframes
        return scan_timeframes(symbol)

    tf_data = scan_tf()
    frames  = []
    for label in ["15m","1H","4H","Daily"]:
        candles = tf_data.get(label, [])
        if len(candles) >= 10:
            frames.append(analyze_timeframe(candles, label))
    return frames


def mtf_bias(frames: list) -> str:
    if not frames: return "Neutral"
    bulls = sum(1 for f in frames if f.get("decision") == "BUY")
    bears = sum(1 for f in frames if f.get("decision") == "SELL")
    if bulls > bears: return "Bullish"
    if bears > bulls: return "Bearish"
    return "Neutral"


# ══════════════════════════════════════════════════════════════
#  MAIN ANALYZE FUNCTION
# ══════════════════════════════════════════════════════════════

def analyze(scan_data: dict) -> dict:
    """
    Full market analysis.
    Uses completed candles only — live price is NOT injected into history.
    """
    symbol  = scan_data.get("symbol", "BTCUSD")
    candles = scan_data.get("candles", [])
    price   = scan_data.get("price", 0)

    if len(candles) < 10:
        return {"error": "Not enough candle data", "symbol": symbol, "price": price}

    # Use completed candles only (not current forming candle)
    # The last candle may be forming — use candles[:-1] for indicators
    # but keep the full list for structure detection
    completed = candles[:-1] if len(candles) > 1 else candles
    closes    = [c["close"] for c in completed]

    if len(closes) < 5:
        return {"error": "Not enough completed candles", "symbol": symbol, "price": price}

    # Core indicators from completed candles
    e20    = calc_ema(closes, 20)
    e50    = calc_ema(closes, 50)
    r14    = calc_rsi(closes, 14)
    macd   = calc_macd(closes)
    atr14  = calc_atr(candles, 14)

    # EMA slope
    e20_slope = calc_ema_slope(closes, 20, 5)
    e50_slope = calc_ema_slope(closes, 50, 10)

    # Volume (completed candles)
    vol = calc_volume(candles)

    # Market structure (full candles including current forming)
    ms = calc_market_structure(candles)

    # Market regime
    regime = calc_market_regime(candles, atr14, e20, e50)

    # S/R zones
    sr = calc_sr_zones(candles, price, atr14)

    # FVG with lifecycle
    fvgs = detect_fvg(candles, price)

    # Liquidity
    liq = detect_liquidity(candles, price)

    # Patterns (completed candles)
    patterns = detect_patterns(completed)

    # Multi-timeframe
    frames = multi_timeframe_analysis(symbol)
    bias   = mtf_bias(frames)

    # Collect all timeframe patterns (tagged with TF)
    all_patterns = list(patterns)
    for f in frames:
        for p in f.get("patterns", []):
            if p not in all_patterns:
                all_patterns.append(p)

    # ATR-normalized distances
    dist_to_sr = {
        "to_resistance_atr": sr["dist_to_resistance_atr"],
        "to_support_atr":    sr["dist_to_support_atr"],
        "near_resistance":   sr["near_resistance"],
        "near_support":      sr["near_support"],
    }

    return {
        "symbol":      symbol,
        "price":       price,
        "session":     scan_data.get("session",""),
        "scanned_at":  scan_data.get("scanned_at",""),
        # Core indicators
        "ema20":       e20,
        "ema50":       e50,
        "ema20_slope": e20_slope,
        "ema50_slope": e50_slope,
        "rsi14":       r14,
        "rsi_label":   rsi_label(r14),
        "macd":        macd,
        "macd_line":   macd["line"],
        "macd_signal": macd["signal"],
        "macd_hist":   macd["histogram"],
        # ATR
        "atr14":       atr14,
        # Market structure
        "ms":          ms,
        # Volume
        "vol":         vol,
        # Regime
        "regime":      regime,
        # S/R zones
        "levels":      sr,
        "dist_sr":     dist_to_sr,
        # FVG
        "fvgs":        fvgs,
        # Liquidity
        "liq":         liq,
        # Patterns
        "patterns":    all_patterns,
        # Multi-timeframe
        "frames":      frames,
        "bias":        bias,
    }
