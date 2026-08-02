"""
reports.py — Stage 7: REPORTS
================================
Reads from PostgreSQL trade_history table via database.py.
"""
from datetime import datetime, timedelta
from database import load_closed_trades

def _safe_avg(values):
    return round(sum(values)/len(values), 2) if values else 0.0

def _win_rate(trades):
    if not trades: return 0.0
    return round(len([t for t in trades if t.get("pl",0)>0])/len(trades)*100, 1)

def _build_report(trades, period_label):
    if not trades:
        return {"period":period_label,"trades":0,"message":f"No completed trades in the {period_label.lower()}."}
    wins   = [t for t in trades if t.get("pl",0)>0]
    losses = [t for t in trades if t.get("pl",0)<=0]
    pls    = [t.get("pl",0) for t in trades]
    best   = max(trades, key=lambda t: t.get("pl",0))
    worst  = min(trades, key=lambda t: t.get("pl",0))
    high_conf = [t for t in trades if t.get("confidence",0)>=75]
    low_conf  = [t for t in trades if t.get("confidence",0)<75]
    return {
        "period":period_label,"trades":len(trades),
        "wins":len(wins),"losses":len(losses),
        "win_rate":_win_rate(trades),
        "total_pl":round(sum(pls),2),
        "avg_win":_safe_avg([t.get("pl",0) for t in wins]),
        "avg_loss":_safe_avg([t.get("pl",0) for t in losses]),
        "best_trade":{"pl":best.get("pl",0),"symbol":best.get("symbol",""),"side":best.get("side","")},
        "worst_trade":{"pl":worst.get("pl",0),"symbol":worst.get("symbol",""),"side":worst.get("side","")},
        "high_conf_wr":_win_rate(high_conf) if high_conf else None,
        "low_conf_wr":_win_rate(low_conf) if low_conf else None,
        "generated_at":datetime.utcnow().isoformat(),
    }

def daily_report():   return _build_report(load_closed_trades(1),  "Daily")
def weekly_report():  return _build_report(load_closed_trades(7),  "Weekly")
def monthly_report(): return _build_report(load_closed_trades(30), "Monthly")
def full_stats():     return _build_report(load_closed_trades(999),"All Time")
