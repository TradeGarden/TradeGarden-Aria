"""
recommendations.py - Aria Engine Health & Improvement System
=============================================================
Not just stats. Actual analysis of what's working and what's failing.

4 layers:
  1. Statistical confidence (is there enough data?)
  2. Pattern discovery (what conditions produce wins?)
  3. Engine health checks (detect glitches, rule violations)
  4. Risk/reward analysis (is the strategy actually good?)

Activates at 3 trades. Gets smarter with more data.
"""
from database import load_closed_trades, load_journal
from datetime import datetime


# ── Helpers ───────────────────────────────────────────────────

def _wr(trades):
    if not trades: return 0.0
    return round(len([t for t in trades if float(t.get("pl",0)) > 0])
                 / len(trades) * 100, 1)

def _avg(vals):
    return round(sum(vals)/len(vals), 2) if vals else 0.0

def _profit_factor(trades):
    wins  = sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0)) > 0)
    loss  = abs(sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0)) < 0))
    return round(wins / loss, 2) if loss > 0 else float("inf")


# ── Engine Health Checks ──────────────────────────────────────

def check_engine_health(trades, journal_entries) -> list:
    """
    Detect actual glitches and engine failures.
    Returns list of issues found.
    """
    issues = []

    # Check 1: Trades opened with 0% trend strength
    zero_strength = [t for t in trades
                     if t.get("exit_reason","").lower().find("strength") > -1]
    # Check journal for suspicious opens
    open_entries = [j for j in journal_entries if j.get("action") == "OPEN"]
    low_conf_trades = [j for j in open_entries
                       if int(j.get("confidence",100)) < 65]
    if low_conf_trades:
        issues.append({
            "type":     "ENGINE_GLITCH",
            "priority": "HIGH",
            "title":    f"Low confidence entries detected ({len(low_conf_trades)} trades)",
            "detail":   f"{len(low_conf_trades)} trades were opened below 65% confidence. "
                        f"This may indicate the confidence scoring had a glitch or "
                        f"rules were not properly enforced. Review those entries.",
            "action":   "Check journal entries marked as low confidence",
        })

    # Check 2: Trades closed immediately at break-even
    be_trades = [t for t in trades
                 if t.get("exit_reason","").lower().find("break") > -1
                 or abs(float(t.get("pl",0))) < 0.10]
    if len(be_trades) > 0:
        issues.append({
            "type":     "PERFORMANCE",
            "priority": "MEDIUM",
            "title":    f"{len(be_trades)} trades closed at/near break-even ($0)",
            "detail":   f"These trades hit break-even SL after profit protection triggered. "
                        f"This is correct behavior — the trade was protected. "
                        f"But if happening frequently, entry timing may need improvement.",
            "action":   "Review if entries were at the right location or too late in the move",
        })

    # Check 3: Trades with very fast timeout (under 30 minutes)
    fast_closes = []
    for t in trades:
        dur = t.get("duration","")
        if "m" in dur and "h" not in dur:
            try:
                mins = int(dur.split("m")[0])
                if mins < 30:
                    fast_closes.append(t)
            except Exception:
                pass
    if fast_closes:
        issues.append({
            "type":     "PERFORMANCE",
            "priority": "MEDIUM",
            "title":    f"{len(fast_closes)} trades closed in under 30 minutes",
            "detail":   "These trades moved against you quickly or hit structure "
                        "invalidation very fast. This can indicate entries at "
                        "exhaustion points or during low-quality setups.",
            "action":   "Review entry conditions on these fast-close trades",
        })

    # Check 4: Consecutive losses
    pls = [float(t.get("pl",0)) for t in trades[-10:]]
    streak = 0
    max_streak = 0
    for pl in pls:
        if pl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if max_streak >= 3:
        issues.append({
            "type":     "RISK",
            "priority": "HIGH",
            "title":    f"Losing streak of {max_streak} detected in last 10 trades",
            "detail":   f"A streak of {max_streak} consecutive losses suggests "
                        f"the market may be in a regime that doesn't suit the current strategy. "
                        f"Consider pausing until market conditions improve.",
            "action":   "Review market structure during the losing streak period",
        })

    return issues


# ── Pattern Discovery ─────────────────────────────────────────

