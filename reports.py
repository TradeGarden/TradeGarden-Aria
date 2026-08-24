"""
reports.py - Stage 7: Performance Reports
Full breakdown including exit reasons and loss analysis.
"""
from datetime import datetime, timedelta
from database import load_closed_trades

def _safe_avg(vals):
    return round(sum(vals)/len(vals), 2) if vals else 0.0

def _wr(trades):
    if not trades: return 0.0
    return round(len([t for t in trades if float(t.get("pl",0)) > 0])
                 / len(trades) * 100, 1)

def _build(trades, label):
    if not trades:
        return {"period": label, "trades": 0,
                "message": f"No completed trades in the {label.lower()}."}
    wins   = [t for t in trades if float(t.get("pl",0)) > 0]
    losses = [t for t in trades if float(t.get("pl",0)) <= 0]
    pls    = [float(t.get("pl",0)) for t in trades]
    best   = max(trades, key=lambda t: float(t.get("pl",0)))
    worst  = min(trades, key=lambda t: float(t.get("pl",0)))

    # Exit reason breakdown
    reasons = {}
    for t in trades:
        r = t.get("exit_reason","Unknown")
        if "Stop Loss" in r:     key = "Stop Loss hit"
        elif "Take Profit" in r: key = "Take Profit hit"
        elif "Partial TP" in r:  key = "Partial TP"
        elif "Timeout" in r:     key = "Timeout exit"
        elif "Structure" in r:   key = "Structure invalidated"
        elif "Manual" in r:      key = "Manual close"
        else:                    key = r[:30]
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "period":      label,
        "trades":      len(trades),
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    _wr(trades),
        "total_pl":    round(sum(pls), 2),
        "avg_win":     _safe_avg([float(t.get("pl",0)) for t in wins]),
        "avg_loss":    _safe_avg([float(t.get("pl",0)) for t in losses]),
        "best_trade":  {"pl": float(best.get("pl",0)),
                        "symbol": best.get("symbol",""),
                        "side": best.get("side","")},
        "worst_trade": {"pl": float(worst.get("pl",0)),
                        "symbol": worst.get("symbol",""),
                        "side": worst.get("side","")},
        "exit_reasons":reasons,
        "generated_at":datetime.utcnow().isoformat(),
    }

def daily_report():   return _build(load_closed_trades(1),   "Daily")
def weekly_report():  return _build(load_closed_trades(7),   "Weekly")
def monthly_report(): return _build(load_closed_trades(30),  "Monthly")
def full_stats():     return _build(load_closed_trades(999), "All Time")
