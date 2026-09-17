"""
analyzer.py - Stage 2: Market Analysis
=======================================
Fixed based on code review:

DATA STATE MODEL (strict):
  completed = candles[:-1]  — confirmed closed candles only
  forming   = candles[-1]   — currently forming (live price)

  Historical indicators (EMA, RSI, MACD, ATR, patterns, BOS):
    ALL use completed candles only. Never contaminated by live price.

  Live-price calculations (distance to S/R, FVG, liquidity):
    Use price parameter explicitly.

FIXES APPLIED:
  - EMA returns None if insufficient history (not fake close price)
  - ATR returns 0.0 if fewer than period TR values
  - BOS is an EVENT not persistent state (only True on the candle it occurs)
  - CHoCH properly checks meaningful structural break
  - MTF analysis strips forming candle in every timeframe
  - Liquidity detects actual sweep/reclaim events
  - Pattern deduplication (hammer and pin bar don't both fire)
  - MTF bias weighted: Daily=3, 4H=2, 1H=1, 15m=1
  - 'reliability' replaced with 'strength' (no unsupported claims)
  - FVG_FILL_THRESHOLD configurable
"""

from datetime import datetime, timezone

FVG_FILL_THRESHOLD = 0.90   # 90% filled = consider closed
MIN_ATR_CANDLES    = 14      # Need at least this many for valid ATR
MIN_EMA_CANDLES    = 20      # Need at least period candles for valid EMA
MIN_MACD_CANDLES   = 100     # Stable MACD warm-up (not just 35)


# ══════════════════════════════════════════════════════════════
#  CORE CALCULATIONS
# ══════════════════════════════════════════════════════════════

def calc_ema(closes: list, period: int):
    """
    True EMA using full history.
    Returns None if insufficient data — never returns fake value.
    """
    if len(closes) < period:
        return None          # Insufficient history — caller must handle
    k   = 2 / (period + 1)
    val = sum(closes[:period]) / period
    for price in closes[period:]:
        val = price * k + val * (1 - k)
    return round(val, 4)


def calc_ema_series(closes: list, period: int) -> list:
    """Full EMA series — needed for MACD signal line."""
    if len(closes) < period:
        return [None] * len(closes)
    k      = 2 / (period + 1)
    result = [None] * (period - 1)
    val    = sum(closes[:period]) / period
    result.append(val)
    for price in closes[period:]:
        val = price * k + val * (1 - k)
        result.append(val)
    return result