def discover_patterns(trades) -> list:
    """Find what conditions produce wins vs losses."""
    insights = []
    if len(trades) < 5:
        return insights

    # BTC vs ETH performance
    btc = [t for t in trades if t.get("symbol","") == "BTCUSD"]
    eth = [t for t in trades if t.get("symbol","") == "ETHUSD"]
    if btc and eth:
        btc_wr = _wr(btc)
        eth_wr = _wr(eth)
        btc_pl = round(sum(float(t.get("pl",0)) for t in btc), 2)
        eth_pl = round(sum(float(t.get("pl",0)) for t in eth), 2)
        diff = abs(btc_wr - eth_wr)
        if diff >= 20:
            better   = "BTC" if btc_wr > eth_wr else "ETH"
            worse    = "ETH" if btc_wr > eth_wr else "BTC"
            better_wr= btc_wr if btc_wr > eth_wr else eth_wr
            worse_wr = eth_wr if btc_wr > eth_wr else btc_wr
            insights.append({
                "priority":   "MEDIUM",
                "emoji":      "📊",
                "title":      f"{better} outperforms {worse} significantly",
                "detail":     f"{better}: {better_wr}% win rate | {worse}: {worse_wr}% win rate. "
                              f"Difference of {diff:.0f}% is meaningful.",
                "confidence": "Medium" if len(trades) < 20 else "High",
                "action":     f"Consider focusing on {better} setups when conditions allow.",
                "evidence":   f"BTC: {len(btc)} trades ${btc_pl:+.2f} | ETH: {len(eth)} trades ${eth_pl:+.2f}",
            })

    # BUY vs SELL
    buys  = [t for t in trades if t.get("side","") == "BUY"]
    sells = [t for t in trades if t.get("side","") == "SELL"]
    if buys and sells:
        b_wr = _wr(buys)
        s_wr = _wr(sells)
        if abs(b_wr - s_wr) >= 25:
            better = "BUY" if b_wr > s_wr else "SELL"
            insights.append({
                "priority":   "MEDIUM",
                "emoji":      "🎯",
                "title":      f"{better} trades are significantly stronger",
                "detail":     f"BUY: {b_wr}% win rate ({len(buys)} trades) | "
                              f"SELL: {s_wr}% win rate ({len(sells)} trades). "
                              f"One direction is clearly stronger.",
                "confidence": "Low" if len(trades) < 15 else "Medium",
                "action":     f"Prioritize {better} setups. Be more selective on "
                              f"{'SELL' if better=='BUY' else 'BUY'} entries.",
                "evidence":   f"Based on {len(trades)} completed trades",
            })

    # High confidence vs low confidence
    high_c = [t for t in trades if int(t.get("confidence",0)) >= 80]
    low_c  = [t for t in trades if int(t.get("confidence",0)) < 80]
    if high_c and low_c:
        hc_wr = _wr(high_c)
        lc_wr = _wr(low_c)
        if hc_wr > lc_wr + 15:
            insights.append({
                "priority":   "HIGH",
                "emoji":      "🔥",
                "title":      f"High confidence trades win more (+{hc_wr-lc_wr:.0f}% difference)",
                "detail":     f"80%+ confidence: {hc_wr}% win rate ({len(high_c)} trades). "
                              f"Below 80%: {lc_wr}% win rate ({len(low_c)} trades). "
                              f"The higher your confidence, the better the outcome.",
                "confidence": "Medium",
                "action":     "Consider raising minimum confidence to 80%.",
                "evidence":   f"Based on {len(trades)} trades",
            })

    # Exit reason analysis
    exit_reasons = {}
    for t in trades:
        r = t.get("exit_reason","Unknown")
        if "Stop Loss" in r:     key = "Stop Loss"
        elif "Take Profit" in r: key = "Take Profit"
        elif "Partial" in r:     key = "Partial TP"
        elif "Timeout" in r:     key = "Timeout"
        elif "Structure" in r:   key = "Structure Invalid"
        elif "Manual" in r:      key = "Manual Close"
        else:                    key = "Other"
        if key not in exit_reasons:
            exit_reasons[key] = {"count":0,"pl":0}
        exit_reasons[key]["count"] += 1
        exit_reasons[key]["pl"]    += float(t.get("pl",0))

    # If manual closes are dominant
    manual = exit_reasons.get("Manual Close",{})
    if manual.get("count",0) > len(trades) * 0.4:
        insights.append({
            "priority":   "MEDIUM",
            "emoji":      "⚠️",
            "title":      f"{manual['count']} trades closed manually ({manual['count']/len(trades)*100:.0f}%)",
            "detail":     f"Manual closes dominate. This means Aria's automatic "
                          f"milestones (TP, trail, structure) are not completing the trades. "
                          f"Either the targets are too far or manual intervention is happening.",
            "confidence": "High",
            "action":     "Let Aria's milestones handle exits. Only manually close if "
                          "market structure clearly reverses.",
            "evidence":   f"Manual P/L: ${manual['pl']:+.2f} across {manual['count']} trades",
        })

    return insights


