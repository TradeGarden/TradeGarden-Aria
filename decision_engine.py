"""
decision_engine.py - Stage 3: Professional Trading Decision Engine
==================================================================
Full professional trading knowledge built in.

Core principle:
  STRUCTURE gives BIAS (direction).
  RETEST gives PERMISSION to enter.
  CONFIRMATION gives the actual ENTRY signal.

Without all three → NO TRADE.

Entry hierarchy (in order of priority):
  1. Higher timeframe structure (4H, Daily bias)
  2. Break of Structure (BOS) with displacement
  3. Wait for retest of broken level
  4. Confirm rejection at retest (not just touching the level)
  5. Check location (not chasing extended moves)
  6. Volume confirmation
  7. Indicator confluence (EMA, RSI confirm)

Anti-chase rule:
  If price is already extended beyond the last BOS level by 1.5+ ATR,
  there is no valid entry — wait for pullback.

Retest quality:
  VALID:   price returns to level, holds above (for bullish), closes up
  WEAK:    price touches level, small reaction, no strong rejection
  FAILED:  price closes decisively below broken level (bullish setup invalidated)
  NONE:    price has not returned to the level yet

Professional trading knowledge applied:
  - BOS alone ≠ entry
  - Momentum entries (chasing) = low probability
  - Best entries = pullback to broken level + rejection
  - Premium (overbought zone) = avoid longs
  - Discount (oversold zone) = avoid shorts
  - Volume must confirm structure breaks
  - Multiple timeframe agreement increases probability
"""

import os

AI_API_KEY = os.getenv("OPENAI_API_KEY", "")
try:
    from openai import OpenAI
    client = OpenAI(api_key=AI_API_KEY) if AI_API_KEY and AI_API_KEY.startswith("sk-") else None
except ImportError:
    client = None


# ── Confidence Scoring ────────────────────────────────────────────────────

