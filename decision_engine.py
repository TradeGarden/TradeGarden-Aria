"""
decision_engine.py — Stage 3: DECIDE
======================================
This is Aria's brain.

Responsibilities:
  - Read all analysis data from analyzer.py
  - Compute confidence score (0–100) from 6 indicators
  - Make exactly ONE decision: BUY / SELL / WAIT
  - Build a reasons list (only true reasons, never invented ones)
  - Generate AI narrative via OpenAI (or fallback text)
  - Return a complete decision package

WAIT is a valid and successful decision.
Aria never forces a trade.
"""

import os
from openai import OpenAI

AI_API_KEY = os.getenv("AI_API_KEY")
client     = OpenAI(api_key=AI_API_KEY) if AI_API_KEY and AI_API_KEY.startswith("sk-") else None


# ──────────────────────────────────────────────
#  CONFIDENCE SCORE
# ──────────────────────────────────────────────

CONFIDENCE_MAX = {
    "Market Structure": 25,
    "EMA Alignment":    25,
    "RSI":              15,
    "Candlestick":      20,
    "Volume":           15,
}


def compute_confidence(analysis: dict, decision: str) -> dict:
    """
    Score the actual market conditions regardless of decision.
    Shows real strength of the setup so the user can see what is happening.
    For WAIT, we score the strongest possible direction (BUY or SELL)
    so the breakdown always shows meaningful numbers.
    """
    scores = {k: 0 for k in CONFIDENCE_MAX}

    ms  = analysis["ms"]
    e20 = analysis["ema20"]
    e50 = analysis["ema50"]
    r   = analysis["rsi14"]
    pat = analysis["patterns"]
    vol = analysis["vol"]

    # Determine dominant direction from market data
    if decision == "BUY":
        direction = "BUY"
    elif decision == "SELL":
        direction = "SELL"
    else:
        # For WAIT: score whichever direction the structure leans
        direction = "BUY" if ms["trend"] == "Bullish" else "SELL"

    is_buy  = direction == "BUY"
    is_sell = direction == "SELL"

    # Market Structure (25 pts)
    if ms["trend"] == "Bullish" and is_buy:
        scores["Market Structure"] = 25
    elif ms["trend"] == "Bearish" and is_sell:
        scores["Market Structure"] = 25
    elif ms["trend"] in ("Bullish", "Bearish"):
        scores["Market Structure"] = 10   # structure exists but misaligned

    # EMA Alignment (25 pts)
    ema_bull = e20 > e50
    ema_bear = e20 < e50
    if (is_buy and ema_bull) or (is_sell and ema_bear):
        scores["EMA Alignment"] = 25
    elif (is_buy and ema_bear) or (is_sell and ema_bull):
        scores["EMA Alignment"] = 0

    # RSI (15 pts) — reward momentum, penalise extremes against trade
    if is_buy:
        if 40 < r < 70:   scores["RSI"] = 15
        elif r <= 40:      scores["RSI"] = 10   # oversold, good for BUY
        elif 70 <= r < 80: scores["RSI"] = 5    # overbought, risky
        else:              scores["RSI"] = 0
    else:
        if 30 < r < 60:   scores["RSI"] = 15
        elif r >= 60:      scores["RSI"] = 10   # overbought, good for SELL
        elif 20 < r <= 30: scores["RSI"] = 5    # oversold, risky
        else:              scores["RSI"] = 0

    # Candlestick (20 pts) — optional, adds to score but never blocks
    bull_pat = [p for p in pat if p["direction"] == "Bullish"]
    bear_pat = [p for p in pat if p["direction"] == "Bearish"]
    if is_buy and bull_pat:
        scores["Candlestick"] = 20 if any(p["strength"] == "Strong" for p in bull_pat) else 10
    elif is_sell and bear_pat:
        scores["Candlestick"] = 20 if any(p["strength"] == "Strong" for p in bear_pat) else 10
    else:
        scores["Candlestick"] = 0   # no pattern = 0 pts, but does NOT block trade

    # Volume (15 pts)
    if is_buy:
        if vol["buy_pressure"] > 55:   scores["Volume"] = 15
        elif vol["buy_pressure"] > 45: scores["Volume"] = 8
    else:
        if vol["sell_pressure"] > 55:  scores["Volume"] = 15
        elif vol["sell_pressure"] > 45:scores["Volume"] = 8

    return {"breakdown": scores, "total": sum(scores.values())}


# ──────────────────────────────────────────────
#  SIGNAL LOGIC
# ──────────────────────────────────────────────

