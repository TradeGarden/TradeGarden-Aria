"""
recommendations.py — Stage 8: IMPROVEMENT RECOMMENDATIONS
============================================================
Aria analyzes its own trade history and suggests improvements.
It NEVER changes the strategy automatically.
The user decides whether to apply each recommendation.

Each recommendation has:
  title    — short name shown in the dashboard
  detail   — full explanation shown when clicked
  priority — "high" / "medium" / "info"
"""

from journal import load_closed_trades
from config import MIN_TRADES_FOR_RECS


def generate() -> list:
    """
    Analyze closed trades and return a list of recommendations.
    Returns informational message if not enough data yet.
    """
    trades = load_closed_trades()

    if len(trades) < MIN_TRADES_FOR_RECS:
        return [{
            "title":    "Not Enough Data Yet",
            "detail":   f"Complete at least {MIN_TRADES_FOR_RECS} trades to receive "
                        f"personalized recommendations. You have {len(trades)} so far.",
            "priority": "info",
        }]

    recs = []

    def win_rate(subset):
        if not subset:
            return 0.0
        return round(len([t for t in subset if t.get("pl", 0) > 0]) / len(subset) * 100, 1)

    def avg_pl(subset):
        if not subset:
            return 0.0
        return round(sum(t.get("pl", 0) for t in subset) / len(subset), 2)

    overall_wr = win_rate(trades)

    # ── 1. Confidence threshold ──────────────────
    high_conf = [t for t in trades if t.get("confidence", 0) >= 75]
    low_conf  = [t for t in trades if t.get("confidence", 0) < 75]

    if high_conf and low_conf:
        hc_wr = win_rate(high_conf)
        lc_wr = win_rate(low_conf)
        if hc_wr - lc_wr >= 15:
            recs.append({
                "title":    "Raise Minimum Confidence Threshold",
                "detail":   (
                    f"Trades taken with confidence ≥ 75% had a win rate of {hc_wr}%. "
                    f"Trades taken below 75% confidence had a win rate of only {lc_wr}%. "
                    f"That is a {round(hc_wr - lc_wr, 1)}% difference. "
                    f"Consider raising the minimum confidence threshold from 75% to 80%."
                ),
                "priority": "high",
            })

    # ── 2. Session performance ───────────────────
    session_data = {}
    for t in trades:
        sess = t.get("session", "Unknown")
        session_data.setdefault(sess, []).append(t)

    for sess, sess_trades in session_data.items():
        if len(sess_trades) < 3:
            continue
        sess_avg = avg_pl(sess_trades)
        sess_wr  = win_rate(sess_trades)
        if sess_avg < 0 and sess_wr < 40:
            recs.append({
                "title":    f"Avoid Trading During {sess} Session",
                "detail":   (
                    f"During the {sess} session you have taken {len(sess_trades)} trades "
                    f"with a win rate of {sess_wr}% and average P/L of ${sess_avg}. "
                    f"Performance is significantly below your overall average. "
                    f"Consider pausing trading during this session and only trading "
                    f"during sessions where results are stronger."
                ),
                "priority": "medium",
            })

    # ── 3. Overall win rate ──────────────────────
    if overall_wr < 50 and len(trades) >= 10:
        recs.append({
            "title":    "Overall Win Rate Below 50%",
            "detail":   (
                f"Your current win rate is {overall_wr}% across {len(trades)} trades. "
                f"This suggests entries may be taken too early or without enough confirmation. "
                f"Consider waiting for all conditions to align before entering a trade, "
                f"and review whether any single condition is consistently failing."
            ),
            "priority": "high",
        })

    # ── 4. R:R performance ───────────────────────
    rr_trades = [t for t in trades if t.get("rr", 0) >= 2]
    low_rr    = [t for t in trades if 0 < t.get("rr", 0) < 2]
    if rr_trades and low_rr:
        rr_wr     = win_rate(rr_trades)
        low_rr_wr = win_rate(low_rr)
        if rr_wr - low_rr_wr >= 10:
            recs.append({
                "title":    "Higher R:R Trades Perform Better",
                "detail":   (
                    f"Trades with R:R ≥ 1:2 had a win rate of {rr_wr}%. "
                    f"Trades with R:R below 1:2 had a win rate of {low_rr_wr}%. "
                    f"The current minimum R:R of 1:2 appears to be working. "
                    f"You may want to test raising it to 1:2.5 for further improvement."
                ),
                "priority": "info",
            })

    # ── 5. WAIT decisions ───────────────────────
    # If lots of WAIT followed by missed moves, recommend adjusting sensitivity
    # (placeholder — requires more data tracking)

    # ── 6. All good ─────────────────────────────
    if not recs:
        recs.append({
            "title":    "Strategy Performing Well",
            "detail":   (
                f"Win rate is {overall_wr}% across {len(trades)} trades. "
                f"No major changes are recommended at this time. "
                f"Continue following the current rules and check back as more trades complete."
            ),
            "priority": "info",
        })

    return recs