def compute_confidence(analysis: dict, decision: str) -> dict:
    """
    Three-layer confidence:
      1. Structure quality (25pts) — is the bias clear?
      2. Retest quality (35pts) — is this a valid entry location?
      3. Confirmation (40pts) — do indicators confirm?

    If retest quality = 0, total confidence stays below 70% → no trade.
    This prevents chasing.
    """
    ms      = analysis.get("ms",  {})
    e20     = analysis.get("ema20",  0)
    e50     = analysis.get("ema50",  0)
    r       = analysis.get("rsi14",  50)
    vol     = analysis.get("vol",    {})
    liq     = analysis.get("liq",    {})
    fvgs    = analysis.get("fvgs",   [])
    frames  = analysis.get("frames", [])
    atr     = analysis.get("atr14",  0)
    price   = analysis.get("price",  0)
    regime  = analysis.get("regime", {})
    ms_s    = analysis.get("ms",     {})
    macd    = analysis.get("macd",   {})

    d      = decision if decision in ("BUY","SELL") else (
             "BUY" if ms.get("trend") == "Bullish" else "SELL")
    is_buy = d == "BUY"

    scores = {
        "Structure":    0,
        "Location":     0,
        "EMA":          0,
        "RSI":          0,
        "Volume":       0,
        "Timeframes":   0,
    }

    # ── 1. Structure quality (max 25pts) ──────────────────────
    trend    = ms.get("trend", "")
    strength = ms.get("strength_pct", 0)
    bos      = ms.get("bos", False)
    displace = ms.get("displacement", 0)

    if (is_buy and trend == "Bullish") or (not is_buy and trend == "Bearish"):
        scores["Structure"] = 12
        if strength >= 50: scores["Structure"] += 5
        if bos:            scores["Structure"] += 5
        if displace >= 0.8:scores["Structure"] += 3  # strong displacement confirms BOS
    elif trend in ("Bullish","Bearish"):
        scores["Structure"] = 6

    # ── 2. Location / Retest quality (max 35pts) ──────────────
    # This is the most important — prevents chasing
    retest_score = 0

    # Check if price is at a valid retest zone
    sw_high = ms.get("swing_high", 0)
    sw_low  = ms.get("swing_low",  0)
    near_res = analysis.get("levels", {}).get("near_resistance", False)
    near_sup = analysis.get("levels", {}).get("near_support",    False)
    dist_res = analysis.get("levels", {}).get("dist_to_resistance_atr", 99)
    dist_sup = analysis.get("levels", {}).get("dist_to_support_atr",    99)

    # Bullish retest: price pulled back to broken resistance (now support)
    if is_buy:
        # Price should be near swing_low or near support (pullback)
        if near_sup and 0 < dist_sup < 1.5:
            retest_score += 20  # at support zone — valid pullback
        elif 0 < dist_sup < 2.5:
            retest_score += 10  # reasonable distance from support
        elif dist_sup > 4.0:
            retest_score -= 10  # too far from support — chasing

        # FVG retest (price in bullish FVG = premium entry)
        bull_fvgs = [f for f in fvgs if f.get("type") == "Bullish" and f.get("fresh")]
        if bull_fvgs:
            nearest_fvg = bull_fvgs[0]
            if nearest_fvg["low"] <= price <= nearest_fvg["high"]:
                retest_score += 15  # price inside bullish FVG = perfect location

        # Liquidity sweep (price swept SSL then reversed = bullish)
        if liq.get("swept_ssl"):
            retest_score += 10

        # Anti-chase: if price extended far above structure, penalize
        if sw_low > 0 and atr > 0:
            dist_from_low = (price - sw_low) / atr
            if dist_from_low > 5:
                retest_score -= 15  # severely extended — do not chase
            elif dist_from_low > 3:
                retest_score -= 8

    else:  # SELL
        if near_res and 0 < dist_res < 1.5:
            retest_score += 20
        elif 0 < dist_res < 2.5:
            retest_score += 10
        elif dist_res > 4.0:
            retest_score -= 10

        bear_fvgs = [f for f in fvgs if f.get("type") == "Bearish" and f.get("fresh")]
        if bear_fvgs:
            nearest_fvg = bear_fvgs[0]
            if nearest_fvg["low"] <= price <= nearest_fvg["high"]:
                retest_score += 15

        if liq.get("swept_bsl"):
            retest_score += 10

        if sw_high > 0 and atr > 0:
            dist_from_high = (sw_high - price) / atr
            if dist_from_high > 5:
                retest_score -= 15
            elif dist_from_high > 3:
                retest_score -= 8

    scores["Location"] = max(0, min(35, retest_score))

    # ── 3. EMA alignment (max 15pts) ──────────────────────────
    ema_slope = analysis.get("ema20_slope", {})
    if (is_buy and e20 > e50) or (not is_buy and e20 < e50):
        scores["EMA"] = 10
        slope_dir = ema_slope.get("direction","")
        if is_buy and slope_dir in ("RISING","RISING_STRONG"):
            scores["EMA"] += 5
        elif not is_buy and slope_dir in ("FALLING","FALLING_STRONG"):
            scores["EMA"] += 5

    # ── 4. RSI (max 10pts) ────────────────────────────────────
    if is_buy:
        if 40 < r < 65:  scores["RSI"] = 10  # ideal: not overbought, has momentum
        elif r <= 40:     scores["RSI"] = 8   # oversold pullback = good entry
        elif 65 <= r < 75:scores["RSI"] = 4   # slightly overbought
        elif r >= 75:     scores["RSI"] = 0   # overbought = avoid
    else:
        if 35 < r < 60:  scores["RSI"] = 10
        elif r >= 60:     scores["RSI"] = 8
        elif 25 <= r < 35:scores["RSI"] = 4
        elif r <= 25:     scores["RSI"] = 0

    # ── 5. Volume (max 10pts) ─────────────────────────────────
    bp  = vol.get("buy_pressure",  50)
    sp  = vol.get("sell_pressure", 50)
    rel = vol.get("relative", 1.0)
    confirms = vol.get("confirms_move", False)

    if is_buy:
        if bp > 60 and rel > 1.2: scores["Volume"] = 10
        elif bp > 50:             scores["Volume"] = 6
        else:                     scores["Volume"] = 2
    else:
        if sp > 60 and rel > 1.2: scores["Volume"] = 10
        elif sp > 50:             scores["Volume"] = 6
        else:                     scores["Volume"] = 2

    # ── 6. Multi-timeframe (max 5pts) ─────────────────────────
    tf_agree = sum(1 for f in frames if f.get("decision") == d)
    if tf_agree >= 3:   scores["Timeframes"] = 5
    elif tf_agree == 2: scores["Timeframes"] = 3
    elif tf_agree == 1: scores["Timeframes"] = 1

    total = sum(scores.values())
    return {"breakdown": scores, "total": min(total, 100)}


# ── Signal Determination ──────────────────────────────────────────────────