def determine_signal(analysis: dict) -> str:
    """
    Three conditions must align for BUY or SELL.
    If not fully aligned → WAIT.
    """
    e20  = analysis["ema20"]
    e50  = analysis["ema50"]
    r    = analysis["rsi14"]
    ms   = analysis["ms"]

    bull = e20 > e50 and r < 70 and ms["trend"] == "Bullish"
    bear = e20 < e50 and r > 30 and ms["trend"] == "Bearish"

    if bull:  return "BUY"
    if bear:  return "SELL"
    return "WAIT"


# ──────────────────────────────────────────────
#  TRADE LEVELS
# ──────────────────────────────────────────────

def calc_trade_levels(price: float, atr: float, decision: str) -> dict:
    """ATR-based stop loss and take profit. R:R always 1:2."""
    if decision == "BUY":
        sl = round(price - atr * 1.5, 2)
        tp = round(price + atr * 3.0, 2)
    elif decision == "SELL":
        sl = round(price + atr * 1.5, 2)
        tp = round(price - atr * 3.0, 2)
    else:
        sl = round(price - atr * 1.5, 2)
        tp = round(price + atr * 1.5, 2)

    sl_dist = abs(price - sl)
    tp_dist = abs(tp - price)
    rr      = round(tp_dist / sl_dist, 1) if sl_dist > 0 else 0.0

    return {"stop_loss": sl, "take_profit": tp, "rr": rr}


# ──────────────────────────────────────────────
#  REASONS
# ──────────────────────────────────────────────

def build_reasons(analysis: dict, decision: str) -> list:
    """
    Only include reasons that are actually true.
    Never invent reasons that don't exist in the data.
    """
    reasons = []
    e20  = analysis["ema20"]
    e50  = analysis["ema50"]
    r    = analysis["rsi14"]
    rl   = analysis["rsi_label"]
    ms   = analysis["ms"]
    vol  = analysis["vol"]
    pat  = analysis["patterns"]
    liq  = analysis["liq"]

    # EMA
    if e20 > e50:
        reasons.append(f"EMA20 (${e20:,.0f}) is above EMA50 (${e50:,.0f}) — bullish trend")
    elif e20 < e50:
        reasons.append(f"EMA20 (${e20:,.0f}) is below EMA50 (${e50:,.0f}) — bearish trend")

    # Market structure
    if ms["trend"] == "Bullish":
        reasons.append(f"Bullish market structure ({ms['sequence']})")
    elif ms["trend"] == "Bearish":
        reasons.append(f"Bearish market structure ({ms['sequence']})")
    else:
        reasons.append(f"Neutral structure ({ms['sequence']}) — no clear trend")

    # RSI
    if rl == "Overbought":
        reasons.append(f"RSI overbought ({r}) — momentum weakening")
    elif rl == "Oversold":
        reasons.append(f"RSI oversold ({r}) — potential reversal zone")
    else:
        reasons.append(f"RSI neutral ({r}) — no extreme momentum")

    # Volume
    if vol["sell_pressure"] > 55:
        reasons.append(f"Volume favors sellers ({vol['sell_pressure']}% selling pressure)")
    elif vol["buy_pressure"] > 55:
        reasons.append(f"Volume favors buyers ({vol['buy_pressure']}% buying pressure)")
    else:
        reasons.append("Volume is balanced — no strong conviction")

    # Candlestick
    bear_p = [p for p in pat if p["direction"] == "Bearish"]
    bull_p = [p for p in pat if p["direction"] == "Bullish"]
    if bear_p:
        reasons.append(f"{bear_p[0]['name']} detected — bearish signal ({bear_p[0]['reliability']} reliability)")
    elif bull_p:
        reasons.append(f"{bull_p[0]['name']} detected — bullish signal ({bull_p[0]['reliability']} reliability)")
    else:
        reasons.append("No significant candlestick pattern detected")

    # BOS / CHoCH
    if ms["bos"]:
        reasons.append("Break of Structure (BOS) confirmed — continuation signal")
    if ms["choch"]:
        reasons.append("Change of Character (CHoCH) — possible trend shift")

    # Liquidity sweep
    if liq["sweep"]:
        sw = liq["sweep"]
        reasons.append(f"Liquidity sweep {sw['direction']} — {sw['signal']}")

    # WAIT explanation
    if decision == "WAIT":
        reasons.append("Conditions not fully aligned — no trade taken. Waiting for confirmation.")

    return reasons


