"""
recommendations.py - Stage 8: Improvement Suggestions
Analyses all completed trades and gives actionable advice.
"""
from database import load_closed_trades

MIN_TRADES_FOR_RECS = 3  # Show analysis after just 3 trades

def generate():
    trades = load_closed_trades(999)
    total  = len(trades)

    if total == 0:
        return [{"title": "No Completed Trades Yet",
                 "detail": "Aria has not completed any trades yet. Once trades close (via SL, TP, or timeout), full analysis will appear here.",
                 "priority": "info"}]

    if total < MIN_TRADES_FOR_RECS:
        return [{"title": f"Collecting Data ({total} trades so far)",
                 "detail": f"Need {MIN_TRADES_FOR_RECS} completed trades for full analysis. {MIN_TRADES_FOR_RECS - total} more to go.",
                 "priority": "info"}]

    def wr(subset):
        if not subset: return 0.0
        return round(len([t for t in subset if float(t.get("pl",0)) > 0]) / len(subset) * 100, 1)

    def avg_pl(subset):
        if not subset: return 0.0
        return round(sum(float(t.get("pl",0)) for t in subset) / len(subset), 2)

    recs         = []
    overall_wr   = wr(trades)
    total_pl     = round(sum(float(t.get("pl",0)) for t in trades), 2)
    wins         = [t for t in trades if float(t.get("pl",0)) > 0]
    losses       = [t for t in trades if float(t.get("pl",0)) <= 0]
    btc_trades   = [t for t in trades if t.get("symbol","") == "BTCUSD"]
    eth_trades   = [t for t in trades if t.get("symbol","") == "ETHUSD"]
    high_conf    = [t for t in trades if int(t.get("confidence",0)) >= 75]
    low_conf     = [t for t in trades if int(t.get("confidence",0)) < 75]
    buy_trades   = [t for t in trades if t.get("side","") == "BUY"]
    sell_trades  = [t for t in trades if t.get("side","") == "SELL"]

    # Overall performance
    emoji = "✅" if total_pl > 0 else "❌"
    recs.append({
        "title": f"{emoji} Overall: {total} trades | Win rate {overall_wr}% | Total P/L ${total_pl:+,.2f}",
        "detail": (f"Completed {total} trades. "
                   f"{len(wins)} wins averaging ${avg_pl(wins):+.2f} each. "
                   f"{len(losses)} losses averaging ${avg_pl(losses):+.2f} each. "
                   f"Total profit/loss: ${total_pl:+,.2f}."),
        "priority": "info" if total_pl >= 0 else "high",
    })

    # Confidence analysis
    if high_conf and low_conf:
        hc_wr = wr(high_conf)
        lc_wr = wr(low_conf)
        diff  = round(hc_wr - lc_wr, 1)
        if diff >= 10:
            recs.append({
                "title": f"High confidence trades win more ({diff}% better)",
                "detail": (f"Trades with 75%+ confidence: {hc_wr}% win rate ({len(high_conf)} trades). "
                           f"Trades below 75%: {lc_wr}% win rate ({len(low_conf)} trades). "
                           f"Recommendation: Consider raising minimum confidence to 75%."),
                "priority": "high",
            })

    # BTC vs ETH
    if btc_trades and eth_trades:
        btc_wr = wr(btc_trades)
        eth_wr = wr(eth_trades)
        btc_pl = round(sum(float(t.get("pl",0)) for t in btc_trades), 2)
        eth_pl = round(sum(float(t.get("pl",0)) for t in eth_trades), 2)
        recs.append({
            "title": f"BTC: {btc_wr}% WR ${btc_pl:+,.2f} | ETH: {eth_wr}% WR ${eth_pl:+,.2f}",
            "detail": (f"BTC: {len(btc_trades)} trades, {btc_wr}% win rate, ${btc_pl:+,.2f} total. "
                       f"ETH: {len(eth_trades)} trades, {eth_wr}% win rate, ${eth_pl:+,.2f} total."),
            "priority": "info",
        })

    # BUY vs SELL
    if buy_trades and sell_trades:
        b_wr = wr(buy_trades)
        s_wr = wr(sell_trades)
        recs.append({
            "title": f"BUY: {b_wr}% win rate | SELL: {s_wr}% win rate",
            "detail": (f"BUY trades: {len(buy_trades)} total, {b_wr}% win rate. "
                       f"SELL trades: {len(sell_trades)} total, {s_wr}% win rate. "
                       + ("Consider focusing on BUY setups only." if b_wr > s_wr + 20 else
                          "Consider focusing on SELL setups only." if s_wr > b_wr + 20 else
                          "Both directions performing similarly.")),
            "priority": "medium" if abs(b_wr - s_wr) > 20 else "info",
        })

    # Win rate warning
    if overall_wr < 40 and total >= 5:
        recs.append({
            "title": "Win rate below 40% - review entry rules",
            "detail": (f"Only {overall_wr}% of {total} trades are profitable. "
                       "This may mean entries are too early, stops too tight, or "
                       "market conditions don't match the strategy."),
            "priority": "high",
        })
    elif overall_wr >= 60:
        recs.append({
            "title": f"Good win rate at {overall_wr}%",
            "detail": f"Winning {overall_wr}% of {total} trades. Strategy is performing well. Keep following the rules.",
            "priority": "info",
        })

    return recs
