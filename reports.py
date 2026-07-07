"""
reports.py — Stage 7: REPORTS
================================
Generates daily, weekly, and monthly performance reports
from the trade journal. No external dependencies.
"""

from datetime import datetime, timedelta
from journal import load_closed_trades


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def _safe_avg(values: list) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _win_rate(trades: list) -> float:
    if not trades:
        return 0.0
    wins = [t for t in trades if t.get("pl", 0) > 0]
    return round(len(wins) / len(trades) * 100, 1)


def _trades_in_days(days: int) -> list:
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = []
    for t in load_closed_trades():
        try:
            if datetime.fromisoformat(t.get("closed_at", "")) >= cutoff:
                result.append(t)
        except Exception:
            pass
    return result


def _build_report(trades: list, period_label: str) -> dict:
    if not trades:
        return {
            "period":  period_label,
            "trades":  0,
            "message": f"No completed trades in the {period_label.lower()}.",
        }

    wins   = [t for t in trades if t.get("pl", 0) > 0]
    losses = [t for t in trades if t.get("pl", 0) <= 0]
    pls    = [t.get("pl", 0) for t in trades]
    best   = max(trades, key=lambda t: t.get("pl", 0))
    worst  = min(trades, key=lambda t: t.get("pl", 0))

    # Session breakdown
    session_data = {}
    for t in trades:
        sess = t.get("session", "Unknown")
        session_data.setdefault(sess, []).append(t.get("pl", 0))
    session_summary = {
        sess: {
            "trades":   len(pls_),
            "total_pl": round(sum(pls_), 2),
            "win_rate": round(len([p for p in pls_ if p > 0]) / len(pls_) * 100, 1),
        }
        for sess, pls_ in session_data.items()
    }

    # Confidence performance
    high_conf = [t for t in trades if t.get("confidence", 0) >= 75]
    low_conf  = [t for t in trades if t.get("confidence", 0) < 75]

    return {
        "period":       period_label,
        "trades":       len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate":     _win_rate(trades),
        "total_pl":     round(sum(pls), 2),
        "avg_win":      _safe_avg([t.get("pl", 0) for t in wins]),
        "avg_loss":     _safe_avg([t.get("pl", 0) for t in losses]),
        "best_trade":   {"pl": best.get("pl", 0), "symbol": best.get("symbol", ""), "side": best.get("side", "")},
        "worst_trade":  {"pl": worst.get("pl", 0), "symbol": worst.get("symbol", ""), "side": worst.get("side", "")},
        "sessions":     session_summary,
        "high_conf_wr": _win_rate(high_conf) if high_conf else None,
        "low_conf_wr":  _win_rate(low_conf)  if low_conf  else None,
        "high_conf_trades": len(high_conf),
        "low_conf_trades":  len(low_conf),
        "generated_at": datetime.utcnow().isoformat(),
    }


# ──────────────────────────────────────────────
#  PUBLIC API
# ──────────────────────────────────────────────

def daily_report() -> dict:
    return _build_report(_trades_in_days(1), "Daily")


def weekly_report() -> dict:
    return _build_report(_trades_in_days(7), "Weekly")


def monthly_report() -> dict:
    return _build_report(_trades_in_days(30), "Monthly")


def full_stats() -> dict:
    """All-time statistics across every closed trade."""
    return _build_report(load_closed_trades(), "All Time")