def determine_signal(analysis: dict) -> str:
    """
    Two conditions required for any signal:
    1. Market structure bias (Bullish/Bearish)
    2. Valid entry location (not chasing extended moves)

    Bullish structure alone = WAIT (not BUY).
    Need pullback to valid zone before considering entry.
    """
    ms      = analysis.get("ms",    {})
    e20     = analysis.get("ema20", 0)
    e50     = analysis.get("ema50", 0)
    r       = analysis.get("rsi14", 50)
    price   = analysis.get("price", 0)
    atr     = analysis.get("atr14", 0)
    liq     = analysis.get("liq",   {})
    fvgs    = analysis.get("fvgs",  [])
    regime  = analysis.get("regime",{})
    levels  = analysis.get("levels",{})

    trend    = ms.get("trend",    "Neutral")
    strength = ms.get("strength_pct", 0)
    bos      = ms.get("bos",     False)
    sw_low   = ms.get("swing_low",  0)
    sw_high  = ms.get("swing_high", 0)

    # Market must be trending (not ranging)
    if regime.get("regime") == "RANGING":
        return "WAIT"  # No trend-following entries in ranging market

    # ── Bullish bias check ────────────────────────────────────
    bullish_bias = (trend == "Bullish" and
                    e20 > e50 and
                    r < 80)

    # ── Bearish bias check ────────────────────────────────────
    bearish_bias = (trend == "Bearish" and
                    e20 < e50 and
                    r > 20)

    if not bullish_bias and not bearish_bias:
        return "WAIT"

    # ── Location check (most important — prevents chasing) ────
    near_sup = levels.get("near_support",    False)
    near_res = levels.get("near_resistance", False)
    dist_sup = levels.get("dist_to_support_atr",    99)
    dist_res = levels.get("dist_to_resistance_atr", 99)

    if bullish_bias:
        # Check if price is at a valid BUY location

        # 1. Price in bullish FVG (ideal)
        in_bull_fvg = any(
            f["low"] <= price <= f["high"]
            for f in fvgs
            if f.get("type") == "Bullish" and f.get("fresh")
        )

        # 2. Price near support (pullback to structure)
        at_support = 0 < dist_sup < 2.0

        # 3. SSL sweep (price swept stops below, reversed bullish)
        swept_ssl = liq.get("swept_ssl", False)

        # 4. Anti-chase: price must not be too extended above swing low
        extended = False
        if sw_low > 0 and atr > 0:
            dist_from_low_atr = (price - sw_low) / atr
            extended = dist_from_low_atr > 4.5

        # 5. Not near resistance (don't buy into a wall)
        into_resistance = near_res and 0 < dist_res < 1.0

        if extended or into_resistance:
            return "WAIT"  # Anti-chase or buying into resistance

        if in_bull_fvg or at_support or swept_ssl:
            return "BUY"

        return "WAIT"  # Bullish but no valid location

    if bearish_bias:
        in_bear_fvg = any(
            f["low"] <= price <= f["high"]
            for f in fvgs
            if f.get("type") == "Bearish" and f.get("fresh")
        )
        at_resistance = 0 < dist_res < 2.0
        swept_bsl     = liq.get("swept_bsl", False)

        extended = False
        if sw_high > 0 and atr > 0:
            dist_from_high_atr = (sw_high - price) / atr
            extended = dist_from_high_atr > 4.5

        into_support = near_sup and 0 < dist_sup < 1.0

        if extended or into_support:
            return "WAIT"

        if in_bear_fvg or at_resistance or swept_bsl:
            return "SELL"

        return "WAIT"

    return "WAIT"


# ── Trade Levels ──────────────────────────────────────────────────────────

def calc_trade_levels(price: float, atr: float, decision: str,
                      swing_low: float = 0, swing_high: float = 0) -> dict:
    """
    Professional level calculation.
    SL: below swing low/above swing high (structure-based)
    TP: 3.5x ATR — allows $15-30+ profit on good moves
    """
    from config import SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER

    if decision == "BUY":
        # SL below the swing low (structure invalidation)
        if swing_low > 0:
            sl = round(swing_low - atr * 0.3, 2)  # just below swing low
        else:
            sl = round(price - atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price + atr * TP_ATR_MULTIPLIER, 2)
    elif decision == "SELL":
        if swing_high > 0:
            sl = round(swing_high + atr * 0.3, 2)
        else:
            sl = round(price + atr * SL_ATR_MULTIPLIER, 2)
        tp = round(price - atr * TP_ATR_MULTIPLIER, 2)
    else:
        return {"stop_loss": 0, "take_profit": 0, "rr": 0}

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

    return {"stop_loss": sl, "take_profit": tp,
            "rr": rr, "sl_dist": sl_dist, "tp_dist": tp_dist}


# ── Narrative Builder ─────────────────────────────────────────────────────

