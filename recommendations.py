"""
recommendations.py - Aria Engine Health & Intelligence Analysis
==============================================================
WIN != TARGET HIT — the most important distinction.
Tracks exit quality, break-even efficiency, entry quality.
Detects engine glitches automatically.
"""
from database import load_closed_trades, load_journal
from datetime import datetime


def _wr(t):
    if not t: return 0.0
    return round(len([x for x in t if float(x.get("pl",0))>0])/len(t)*100,1)

def _avg(vals):
    return round(sum(vals)/len(vals),2) if vals else 0.0

def _pf(trades):
    w = sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0))>0)
    l = abs(sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0))<0))
    return round(w/l,2) if l>0 else 999.0

def _bar(n, total, width=20):
    filled = int(n/max(total,1)*width)
    return "█"*filled + "░"*(width-filled)


def generate() -> list:
    trades = load_closed_trades(999)
    total  = len(trades)
    target = 30
    recs   = []

    # ── No trades yet ─────────────────────────────────────────
    if total == 0:
        return [{
            "priority":"INFO","emoji":"🚀",
            "title":"Aria is running — no completed trades yet",
            "detail":("Aria scans every 60 seconds. Once a trade closes "
                      "(via Take Profit, Stop Loss, break-even, or timeout), "
                      "full analysis appears here.\n\n"
                      "Check the Journal page to see open positions and "
                      "what Aria is seeing right now."),
            "confidence":"—","action":"No action needed. Let Aria trade.",
            "evidence":"0 completed trades",
        }]

    if total < 3:
        return [{
            "priority":"INFO","emoji":"📊",
            "title":f"Collecting data — {total}/3 minimum trades",
            "detail":f"{3-total} more completed trade(s) needed to begin analysis.",
            "confidence":"—","action":"No action needed.",
            "evidence":f"{total} trade(s) recorded",
        }]

    # ── Calculate core stats ──────────────────────────────────
    wins   = [t for t in trades if float(t.get("pl",0)) > 0]
    losses = [t for t in trades if float(t.get("pl",0)) < 0]
    be_trades = [t for t in trades if abs(float(t.get("pl",0))) < 0.15]
    tp_trades = [t for t in trades
                 if "Take Profit" in t.get("exit_reason","")
                 or "Partial" in t.get("exit_reason","")]
    sl_trades = [t for t in trades if "Stop Loss" in t.get("exit_reason","")]
    manual_t  = [t for t in trades if "Manual" in t.get("exit_reason","")]
    timeout_t = [t for t in trades if "Timeout" in t.get("exit_reason","")
                 or "timeout" in t.get("exit_reason","").lower()]
    struct_t  = [t for t in trades if "Structure" in t.get("exit_reason","")
                 or "structure" in t.get("exit_reason","").lower()]

    total_pl  = round(sum(float(t.get("pl",0)) for t in trades), 2)
    win_rate  = _wr(trades)
    avg_win   = _avg([float(t.get("pl",0)) for t in wins])
    avg_loss  = _avg([float(t.get("pl",0)) for t in losses])
    pf        = _pf(trades)
    expectancy= round((win_rate/100*avg_win)+((1-win_rate/100)*avg_loss),2)

    rr_vals   = [float(t.get("rr",0)) for t in trades if float(t.get("rr",0))>0]
    avg_rr    = _avg(rr_vals)

    # ── 1. SAMPLE SIZE ────────────────────────────────────────
    progress  = min(total, target)
    conf_lbl  = "Very Low" if total<5 else "Low" if total<10 else "Medium" if total<20 else "High"
    recs.append({
        "priority":"HIGH" if total<target else "INFO",
        "emoji":"📊",
        "title":f"Sample Size: {total}/{target} trades — Confidence {conf_lbl}",
        "detail":(
            f"Progress: {_bar(total,target)} {total}/{target}\n\n"
            f"Current results: {win_rate}% win rate | ${total_pl:+.2f} P/L | "
            f"Profit factor: {pf if pf!=999.0 else '∞'}\n\n"
            + (f"⚠️ Only {total} trades is NOT enough to validate a strategy. "
               f"Do not change rules yet. Keep collecting data."
               if total < target else
               f"✅ Sufficient data for reliable analysis.")
        ),
        "confidence":conf_lbl,
        "action":("Keep rules unchanged. Collect more data." if total<target
                  else "Review all recommendations carefully."),
        "evidence":f"{total} completed trades",
    })

    # ── 2. ENGINE HEALTH (separate from strategy) ─────────────
    engine_score = 100
    engine_issues = []

    # Check for journal entries to detect glitches
    try:
        journal = load_journal()
        open_entries = [j for j in journal if j.get("action")=="OPEN"]
        # Low confidence opens
        low_conf = [j for j in open_entries if int(j.get("confidence",100)) < 60]
        if low_conf:
            engine_score -= 20
            engine_issues.append(f"{len(low_conf)} trades opened below 60% confidence — possible rule bypass")
        # Zero trend strength opens
        zero_str = [j for j in open_entries if int(j.get("strength",100)) < 10]
        if zero_str:
            engine_score -= 25
            engine_issues.append(f"{len(zero_str)} trades opened with near-zero trend strength — this was the glitch causing bad entries")
    except Exception:
        pass

    # Check for losing streaks
    recent_pl = [float(t.get("pl",0)) for t in trades[-10:]]
    streak = cur_streak = 0
    for pl in recent_pl:
        if pl < 0: cur_streak += 1; streak = max(streak, cur_streak)
        else: cur_streak = 0
    if streak >= 3:
        engine_score -= 30
        engine_issues.append(f"Losing streak of {streak} in last 10 trades")

    strategy_score = min(100, int(
        (win_rate * 0.4) +
        (min(pf if pf!=999.0 else 3, 3) / 3 * 30) +
        (30 if expectancy > 0 else 0)
    ))

    recs.append({
        "priority":"HIGH" if engine_score<70 else "INFO",
        "emoji":"🏥",
        "title":f"Engine Health: {engine_score}/100 | Strategy Confidence: {strategy_score}/100",
        "detail":(
            f"ENGINE HEALTH (is the bot working correctly?): {engine_score}/100\n"
            + ("\n".join(f"  ⚠️ {i}" for i in engine_issues) if engine_issues
               else "  ✅ No glitches detected\n")
            + f"\n\nSTRATEGY CONFIDENCE (is the strategy profitable?): {strategy_score}/100\n"
            f"  Win Rate: {win_rate}%\n"
            f"  Profit Factor: {pf if pf!=999.0 else '∞'}\n"
            f"  Expectancy: ${expectancy:+.2f} per trade\n"
            f"  Avg Win: ${avg_win:+.2f} | Avg Loss: ${avg_loss:+.2f}"
        ),
        "confidence":conf_lbl,
        "action":("Fix engine issues above first." if engine_issues
                  else "Engine running correctly."),
        "evidence":f"Based on {total} trades",
    })

    # ── 3. WIN vs TARGET HIT (most important distinction) ─────
    tp_count  = len(tp_trades)
    be_count  = len(be_trades)
    sl_count  = len(sl_trades)
    win_count = len(wins)

    # Target capture rate
    target_rate = round(tp_count/max(total,1)*100,1)
    be_rate     = round(be_count/max(total,1)*100,1)

    exit_detail = (
        f"EXIT BREAKDOWN — this is what's ACTUALLY happening:\n\n"
        f"  Take Profit hit:    {tp_count}/{total} trades ({target_rate}%)\n"
        f"  Break-Even exit:    {be_count}/{total} trades ({be_rate}%)\n"
        f"  Stop Loss hit:      {sl_count}/{total} trades ({round(sl_count/max(total,1)*100,1)}%)\n"
        f"  Manual close:       {len(manual_t)}/{total} trades\n"
        f"  Timeout:            {len(timeout_t)}/{total} trades\n"
        f"  Structure invalid:  {len(struct_t)}/{total} trades\n\n"
        f"  WIN RATE: {win_rate}% ({win_count} wins)\n"
        f"  TARGET HIT RATE: {target_rate}% ({tp_count} targets)\n\n"
        + ("⚠️ WIN ≠ TARGET HIT. Most wins came from break-even, not the planned target. "
           "The entries are producing positive movement but trades are not reaching their intended targets. "
           "This means exit management may need review, NOT entry rules."
           if be_count > tp_count else
           "✅ Trades are reaching their intended targets.")
    )

    be_priority = "HIGH" if be_count > total*0.5 and tp_count == 0 else "MEDIUM" if be_count > tp_count else "INFO"
    recs.append({
        "priority": be_priority,
        "emoji":"🎯",
        "title":f"Exit Quality: {target_rate}% targets hit | {be_rate}% break-even exits",
        "detail": exit_detail,
        "confidence": conf_lbl,
        "action":(
            "DO NOT change entry rules. The entries are working. "
            "Investigate whether break-even triggers too early relative to ATR and structure."
            if be_count > tp_count else
            "Exit management is working well."
        ),
        "evidence":f"TP: {tp_count} | BE: {be_count} | SL: {sl_count} of {total} trades",
    })

    # ── 4. Break-even efficiency analysis ─────────────────────
    if be_count >= 2:
        recs.append({
            "priority":"MEDIUM",
            "emoji":"⚡",
            "title":f"Break-Even Analysis: {be_count} trades exited at break-even",
            "detail":(
                f"Break-even protection triggered {be_count} times.\n\n"
                f"This means Aria correctly protected the trades from turning into losses. "
                f"However, {be_count} trades that moved in the right direction "
                f"ended at $0 profit instead of hitting the take profit target.\n\n"
                f"Possible causes:\n"
                f"  1. Break-even triggers too early — price pulls back before continuing\n"
                f"  2. Take profit target is too far for current volatility\n"
                f"  3. Market is in consolidation — moves stall before target\n"
                f"  4. Entry timing is slightly late in the move\n\n"
                f"Current BE trigger: +$2 profit → SL moves to entry\n"
                f"Current TP target: ~2.5x ATR from entry\n\n"
                f"DO NOT change until 20+ trades collected."
            ),
            "confidence":"Low" if total<15 else "Medium",
            "action":"Monitor for 20+ trades before adjusting BE trigger.",
            "evidence":f"{be_count} break-even exits of {total} total",
        })

    # ── 5. Risk/Reward Analysis ───────────────────────────────
    if rr_vals:
        recs.append({
            "priority":"INFO",
            "emoji":"💰",
            "title":f"Risk/Reward: Avg 1:{avg_rr:.2f} | Expectancy ${expectancy:+.2f}/trade",
            "detail":(
                f"Average planned R:R: 1:{avg_rr:.2f} (minimum required: 1:1.8)\n"
                f"Expectancy: ${expectancy:+.2f} per trade\n"
                f"  Positive expectancy = the strategy has a mathematical edge\n"
                f"  Negative expectancy = losing money long term on average\n\n"
                f"Profit Factor: {pf if pf!=999.0 else '∞'}\n"
                f"  > 1.5 = good | > 2.0 = excellent | < 1.0 = losing strategy\n\n"
                f"Average winner: ${avg_win:+.2f}\n"
                f"Average loser:  ${avg_loss:+.2f}\n"
                f"Best trade:     ${float(max(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n"
                f"Worst trade:    ${float(min(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}"
            ),
            "confidence":conf_lbl,
            "action":"Keep R:R minimum at 1.8. Review if avg drops below 1.5.",
            "evidence":f"{len(rr_vals)} trades with R:R data",
        })

    # ── 6. Pattern Discovery ──────────────────────────────────
    btc_t = [t for t in trades if t.get("symbol","")=="BTCUSD"]
    eth_t = [t for t in trades if t.get("symbol","")=="ETHUSD"]
    buy_t = [t for t in trades if t.get("side","")=="BUY"]
    sell_t= [t for t in trades if t.get("side","")=="SELL"]

    if btc_t and eth_t:
        btc_wr = _wr(btc_t); eth_wr = _wr(eth_t)
        btc_pl = round(sum(float(t.get("pl",0)) for t in btc_t),2)
        eth_pl = round(sum(float(t.get("pl",0)) for t in eth_t),2)
        if abs(btc_wr-eth_wr) >= 20:
            better = "BTC" if btc_wr>eth_wr else "ETH"
            recs.append({
                "priority":"MEDIUM","emoji":"📈",
                "title":f"{better} performing significantly better",
                "detail":(f"BTC: {btc_wr}% WR ({len(btc_t)} trades) ${btc_pl:+.2f}\n"
                          f"ETH: {eth_wr}% WR ({len(eth_t)} trades) ${eth_pl:+.2f}\n\n"
                          f"Difference of {abs(btc_wr-eth_wr):.0f}% is meaningful."),
                "confidence":"Low" if total<15 else "Medium",
                "action":f"Prioritize {better} setups.",
                "evidence":f"{len(btc_t)} BTC + {len(eth_t)} ETH trades",
            })

    if buy_t and sell_t and abs(_wr(buy_t)-_wr(sell_t)) >= 25:
        better = "BUY" if _wr(buy_t)>_wr(sell_t) else "SELL"
        recs.append({
            "priority":"MEDIUM","emoji":"🎯",
            "title":f"{better} trades outperforming significantly",
            "detail":(f"BUY: {_wr(buy_t)}% WR ({len(buy_t)} trades)\n"
                      f"SELL: {_wr(sell_t)}% WR ({len(sell_t)} trades)"),
            "confidence":"Low" if total<15 else "Medium",
            "action":f"Be more selective on {'SELL' if better=='BUY' else 'BUY'} entries.",
            "evidence":f"{len(buy_t)} buys + {len(sell_t)} sells",
        })

    # ── 7. Market Condition Tracking ──────────────────────────
    bull_t = [t for t in trades if "Bullish" in t.get("trend","")]
    bear_t = [t for t in trades if "Bearish" in t.get("trend","")]
    recs.append({
        "priority":"INFO","emoji":"🧠",
        "title":"Market Condition Tracking — Learning",
        "detail":(
            f"Aria is tracking performance across conditions:\n\n"
            f"  Bullish structure: {len(bull_t)}/{total} trades | {_wr(bull_t)}% WR\n"
            f"  Bearish structure: {len(bear_t)}/{total} trades | {_wr(bear_t)}% WR\n"
            f"  BTC: {len(btc_t)}/{total} | ETH: {len(eth_t)}/{total}\n"
            f"  BUY: {len(buy_t)}/{total} | SELL: {len(sell_t)}/{total}\n\n"
            f"Status: {'Learning' if total<target else 'Pattern analysis available'}\n"
            f"Need {max(0,target-total)} more trades for reliable patterns."
        ),
        "confidence":conf_lbl,
        "action":f"{'Continue collecting data.' if total<target else 'Review patterns above.'}",
        "evidence":f"{total}/{target} trades collected",
    })

    return recs