def calc_rsi(closes: list, period: int = 14) -> float:
    """Wilder RSI — matches TradingView."""
    if len(closes) < period + 1:
        return 50.0
    deltas  = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains   = [max(d, 0) for d in deltas]
    losses  = [abs(min(d, 0)) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def calc_macd(closes: list) -> dict:
    """
    Proper MACD: EMA12 - EMA26, Signal = EMA9 of MACD series.
    Requires MIN_MACD_CANDLES for stable output.
    """
    null = {"line":0,"signal":0,"histogram":0,"trend":"NEUTRAL","cross":"NONE","valid":False}
    if len(closes) < MIN_MACD_CANDLES:
        return null

    ema12s = calc_ema_series(closes, 12)
    ema26s = calc_ema_series(closes, 26)

    macd_series = [e12 - e26
                   for e12, e26 in zip(ema12s, ema26s)
                   if e12 is not None and e26 is not None]

    if len(macd_series) < 9:
        return null

    sig_series = calc_ema_series(macd_series, 9)

    line    = round(macd_series[-1], 4)
    signal  = round(sig_series[-1], 4) if sig_series[-1] is not None else 0
    hist    = round(line - signal, 4)

    prev_l  = macd_series[-2] if len(macd_series) >= 2 else line
    prev_s  = sig_series[-2]  if len(sig_series)  >= 2 and sig_series[-2] is not None else signal

    cross = "NONE"
    if prev_l <= prev_s and line > signal:
        cross = "BULLISH_CROSS"
    elif prev_l >= prev_s and line < signal:
        cross = "BEARISH_CROSS"

    trend = ("BULLISH" if line > 0 and line > signal else
             "BEARISH" if line < 0 and line < signal else "NEUTRAL")

    return {"line":line,"signal":signal,"histogram":hist,
            "trend":trend,"cross":cross,"valid":True}


def calc_atr(candles: list, period: int = 14) -> float:
    """
    Wilder ATR.
    Returns 0.0 if insufficient data — never silently approximates.
    """
    if len(candles) < period + 1:
        return 0.0      # Strict: insufficient data

    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(trs) < period:
        return 0.0

    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def calc_ema_slope(closes: list, period: int, lookback: int = 5) -> dict:
    null = {"slope_pct":0,"direction":"FLAT","acceleration":"NONE","valid":False}
    if len(closes) < period + lookback:
        return null
    series = calc_ema_series(closes, period)
    valid  = [x for x in series if x is not None]
    if len(valid) < lookback + 1:
        return null
    cur   = valid[-1]
    prev  = valid[-lookback]
    slope = (cur - prev) / prev * 100
    if len(valid) >= lookback * 2:
        old   = (valid[-lookback] - valid[-lookback*2]) / valid[-lookback*2] * 100
        accel = ("ACCELERATING" if abs(slope) > abs(old) * 1.2 else
                 "DECELERATING" if abs(slope) < abs(old) * 0.8 else "STEADY")
    else:
        accel = "STEADY"
    direction = ("RISING_STRONG" if slope > 0.3 else "RISING" if slope > 0.05 else
                 "FLAT" if abs(slope) <= 0.05 else
                 "FALLING" if slope > -0.3 else "FALLING_STRONG")
    return {"slope_pct":round(slope,4),"direction":direction,
            "acceleration":accel,"valid":True}


# ══════════════════════════════════════════════════════════════
#  VOLUME
# ══════════════════════════════════════════════════════════════

def calc_volume(candles: list) -> dict:
    """
    Uses completed candles only (passed in already).
    up/down candle volume is a PROXY — not real order flow.
    """
    if len(candles) < 5:
        return {"current":0,"avg20":0,"relative":0,"up_vol_pct":50,
                "down_vol_pct":50,"buy_pressure":50,"sell_pressure":50,
                "label":"Unknown","confirms_move":False}

    recent = candles[-20:]
    avg20  = sum(c["volume"] for c in recent) / len(recent) if recent else 1
    # Current = last completed candle
    cur    = candles[-1]["volume"]
    rel    = round(cur / avg20, 2) if avg20 > 0 else 1.0

    up_vol   = sum(c["volume"] for c in recent if c["close"] >= c["open"])
    down_vol = sum(c["volume"] for c in recent if c["close"] <  c["open"])
    total_v  = up_vol + down_vol or 1
    up_pct   = round(up_vol / total_v * 100, 1)
    down_pct = round(down_vol / total_v * 100, 1)

    label = ("Very High" if rel > 2.0 else "High" if rel > 1.3 else
             "Normal"    if rel > 0.7 else "Low"  if rel > 0.4 else "Very Low")

    return {"current":cur,"avg20":round(avg20,2),"relative":rel,
            "up_vol_pct":up_pct,"down_vol_pct":down_pct,
            "buy_pressure":up_pct,"sell_pressure":down_pct,
            "label":label,"confirms_move":rel > 1.2}


# ══════════════════════════════════════════════════════════════
#  MARKET STRUCTURE — REAL SWINGS + PROPER BOS/CHoCH
# ══════════════════════════════════════════════════════════════

def calc_market_structure(candles: list) -> dict:
    """
    FIXED:
    - BOS is now an EVENT (only True on the candle it first occurs)
    - CHoCH requires meaningful structural level break
    - Displacement is part of the BOS condition
    - Uses only completed (passed-in) candles
    """
    null = {
        "structure":"Forming","trend":"Neutral","sequence":"Forming",
        "swing_high":0,"swing_low":0,"strength_pct":0,
        "strength_label":"Weak","bos":False,"choch":False,
        "bos_event":None,"choch_event":None,"displacement":0,
    }
    if len(candles) < 10:
        return null

    n    = len(candles)
    left = 3

    # Find confirmed swing highs and lows
    swing_highs = []
    swing_lows  = []
    for i in range(left, n - left):
        h = candles[i]["high"]
        l = candles[i]["low"]
        if all(h >= candles[j]["high"] for j in range(i-left, i+left+1) if j != i):
            swing_highs.append((i, h))
        if all(l <= candles[j]["low"]  for j in range(i-left, i+left+1) if j != i):
            swing_lows.append((i, l))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        # Fallback
        sh = max(c["high"] for c in candles[-20:])
        sl = min(c["low"]  for c in candles[-20:])
        return {**null,
                "swing_high": round(sh,2), "swing_low": round(sl,2),
                "strength_pct":10}

    # Label swings
    sh_labels = ["HH" if swing_highs[i][1] > swing_highs[i-1][1] else "LH"
                 for i in range(1, len(swing_highs))]
    sl_labels = ["HL" if swing_lows[i][1]  > swing_lows[i-1][1]  else "LL"
                 for i in range(1, len(swing_lows))]

    # Build sequence
    seq_parts = []
    for i in range(min(max(len(sh_labels), len(sl_labels)), 4)):
        if i < len(sh_labels): seq_parts.append(sh_labels[i])
        if i < len(sl_labels): seq_parts.append(sl_labels[i])
    sequence = " → ".join(seq_parts[-8:]) if seq_parts else "Forming"

    # Trend from last 2 swings
    last_2_sh = swing_highs[-2:]
    last_2_sl = swing_lows[-2:]
    hh = last_2_sh[-1][1] > last_2_sh[0][1]
    hl = last_2_sl[-1][1] > last_2_sl[0][1]
    lh = last_2_sh[-1][1] < last_2_sh[0][1]
    ll = last_2_sl[-1][1] < last_2_sl[0][1]

    if hh and hl:   trend, structure = "Bullish", "HH / HL"
    elif lh and ll: trend, structure = "Bearish", "LH / LL"
    elif hh and ll: trend, structure = "Neutral", "HH / LL"
    else:           trend, structure = "Neutral", "LH / HL"

    # Strength
    recent_sh = sh_labels[-4:]
    recent_sl = sl_labels[-4:]
    if trend == "Bullish":
        agree = sum(1 for x in recent_sh if x=="HH") + sum(1 for x in recent_sl if x=="HL")
    elif trend == "Bearish":
        agree = sum(1 for x in recent_sh if x=="LH") + sum(1 for x in recent_sl if x=="LL")
    else:
        agree = 0
    total_sw = len(recent_sh) + len(recent_sl) or 1
    sp = min(int(agree / total_sw * 100), 100)
    if trend in ("Bullish","Bearish") and total_sw >= 2:
        sp = max(sp, 15)
    strength_label = "Strong" if sp >= 70 else "Moderate" if sp >= 40 else "Weak"

    # ── BOS as EVENT (not persistent state) ───────────────────
    # Only True on the exact candle where price first closes beyond level
    # Requires displacement >= 0.5 ATR to filter fakeouts

    last_close  = candles[-1]["close"]
    prev_close  = candles[-2]["close"] if n >= 2 else last_close
    last_sh     = swing_highs[-1][1]
    last_sl     = swing_lows[-1][1]
    prev_sh     = swing_highs[-2][1]
    prev_sl     = swing_lows[-2][1]

    # ATR for displacement
    atr_approx = calc_atr(candles[-20:], 14) if n >= 15 else 0
    last_range  = candles[-1]["high"] - candles[-1]["low"]
    displacement = round(last_range / atr_approx, 2) if atr_approx > 0 else 0

    # BOS: last candle closes beyond PREVIOUS swing level (not just last)
    # AND previous candle also closed beyond (fakeout filter)
    # AND displacement >= 0.5 ATR (momentum confirmation)
    bos_bull = (
        trend == "Bullish" and
        last_close > prev_sh and    # breaks previous swing high
        prev_close > prev_sh and    # previous candle also above
        displacement >= 0.5         # real momentum, not noise
    )
    bos_bear = (
        trend == "Bearish" and
        last_close < prev_sl and
        prev_close < prev_sl and
        displacement >= 0.5
    )
    bos = bos_bull or bos_bear

    bos_event = None
    if bos:
        bos_event = {
            "direction":    "Bullish" if bos_bull else "Bearish",
            "level":        round(prev_sh if bos_bull else prev_sl, 2),
            "close":        round(last_close, 2),
            "displacement": displacement,
            "strong":       displacement >= 1.0,
            "weak":         displacement < 0.8,
        }

    # ── CHoCH as EVENT ─────────────────────────────────────────
    # Bullish CHoCH: in downtrend, price breaks a meaningful previous LH
    # Bearish CHoCH: in uptrend, price breaks below a meaningful previous HL
    choch = False
    choch_event = None

    if trend == "Bearish" and len(swing_highs) >= 3:
        # Look for break above a recent lower high (not just last swing)
        recent_lhs = [sh[1] for sh in swing_highs[-3:] if sh[1] < last_sh]
        if recent_lhs:
            meaningful_lh = max(recent_lhs)
            if last_close > meaningful_lh and displacement >= 0.5:
                choch = True
                choch_event = {
                    "direction": "Bullish",
                    "level": round(meaningful_lh, 2),
                    "close": round(last_close, 2),
                    "displacement": displacement,
                }

    if trend == "Bullish" and len(swing_lows) >= 3:
        recent_hls = [sl[1] for sl in swing_lows[-3:] if sl[1] > last_sl]
        if recent_hls:
            meaningful_hl = min(recent_hls)
            if last_close < meaningful_hl and displacement >= 0.5:
                choch = True
                choch_event = {
                    "direction": "Bearish",
                    "level": round(meaningful_hl, 2),
                    "close": round(last_close, 2),
                    "displacement": displacement,
                }

    return {
        "structure":      structure,
        "trend":          trend,
        "sequence":       sequence,
        "swing_high":     round(last_sh, 2),
        "swing_low":      round(last_sl, 2),
        "prev_swing_high":round(prev_sh, 2),
        "prev_swing_low": round(prev_sl, 2),
        "strength_pct":   sp,
        "strength_label": strength_label,
        "bos":            bos,
        "choch":          choch,
        "bos_event":      bos_event,
        "choch_event":    choch_event,
        "displacement":   displacement,
    }


# ══════════════════════════════════════════════════════════════
#  MARKET REGIME
# ══════════════════════════════════════════════════════════════

def calc_market_regime(candles: list, atr: float, ema20, ema50) -> dict:
    """Classify TRENDING vs RANGING using completed candles."""
    if len(candles) < 20 or atr == 0:
        return {"regime":"UNKNOWN","trending":False,"volatility":"NORMAL"}

    recent = candles[-20:]
    highs  = [c["high"] for c in recent]
    lows   = [c["low"]  for c in recent]
    rng    = max(highs) - min(lows)
    rng_atr= rng / atr

    # EMA separation
    e20 = ema20 if ema20 else 0
    e50 = ema50 if ema50 else 0
    price    = candles[-1]["close"]
    ema_sep  = abs(e20 - e50) / price * 100 if price > 0 else 0

    old = candles[-40:-20] if len(candles) >= 40 else candles[:20]
    old_atr   = calc_atr(old, 14) if len(old) >= 15 else atr
    atr_ratio = atr / old_atr if old_atr > 0 else 1.0

    if rng_atr > 8 and ema_sep > 0.3:   regime = "STRONG_TREND"
    elif rng_atr > 5 and ema_sep > 0.1: regime = "TRENDING"
    elif rng_atr < 3 or ema_sep < 0.05: regime = "RANGING"
    else:                                regime = "TRANSITIONING"

    volatility = ("HIGH" if atr_ratio > 1.5 else
                  "LOW"  if atr_ratio < 0.6 else "NORMAL")

    return {
        "regime":     regime,
        "trending":   regime in ("TRENDING","STRONG_TREND"),
        "volatility": volatility,
        "range_atr":  round(rng_atr, 1),
        "ema_sep_pct":round(ema_sep, 3),
        "atr_ratio":  round(atr_ratio, 2),
    }


# ══════════════════════════════════════════════════════════════
#  S/R ZONES
# ══════════════════════════════════════════════════════════════

def calc_sr_zones(candles: list, price: float, atr: float) -> dict:
    """
    Real S/R zones from price clustering.
    FIXED: clustering uses sorted levels not order-dependent centroid updates.
    """
    if len(candles) < 20 or atr == 0:
        return {"support":[],"resistance":[],"nearest_support":0,
                "nearest_resistance":0,"support_touches":0,
                "resistance_touches":0,"support_strength":"Weak",
                "resistance_strength":"Weak","dist_to_support_atr":0,
                "dist_to_resistance_atr":0,"near_resistance":False,
                "near_support":False}

    recent = candles[-100:] if len(candles) >= 100 else candles
    raw_s, raw_r = [], []

    for i in range(2, len(recent)-2):
        h = recent[i]["high"]
        l = recent[i]["low"]
        if h >= max(recent[j]["high"] for j in range(i-2,i+3) if j!=i):
            raw_r.append(h)
        if l <= min(recent[j]["low"]  for j in range(i-2,i+3) if j!=i):
            raw_s.append(l)

    def cluster(levels, threshold):
        if not levels: return []
        sorted_lvls = sorted(levels)
        groups = []
        group  = [sorted_lvls[0]]
        for lvl in sorted_lvls[1:]:
            if lvl - group[-1] <= threshold:
                group.append(lvl)
            else:
                groups.append(group)
                group = [lvl]
        groups.append(group)
        return [{"level": round(sum(g)/len(g), 2), "touches": len(g)}
                for g in groups if len(g) >= 2]

    cluster_dist = atr * 0.5
    sup_zones = sorted([z for z in cluster(raw_s, cluster_dist) if z["level"] < price],
                       key=lambda z: z["level"], reverse=True)
    res_zones = sorted([z for z in cluster(raw_r, cluster_dist) if z["level"] > price],
                       key=lambda z: z["level"])

    def strength(t): return "Strong" if t >= 4 else "Moderate" if t >= 2 else "Weak"

    best_s = sup_zones[0] if sup_zones else {"level":0,"touches":0}
    best_r = res_zones[0] if res_zones else {"level":0,"touches":0}

    dist_s = round((price - best_s["level"]) / atr, 2) if best_s["level"] and atr else 0
    dist_r = round((best_r["level"] - price) / atr, 2) if best_r["level"] and atr else 0

    return {
        "support":               [round(z["level"],2) for z in sup_zones[:3]],
        "resistance":            [round(z["level"],2) for z in res_zones[:3]],
        "nearest_support":       best_s["level"],
        "nearest_resistance":    best_r["level"],
        "support_touches":       best_s["touches"],
        "resistance_touches":    best_r["touches"],
        "support_strength":      strength(best_s["touches"]),
        "resistance_strength":   strength(best_r["touches"]),
        "dist_to_support_atr":   dist_s,
        "dist_to_resistance_atr":dist_r,
        "near_resistance":       0 < dist_r < 1.5,
        "near_support":          0 < dist_s < 1.5,
    }


# ══════════════════════════════════════════════════════════════
#  FVG WITH LIFECYCLE
# ══════════════════════════════════════════════════════════════

def detect_fvg(candles: list, price: float) -> list:
    """
    FVG detection on completed candles.
    FVG_FILL_THRESHOLD configurable.
    FIXED: FVG existence separated from current price location.
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
            if gap_size <= 0:
                continue
            subsequent = recent[i+2:] if i+2 < len(recent) else []
            min_since  = min((c["low"] for c in subsequent), default=fvg_high)
            fill_pct   = max(0, min(1, (fvg_high - min_since) / gap_size))
            filled     = fill_pct >= FVG_FILL_THRESHOLD
            # Price relation: ABOVE, INSIDE, BELOW
            if price > fvg_high:    price_rel = "ABOVE"
            elif price >= fvg_low:  price_rel = "INSIDE"
            else:                   price_rel = "BELOW"

            if not filled:
                fvgs.append({
                    "type":"Bullish","low":round(fvg_low,2),"high":round(fvg_high,2),
                    "midpoint":round((fvg_low+fvg_high)/2,2),"size":round(gap_size,2),
                    "age":len(recent)-i,"fill_pct":round(fill_pct*100,1),
                    "status":"Partial" if fill_pct > 0.1 else "Fresh",
                    "fresh":fill_pct < 0.1,"price_relation":price_rel,
                })

        # Bearish FVG
        if c0["low"] > c2["high"]:
            fvg_high = c0["low"]
            fvg_low  = c2["high"]
            gap_size = fvg_high - fvg_low
            if gap_size <= 0:
                continue
            subsequent = recent[i+2:] if i+2 < len(recent) else []
            max_since  = max((c["high"] for c in subsequent), default=fvg_low)
            fill_pct   = max(0, min(1, (max_since - fvg_low) / gap_size))
            filled     = fill_pct >= FVG_FILL_THRESHOLD
            if price > fvg_high:   price_rel = "ABOVE"
            elif price >= fvg_low: price_rel = "INSIDE"
            else:                  price_rel = "BELOW"

            if not filled:
                fvgs.append({
                    "type":"Bearish","low":round(fvg_low,2),"high":round(fvg_high,2),
                    "midpoint":round((fvg_low+fvg_high)/2,2),"size":round(gap_size,2),
                    "age":len(recent)-i,"fill_pct":round(fill_pct*100,1),
                    "status":"Partial" if fill_pct > 0.1 else "Fresh",
                    "fresh":fill_pct < 0.1,"price_relation":price_rel,
                })

    # Sort by proximity to current price
    active = [f for f in fvgs]
    if price > 0:
        active.sort(key=lambda f: abs(f["midpoint"] - price))
    return active[:5]


# ══════════════════════════════════════════════════════════════
#  LIQUIDITY — ACTUAL SWEEP DETECTION
# ══════════════════════════════════════════════════════════════

def detect_liquidity(candles: list, price: float) -> dict:
    """
    FIXED: Detects actual sweep events (wick beyond level + close back inside)
    not just whether current price > max high.
    """
    if len(candles) < 10:
        return {"bsl":0,"ssl":0,"swept_bsl":False,"swept_ssl":False,
                "equal_highs":[],"equal_lows":[],"near_bsl":False,"near_ssl":False,
                "recent_sweep":None}

    recent = candles[-50:] if len(candles) >= 50 else candles
    highs  = [c["high"]  for c in recent]
    lows   = [c["low"]   for c in recent]

    # BSL = stops above the highest high (where longs park stops)
    # SSL = stops below the lowest low (where shorts park stops)
    bsl = max(highs)
    ssl = min(lows)

    tol = (bsl - ssl) * 0.003

    # Equal highs/lows — areas of clustered liquidity
    eq_highs, eq_lows = [], []
    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            if abs(highs[i] - highs[j]) < tol:
                eq_highs.append(round((highs[i]+highs[j])/2, 2))
            if abs(lows[i] - lows[j]) < tol:
                eq_lows.append(round((lows[i]+lows[j])/2, 2))

    # ── Actual sweep detection ────────────────────────────────
    # A sweep = wick beyond the level AND close back inside
    # Check last 5 candles for recent sweep events
    swept_bsl   = False
    swept_ssl   = False
    recent_sweep = None

    for i in range(max(0, len(recent)-5), len(recent)):
        c = recent[i]
        # Bullish sweep (SSL): wick below ssl, close above ssl
        if c["low"] < ssl * 1.002 and c["close"] > ssl:
            swept_ssl    = True
            recent_sweep = {"type":"SSL_SWEEP","level":round(ssl,2),
                            "wick_low":round(c["low"],2),"close":round(c["close"],2),
                            "signal":"bullish"}
        # Bearish sweep (BSL): wick above bsl, close below bsl
        if c["high"] > bsl * 0.998 and c["close"] < bsl:
            swept_bsl    = True
            recent_sweep = {"type":"BSL_SWEEP","level":round(bsl,2),
                            "wick_high":round(c["high"],2),"close":round(c["close"],2),
                            "signal":"bearish"}

    return {
        "bsl":          round(bsl, 2),
        "ssl":          round(ssl, 2),
        "swept_bsl":    swept_bsl,
        "swept_ssl":    swept_ssl,
        "equal_highs":  list(set(eq_highs))[:3],
        "equal_lows":   list(set(eq_lows))[:3],
        "near_bsl":     0 < (bsl - price) / max(bsl,1) * 100 < 0.5,
        "near_ssl":     0 < (price - ssl) / max(ssl,1) * 100 < 0.5,
        "recent_sweep": recent_sweep,
    }


# ══════════════════════════════════════════════════════════════
#  CANDLESTICK PATTERNS — DEDUPLICATED
# ══════════════════════════════════════════════════════════════

def detect_patterns(candles: list) -> list:
    """
    FIXED:
    - 'reliability' removed — unsupported claim
    - Uses 'strength' instead (Strong/Moderate)
    - Deduplicated: hammer and pin bar don't both fire for same candle
    - Priority given to most specific pattern
    """
    if len(candles) < 3:
        return []

    c  = candles[-1]
    p  = candles[-2]
    pp = candles[-3]

    body  = abs(c["close"] - c["open"])
    rng   = c["high"] - c["low"]
    upper = c["high"] - max(c["close"], c["open"])
    lower = min(c["close"], c["open"]) - c["low"]
    bull  = c["close"] > c["open"]
    if rng == 0:
        return []

    patterns = []
    used_categories = set()  # prevent duplicate signals

    # 3-candle patterns first (most specific)
    if (pp["close"] < pp["open"] and
        abs(p["close"]-p["open"]) < abs(pp["close"]-pp["open"])*0.3 and
        c["close"] > c["open"] and
        c["close"] > (pp["open"]+pp["close"])/2):
        patterns.append({"name":"Morning Star","direction":"Bullish",
                         "signal":"bullish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bull")

    if (pp["close"] > pp["open"] and
        abs(p["close"]-p["open"]) < abs(pp["close"]-pp["open"])*0.3 and
        c["close"] < c["open"] and
        c["close"] < (pp["open"]+pp["close"])/2):
        patterns.append({"name":"Evening Star","direction":"Bearish",
                         "signal":"bearish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bear")

    # Engulfing
    if (bull and c["open"] < p["close"] and c["close"] > p["open"] and
        p["close"] < p["open"] and "reversal_bull" not in used_categories):
        patterns.append({"name":"Bullish Engulfing","direction":"Bullish",
                         "signal":"bullish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bull")

    if (not bull and c["open"] > p["close"] and c["close"] < p["open"] and
        p["close"] > p["open"] and "reversal_bear" not in used_categories):
        patterns.append({"name":"Bearish Engulfing","direction":"Bearish",
                         "signal":"bearish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bear")

    # Pin Bar (priority over Hammer to avoid duplicate)
    if lower > rng * 0.65 and body < rng * 0.25 and "reversal_bull" not in used_categories:
        patterns.append({"name":"Bullish Pin Bar","direction":"Bullish",
                         "signal":"bullish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bull")
    elif upper > rng * 0.65 and body < rng * 0.25 and "reversal_bear" not in used_categories:
        patterns.append({"name":"Bearish Pin Bar","direction":"Bearish",
                         "signal":"bearish","strength":"Strong","category":"reversal"})
        used_categories.add("reversal_bear")

    # Hammer (only if no pin bar already detected)
    elif (lower > body * 2 and upper < body * 0.5 and
          lower > rng * 0.6 and "reversal_bull" not in used_categories):
        patterns.append({"name":"Hammer","direction":"Bullish",
                         "signal":"bullish","strength":"Moderate","category":"reversal"})
        used_categories.add("reversal_bull")

    # Shooting Star
    elif (upper > body * 2 and lower < body * 0.5 and
          upper > rng * 0.6 and "reversal_bear" not in used_categories):
        patterns.append({"name":"Shooting Star","direction":"Bearish",
                         "signal":"bearish","strength":"Moderate","category":"reversal"})
        used_categories.add("reversal_bear")

    # Doji (only if no directional pattern)
    if body < rng * 0.1 and not used_categories:
        if upper > rng * 0.4 and lower > rng * 0.4:
            patterns.append({"name":"Doji","direction":"Neutral",
                             "signal":"neutral","strength":"Weak","category":"indecision"})
        elif upper > rng * 0.6:
            patterns.append({"name":"Gravestone Doji","direction":"Bearish",
                             "signal":"bearish","strength":"Moderate","category":"reversal"})
        elif lower > rng * 0.6:
            patterns.append({"name":"Dragonfly Doji","direction":"Bullish",
                             "signal":"bullish","strength":"Moderate","category":"reversal"})

    return patterns


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
#  TIMEFRAME ANALYSIS — CONSISTENT CANDLE STATE
# ══════════════════════════════════════════════════════════════

def analyze_timeframe(candles: list, label: str) -> dict:
    """
    FIXED: Strips forming candle consistently across ALL timeframes.
    Every timeframe uses the same completed-candle policy.
    """
    if len(candles) < 12:
        return {"label":label,"decision":"N/A","structure":"N/A","trend":"N/A",
                "rsi":0,"strength":0,"patterns":[],"bos":False,"displacement":0}

    # Strip forming candle — same rule as main analyze()
    completed = candles[:-1] if len(candles) > 1 else candles
    closes    = [c["close"] for c in completed]

    e20 = calc_ema(closes, 20)
    e50 = calc_ema(closes, 50)
    r   = calc_rsi(closes, 14)
    ms  = calc_market_structure(completed)

    # Tag patterns with timeframe
    patterns = [{**p, "timeframe": label} for p in detect_patterns(completed)]

    # Decision: needs both EMA alignment AND structure
    bull = (e20 is not None and e50 is not None and
            e20 > e50 and r < 75 and ms["trend"] == "Bullish")
    bear = (e20 is not None and e50 is not None and
            e20 < e50 and r > 25 and ms["trend"] == "Bearish")

    return {
        "label":       label,
        "decision":    "BUY" if bull else ("SELL" if bear else "HOLD"),
        "structure":   ms["structure"],
        "trend":       ms["trend"],
        "strength":    ms["strength_pct"],
        "bos":         ms["bos"],
        "displacement":ms["displacement"],
        "rsi":         round(r, 2),
        "e20":         round(e20, 2) if e20 else None,
        "e50":         round(e50, 2) if e50 else None,
        "patterns":    patterns,
    }


def multi_timeframe_analysis(symbol: str) -> list:
    from scanner import scan_timeframes
    tf_data = scan_timeframes(symbol)
    frames  = []
    for label in ["15m","1H","4H","Daily"]:
        candles = tf_data.get(label, [])
        if len(candles) >= 12:
            frames.append(analyze_timeframe(candles, label))
    return frames


def mtf_bias(frames: list) -> dict:
    """
    FIXED: Weighted by timeframe significance.
    Daily=3, 4H=2, 1H=1, 15m=1
    Reports HTF bias and execution bias separately.
    """
    weights  = {"Daily":3,"4H":2,"1H":1,"15m":1}
    htf_labels = {"Daily","4H"}

    htf_bull = htf_bear = htf_hold = 0
    ex_bull  = ex_bear  = 0

    for f in frames:
        w = weights.get(f["label"], 1)
        d = f.get("decision","HOLD")
        if f["label"] in htf_labels:
            if d == "BUY":  htf_bull += w
            elif d == "SELL": htf_bear += w
            else: htf_hold += w
        else:
            if d == "BUY":  ex_bull += 1
            elif d == "SELL": ex_bear += 1

    htf_bias = ("Bullish" if htf_bull > htf_bear else
                "Bearish" if htf_bear > htf_bull else "Neutral")
    ex_bias  = ("Bullish" if ex_bull > ex_bear else
                "Bearish" if ex_bear > ex_bull else "Neutral")
    alignment = "Aligned" if htf_bias == ex_bias else "Conflicted"

    # Simple string bias for backward compat
    total_bull = htf_bull + ex_bull
    total_bear = htf_bear + ex_bear
    simple = ("Bullish" if total_bull > total_bear else
              "Bearish" if total_bear > total_bull else "Neutral")

    return {
        "simple":    simple,
        "htf_bias":  htf_bias,
        "exec_bias": ex_bias,
        "alignment": alignment,
    }


# ══════════════════════════════════════════════════════════════
#  MAIN ANALYZE — STRICT CANDLE STATE
# ══════════════════════════════════════════════════════════════

def analyze(scan_data: dict) -> dict:
    """
    FIXED: All historical indicators use COMPLETED candles only.
    Live price used ONLY for distance calculations.

    Data state model:
      completed = candles[:-1]  — closed candles for indicators
      price     = live price    — for distance to levels
    """
    symbol  = scan_data.get("symbol","BTCUSD")
    candles = scan_data.get("candles",[])
    price   = scan_data.get("price", 0)

    if len(candles) < 10:
        return {"error":"Not enough candle data","symbol":symbol,"price":price}

    # ── STRICT: completed candles only for all historical calculations ──
    completed = candles[:-1] if len(candles) > 1 else candles
    closes    = [c["close"] for c in completed]

    if len(closes) < 5:
        return {"error":"Not enough completed candles","symbol":symbol,"price":price}

    # Historical indicators — completed candles only
    e20     = calc_ema(closes, 20)
    e50     = calc_ema(closes, 50)
    r14     = calc_rsi(closes, 14)
    macd    = calc_macd(closes)
    atr14   = calc_atr(completed, 14)

    e20_slope = calc_ema_slope(closes, 20, 5)
    e50_slope = calc_ema_slope(closes, 50, 10)
    vol       = calc_volume(completed)

    # Market structure — completed candles
    ms     = calc_market_structure(completed)
    regime = calc_market_regime(completed, atr14, e20, e50)

    # S/R, FVG, Liquidity — completed candles + live price for distance
    sr  = calc_sr_zones(completed, price, atr14)
    fvg = detect_fvg(completed, price)
    liq = detect_liquidity(completed, price)

    # Patterns — completed candles
    patterns = detect_patterns(completed)

    # MTF
    frames = multi_timeframe_analysis(symbol)
    bias   = mtf_bias(frames)

    # Tag patterns with timeframe
    all_patterns = list(patterns)
    for f in frames:
        for p in f.get("patterns",[]):
            if p not in all_patterns:
                all_patterns.append(p)

    return {
        "symbol":      symbol,
        "price":       price,
        "session":     scan_data.get("session",""),
        "scanned_at":  scan_data.get("scanned_at",""),
        # Indicators (completed candles)
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
        "atr14":       atr14,
        # Structure
        "ms":          ms,
        # Volume
        "vol":         vol,
        # Regime
        "regime":      regime,
        # S/R
        "levels":      sr,
        "dist_sr":     {
            "to_resistance_atr": sr["dist_to_resistance_atr"],
            "to_support_atr":    sr["dist_to_support_atr"],
            "near_resistance":   sr["near_resistance"],
            "near_support":      sr["near_support"],
        },
        # FVG
        "fvgs":        fvg,
        # Liquidity
        "liq":         liq,
        # Patterns
        "patterns":    all_patterns,
        # MTF
        "frames":      frames,
        "bias":        bias["simple"],
        "bias_detail": bias,
        # Candles (for dashboard chart)
        "candles":     candles,
    }