# ── Main Generate Function ────────────────────────────────────

def generate() -> list:
    trades  = load_closed_trades(999)
    total   = len(trades)
    target  = 30  # trades needed for reliable analysis
    recs    = []

    # ── Section 1: Sample size warning ───────────────────────
    progress = min(int(total / target * 100), 100)
    bar_fill = "█" * (progress // 10) + "░" * (10 - progress // 10)

    if total == 0:
        return [{
            "priority":   "INFO",
            "emoji":      "🚀",
            "title":      "Aria is running — waiting for first completed trade",
            "detail":     "Aria is scanning every 60 seconds. Once a trade "
                          "closes (via Take Profit, Stop Loss, or timeout), "
                          "full analysis appears here. Check the Journal page "
                          "to see open positions.",
            "confidence": "—",
            "action":     "No action needed. Let Aria trade.",
            "evidence":   "0 completed trades",
        }]

    if total < 3:
        return [{
            "priority":   "INFO",
            "emoji":      "📊",
            "title":      f"Collecting data — {total}/30 trades completed",
            "detail":     f"Need at least 3 completed trades to start analysis. "
                          f"{3 - total} more to go.",
            "confidence": "—",
            "action":     "No action needed. Let Aria trade.",
            "evidence":   f"{total} completed trade{'s' if total!=1 else ''}",
        }]

    # Stats
    wins      = [t for t in trades if float(t.get("pl",0)) > 0]
    losses    = [t for t in trades if float(t.get("pl",0)) < 0]
    total_pl  = round(sum(float(t.get("pl",0)) for t in trades), 2)
    win_rate  = _wr(trades)
    avg_win   = _avg([float(t.get("pl",0)) for t in wins])
    avg_loss  = _avg([float(t.get("pl",0)) for t in losses])
    pf        = _profit_factor(trades)
    best      = max(trades, key=lambda t: float(t.get("pl",0)))
    worst     = min(trades, key=lambda t: float(t.get("pl",0)))

    # Sample size status
    if total < target:
        conf_label = "Low" if total < 10 else "Medium"
        status_emoji = "🟡" if total < 10 else "🟠"
        recs.append({
            "priority":   "HIGH",
            "emoji":      "📊",
            "title":      f"Sample size: {total}/{target} trades — {conf_label} confidence",
            "detail":     (f"Current results: {win_rate}% win rate, ${total_pl:+.2f} P/L. "
                           f"This is {'promising' if total_pl > 0 else 'concerning'} but "
                           f"{target - total} more trades are needed before conclusions "
                           f"can be trusted. Do not change rules based on {total} trades.\n\n"
                           f"Progress: {bar_fill} {total}/{target}"),
            "confidence": conf_label,
            "action":     "Keep rules unchanged. Collect more data.",
            "evidence":   f"{total} completed trades",
        })
    else:
        recs.append({
            "priority":   "INFO",
            "emoji":      "✅",
            "title":      f"Sufficient data — {total} trades analyzed",
            "detail":     (f"Win rate: {win_rate}% | Total P/L: ${total_pl:+.2f} | "
                           f"Profit factor: {pf} | Avg win: ${avg_win:+.2f} | "
                           f"Avg loss: ${avg_loss:+.2f}"),
            "confidence": "High",
            "action":     "Review recommendations below carefully.",
            "evidence":   f"{total} completed trades",
        })

    # ── Section 2: Overall health ─────────────────────────────
    health_score = 50
    if win_rate >= 60:  health_score += 20
    elif win_rate < 40: health_score -= 20
    if total_pl > 0:    health_score += 15
    else:               health_score -= 15
    if pf > 1.5:        health_score += 15
    elif pf < 1.0:      health_score -= 15
    health_score = max(0, min(100, health_score))

    health_label = ("🟢 Healthy" if health_score >= 70
                    else "🟡 Early Stage" if health_score >= 50
                    else "🔴 Needs Attention")

    recs.append({
        "priority":   "INFO",
        "emoji":      "🏥",
        "title":      f"Engine Health: {health_score}/100 — {health_label}",
        "detail":     (f"Win Rate: {win_rate}% | P/L: ${total_pl:+.2f} | "
                       f"Profit Factor: {pf if pf != float('inf') else '∞'} | "
                       f"Best trade: ${float(best.get('pl',0)):+.2f} | "
                       f"Worst trade: ${float(worst.get('pl',0)):+.2f} | "
                       f"Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:+.2f}"),
        "confidence": "Medium" if total < 20 else "High",
        "action":     ("Keep current rules." if health_score >= 70
                       else "Review engine health issues below."),
        "evidence":   f"{total} trades, {len(wins)} wins, {len(losses)} losses",
    })

    # ── Section 3: Risk/Reward analysis ──────────────────────
    rr_vals = [float(t.get("rr",0)) for t in trades if float(t.get("rr",0)) > 0]
    avg_rr  = _avg(rr_vals)
    if avg_rr > 0:
        expectancy = round((win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss), 2)
        recs.append({
            "priority":   "MEDIUM" if expectancy < 0 else "INFO",
            "emoji":      "💰",
            "title":      f"Expectancy: ${expectancy:+.2f} per trade | Avg R:R 1:{avg_rr:.2f}",
            "detail":     (f"Expectancy shows average profit per trade. "
                           f"${expectancy:+.2f} means on average each trade "
                           f"{'makes' if expectancy > 0 else 'loses'} ${abs(expectancy):.2f}. "
                           f"Positive expectancy = edge. Negative = no edge yet."),
            "confidence": "Low" if total < 10 else "Medium",
            "action":     ("Good edge developing." if expectancy > 0
                           else "Negative expectancy — review entry quality."),
            "evidence":   f"Calculated from {len(rr_vals)} completed trades",
        })

    # ── Section 4: Engine health checks ──────────────────────
    try:
        journal = load_journal()
    except Exception:
        journal = []

    issues = check_engine_health(trades, journal)
    for issue in issues:
        recs.append({
            "priority":   issue["priority"],
            "emoji":      "⚠️" if issue["priority"]=="HIGH" else "🔍",
            "title":      issue["title"],
            "detail":     issue["detail"],
            "confidence": "High",
            "action":     issue["action"],
            "evidence":   f"Detected in trade history",
        })

    # ── Section 5: Pattern discovery ─────────────────────────
    patterns = discover_patterns(trades)
    recs.extend(patterns)

    # ── Section 6: Market condition tracking ─────────────────
    recs.append({
        "priority":   "INFO",
        "emoji":      "🧠",
        "title":      "Market Condition Tracking",
        "detail":     (f"Aria is tracking performance across:\n"
                       f"• Bullish structure: {len([t for t in trades if 'Bullish' in t.get('trend','')])}/{total} trades\n"
                       f"• Bearish structure: {len([t for t in trades if 'Bearish' in t.get('trend','')])}/{total} trades\n"
                       f"• BTC trades: {len([t for t in trades if t.get('symbol','')=='BTCUSD'])}/{total}\n"
                       f"• ETH trades: {len([t for t in trades if t.get('symbol','')=='ETHUSD'])}/{total}\n"
                       f"• BUY trades: {len([t for t in trades if t.get('side','')=='BUY'])}/{total}\n"
                       f"• SELL trades: {len([t for t in trades if t.get('side','')=='SELL'])}/{total}\n\n"
                       f"Status: {'Learning' if total < target else 'Analyzing'}"),
        "confidence": "Low" if total < target else "Medium",
        "action":     f"{'Collect ' + str(target-total) + ' more trades for reliable patterns.' if total < target else 'Patterns available — see above.'}",
        "evidence":   f"{total} trades analyzed",
    })

    return recs