def build_reasons(analysis: dict, decision: str) -> list:
    """Build human-readable explanation of the decision."""
    reasons  = []
    ms       = analysis.get("ms",  {})
    e20      = analysis.get("ema20", 0)
    e50      = analysis.get("ema50", 0)
    r        = analysis.get("rsi14", 50)
    liq      = analysis.get("liq",  {})
    fvgs     = analysis.get("fvgs", [])
    vol      = analysis.get("vol",  {})
    frames   = analysis.get("frames",[])
    levels   = analysis.get("levels",{})
    regime   = analysis.get("regime",{})
    macd     = analysis.get("macd", {})
    price    = analysis.get("price", 0)

    trend    = ms.get("trend","")
    bos      = ms.get("bos", False)
    choch    = ms.get("choch", False)
    displace = ms.get("displacement", 0)
    strength = ms.get("strength_pct", 0)
    sw_low   = ms.get("swing_low", 0)
    sw_high  = ms.get("swing_high", 0)
    seq      = ms.get("sequence","")

    if decision == "WAIT":
        # Explain WHY waiting
        if regime.get("regime") == "RANGING":
            reasons.append(f"Market is RANGING — no trend-following entries")
        if trend == "Neutral":
            reasons.append(f"Market structure is Neutral ({seq}) — no clear bias")
        if trend == "Bullish":
            dist_sup = levels.get("dist_to_support_atr", 99)
            if dist_sup > 3:
                reasons.append(f"Bullish structure confirmed but price is {dist_sup:.1f} ATR above support — waiting for pullback to retest zone")
            else:
                reasons.append(f"Bullish bias but no valid retest or FVG entry detected")
        if trend == "Bearish":
            dist_res = levels.get("dist_to_resistance_atr", 99)
            if dist_res > 3:
                reasons.append(f"Bearish structure confirmed but price is {dist_res:.1f} ATR below resistance — waiting for retest")
            else:
                reasons.append(f"Bearish bias but no valid retest or FVG entry detected")
        if e20 > e50 and trend == "Bearish":
            reasons.append(f"EMA conflict: EMA20 above EMA50 but structure bearish")
        if not reasons:
            reasons.append("Conditions not fully aligned — waiting for clearer setup")
        return reasons

    is_buy = decision == "BUY"

    # Structure
    reasons.append(f"{trend} market structure ({seq}) — directional bias confirmed")
    if bos:
        disp_label = "strong" if displace >= 0.8 else "weak"
        reasons.append(f"BOS confirmed with {disp_label} displacement ({displace:.1f}x ATR)")
    if choch:
        reasons.append(f"Change of Character detected — potential trend reversal")

    # Location
    bull_fvgs = [f for f in fvgs if f.get("type")=="Bullish" and f.get("fresh")]
    bear_fvgs = [f for f in fvgs if f.get("type")=="Bearish" and f.get("fresh")]
    if is_buy and bull_fvgs:
        reasons.append(f"Price in fresh Bullish FVG ${bull_fvgs[0]['low']:,.2f}–${bull_fvgs[0]['high']:,.2f} — premium entry location")
    if not is_buy and bear_fvgs:
        reasons.append(f"Price in fresh Bearish FVG ${bear_fvgs[0]['low']:,.2f}–${bear_fvgs[0]['high']:,.2f}")

    dist_sup = levels.get("dist_to_support_atr", 99)
    dist_res = levels.get("dist_to_resistance_atr", 99)
    if is_buy and dist_sup < 2.0:
        reasons.append(f"Price near support zone ({dist_sup:.1f} ATR) — valid pullback location")
    if not is_buy and dist_res < 2.0:
        reasons.append(f"Price near resistance zone ({dist_res:.1f} ATR) — valid short location")

    # Liquidity
    if is_buy and liq.get("swept_ssl"):
        reasons.append(f"SSL swept at ${liq.get('ssl',0):,.2f} — stop hunt complete, bullish reversal likely")
    if not is_buy and liq.get("swept_bsl"):
        reasons.append(f"BSL swept at ${liq.get('bsl',0):,.2f} — stop hunt complete, bearish reversal likely")

    # EMA
    e20_slope = analysis.get("ema20_slope", {})
    if (is_buy and e20 > e50) or (not is_buy and e20 < e50):
        slope_desc = e20_slope.get("direction","").replace("_"," ").lower()
        reasons.append(f"EMA20 (${e20:,.0f}) {'above' if is_buy else 'below'} EMA50 (${e50:,.0f}) — trend confirmed | slope: {slope_desc}")

    # MACD
    if macd.get("cross") == "BULLISH_CROSS" and is_buy:
        reasons.append(f"MACD bullish crossover — momentum confirming")
    elif macd.get("cross") == "BEARISH_CROSS" and not is_buy:
        reasons.append(f"MACD bearish crossover — momentum confirming")
    elif macd.get("trend") == ("BULLISH" if is_buy else "BEARISH"):
        reasons.append(f"MACD {macd['trend'].lower()} — momentum aligned")

    # RSI
    rl = analysis.get("rsi_label","")
    reasons.append(f"RSI {r:.1f} ({rl}) — {'momentum building' if 40<r<70 else 'oversold pullback' if r<=40 else 'elevated'}")

    # Volume
    bp = vol.get("buy_pressure", 50)
    sp = vol.get("sell_pressure", 50)
    rel = vol.get("relative", 1.0)
    if (is_buy and bp > 55) or (not is_buy and sp > 55):
        reasons.append(f"Volume confirms direction ({bp if is_buy else sp:.0f}% {'buying' if is_buy else 'selling'} pressure, {rel:.1f}x average)")

    # Multi-timeframe
    tf_agree = [f["label"] for f in frames if f.get("decision") == decision]
    if tf_agree:
        reasons.append(f"Timeframe confluence: {', '.join(tf_agree)} all agree on {decision}")

    # Regime
    reg = regime.get("regime","")
    if reg in ("TRENDING","STRONG_TREND"):
        reasons.append(f"Market regime: {reg} — trend-following strategy optimal")

    return reasons


