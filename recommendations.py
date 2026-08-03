"""
recommendations.py - Stage 8
"""
from database import load_closed_trades
from config import MIN_TRADES_FOR_RECS

def generate():
    trades = load_closed_trades(999)
    if len(trades) < MIN_TRADES_FOR_RECS:
        return [{"title":"Not Enough Data Yet",
                 "detail":f"Complete at least {MIN_TRADES_FOR_RECS} trades. You have {len(trades)} so far.",
                 "priority":"info"}]
    recs = []
    def wr(subset):
        return round(len([t for t in subset if t.get("pl",0)>0])/len(subset)*100,1) if subset else 0.0

    overall_wr = wr(trades)
    high_conf = [t for t in trades if t.get("confidence",0)>=75]
    low_conf  = [t for t in trades if t.get("confidence",0)<75]
    if high_conf and low_conf:
        hc_wr = wr(high_conf); lc_wr = wr(low_conf)
        if hc_wr - lc_wr >= 15:
            recs.append({"title":"Raise Minimum Confidence Threshold",
                         "detail":f"Trades ≥75% confidence won {hc_wr}% vs {lc_wr}% below 75%. Consider raising the minimum.",
                         "priority":"high"})
    if overall_wr < 50 and len(trades) >= 10:
        recs.append({"title":"Overall Win Rate Below 50%",
                     "detail":f"Win rate is {overall_wr}% across {len(trades)} trades. Review entry criteria.",
                     "priority":"high"})
    if not recs:
        recs.append({"title":"Strategy Performing Well",
                     "detail":f"Win rate {overall_wr}% across {len(trades)} trades. No changes recommended.",
                     "priority":"info"})
    return recs