# ──────────────────────────────────────────────
#  AI NARRATIVE
# ──────────────────────────────────────────────

def get_narrative(analysis: dict, decision: str, confidence: int, levels: dict) -> str:
    """
    Generate a professional narrative from OpenAI.
    Falls back to a structured template if no API key is set.
    """
    ms      = analysis["ms"]
    vol     = analysis["vol"]
    pat     = analysis["patterns"]
    price   = analysis["price"]
    symbol  = analysis["symbol"]
    session = analysis.get("session", "")

    pat_str = ", ".join(
        f"{p['name']} ({p['reliability']} reliability)" for p in pat
    ) or "None detected"

    fallback = (
        f"SIGNAL: {decision}\n\n"
        f"REASON:\n"
        f"• {ms['trend']} {ms['structure']} structure ({ms['sequence']}) — "
        f"{ms['strength_label']} at {ms['strength_pct']}%\n"
        f"• EMA20 {'above' if analysis['ema20'] > analysis['ema50'] else 'below'} EMA50 — "
        f"{'bullish' if analysis['ema20'] > analysis['ema50'] else 'bearish'} trend\n"
        f"• RSI {analysis['rsi14']} — {analysis['rsi_label'].lower()}\n"
        f"• Volume: {vol['buy_pressure']}% buyers / {vol['sell_pressure']}% sellers ({vol['label']})\n"
        f"• Patterns: {pat_str}\n\n"
        f"RISK: Moderate\n\n"
        f"CONCLUSION: The market is in a {ms['trend'].lower()} phase. "
        f"Stop Loss ${levels['stop_loss']:,.2f} · "
        f"Take Profit ${levels['take_profit']:,.2f} · "
        f"R:R 1:{levels['rr']}"
    )

    if not client:
        return fallback

    try:
        prompt = f"""You are Aria, a professional crypto trading AI. Write a concise structured analysis.
Explain what the indicators mean together — do not just list numbers.

Symbol: {symbol} | Price: ${price:,.2f} | Session: {session}
Decision: {decision} | Confidence: {confidence}%
Structure: {ms['structure']} ({ms['sequence']}) | Trend: {ms['trend']} | Strength: {ms['strength_label']} {ms['strength_pct']}%
EMA20: ${analysis['ema20']:,.2f} | EMA50: ${analysis['ema50']:,.2f}
RSI: {analysis['rsi14']} ({analysis['rsi_label']})
MACD: {analysis['macd_line']:+.2f} vs signal {analysis['macd_signal']:+.2f}
Volume: buy {vol['buy_pressure']}% / sell {vol['sell_pressure']}% (relative x{vol['relative']}) — {vol['label']}
Patterns: {pat_str}
SL: ${levels['stop_loss']:,.2f} | TP: ${levels['take_profit']:,.2f} | R:R 1:{levels['rr']}

Format:
SIGNAL: [decision]

REASON:
• [market structure insight]
• [EMA insight with numbers]
• [RSI insight]
• [volume insight]
• [candlestick insight]

RISK: [Low/Moderate/High] — [one sentence why]

CONCLUSION: [2–3 sentences on the full market picture and what to watch]

Max 180 words. Be direct and specific."""

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=280,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()

    except Exception:
        return fallback


# ──────────────────────────────────────────────
#  MAIN DECIDE FUNCTION
# ──────────────────────────────────────────────

def decide(analysis: dict) -> dict:
    """
    Stage 3 — DECIDE.
    Takes the full analysis and returns one decision with all context.
    """
    if "error" in analysis:
        return {
            "decision":   "WAIT",
            "confidence": {"breakdown": {}, "total": 0},
            "reasons":    [analysis["error"]],
            "levels":     {"stop_loss": 0, "take_profit": 0, "rr": 0},
            "trend":      "Unknown",
            "narrative":  "Insufficient data to make a decision.",
        }

    decision = determine_signal(analysis)
    levels   = calc_trade_levels(analysis["price"], analysis["atr14"], decision)
    conf     = compute_confidence(analysis, decision)
    reasons  = build_reasons(analysis, decision)
    narrative= get_narrative(analysis, decision, conf["total"], levels)

    trend_label = analysis["ms"]["trend"]
    if trend_label not in ("Bullish", "Bearish"):
        trend_label = "Sideways"

    return {
        "decision":   decision,
        "trend":      trend_label,
        "confidence": conf,
        "reasons":    reasons,
        "levels":     levels,
        "narrative":  narrative,
    }