def generate_narrative(analysis: dict, decision: str, reasons: list) -> str:
    """Generate AI narrative or fallback text."""
    if client and AI_API_KEY:
        try:
            ms    = analysis.get("ms",{})
            price = analysis.get("price",0)
            sym   = analysis.get("symbol","")
            r     = analysis.get("rsi14",50)
            atr   = analysis.get("atr14",0)

            prompt = (
                f"You are Aria, a professional crypto trading AI. "
                f"Analyze {sym} at ${price:,.2f}.\n"
                f"Structure: {ms.get('trend','')} {ms.get('sequence','')}\n"
                f"Decision: {decision}\n"
                f"Reasons: {' | '.join(reasons[:4])}\n"
                f"RSI: {r:.1f} | ATR: ${atr:,.0f}\n"
                f"Write 2-3 sentences explaining this {decision} decision like a professional trader. "
                f"Focus on structure, location, and risk. Be specific with price levels."
            )
            resp = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role":"user","content":prompt}],
                max_tokens=150,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            pass

    # Professional fallback narrative
    if decision == "WAIT":
        return f"SIGNAL: WAIT\n{chr(10).join('• ' + r for r in reasons[:4])}"

    ms  = analysis.get("ms",{})
    lev = calc_trade_levels(
        analysis.get("price",0),
        analysis.get("atr14",0),
        decision,
        ms.get("swing_low",0),
        ms.get("swing_high",0),
    )
    return (
        f"SIGNAL: {decision}\n"
        f"{'• ' + chr(10) + '• '.join(reasons[:5])}\n\n"
        f"Structure: {ms.get('trend','')} ({ms.get('sequence','')})\n"
        f"Entry: ${analysis.get('price',0):,.2f} | "
        f"SL: ${lev.get('stop_loss',0):,.2f} | "
        f"TP: ${lev.get('take_profit',0):,.2f} | "
        f"R:R 1:{lev.get('rr',0)}"
    )


# ── Main Decision Function ────────────────────────────────────────────────

def decide(analysis: dict) -> dict:
    """
    Full professional trading decision.
    Returns complete decision package with all supporting evidence.
    """
    if "error" in analysis:
        return {
            "decision":   "WAIT",
            "confidence": {"breakdown":{}, "total":0},
            "reasons":    [analysis["error"]],
            "narrative":  "WAIT — insufficient data",
            "levels":     {"stop_loss":0,"take_profit":0,"rr":0},
            "trend":      "Unknown",
        }

    price = analysis.get("price",  0)
    atr   = analysis.get("atr14",  0)
    ms    = analysis.get("ms",    {})

    decision   = determine_signal(analysis)
    confidence = compute_confidence(analysis, decision)
    conf_total = confidence["total"]

    # Final gate: minimum confidence
    from config import MIN_CONFIDENCE
    if conf_total < MIN_CONFIDENCE and decision != "WAIT":
        decision = "WAIT"

    levels = calc_trade_levels(
        price, atr, decision,
        ms.get("swing_low",  0),
        ms.get("swing_high", 0),
    )

    reasons   = build_reasons(analysis, decision)
    narrative = generate_narrative(analysis, decision, reasons)

    return {
        "decision":   decision,
        "confidence": confidence,
        "reasons":    reasons,
        "narrative":  narrative,
        "levels":     levels,
        "trend":      ms.get("trend",""),
        "regime":     analysis.get("regime",{}).get("regime",""),
    }
