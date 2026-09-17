"""
recommendations.py - Aria Engine Health & Improvement Analysis
==============================================================
FIXED per review:

1. exit_map bug removed - get_type() called directly per trade
2. Fallback uses UNKNOWN_PROFIT/UNKNOWN_LOSS not guessed categories
3. BE analysis uses actual be_trigger_r from database
4. Losing streak removed from engine health (it's performance, not health)
5. capture_ratio replaces arbitrary 0.4 threshold
6. MFE/MAE analysis added
7. All language is descriptive not prescriptive
"""

from database import load_closed_trades, load_journal


def _avg(vals):
    return round(sum(vals)/len(vals), 2) if vals else 0.0

def _wr(t):
    if not t: return 0.0
    return round(len([x for x in t if float(x.get("pl",0))>0])/len(t)*100,1)

def _pf(trades):
    w = sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0))>0)
    l = abs(sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0))<0))
    return round(w/l,2) if l>0 else 999.0

def _bar(n, total, width=20):
    filled = int(n/max(total,1)*width)
    return "█"*filled + "░"*(width-filled)

def _sample_label(n):
    if n < 10:  return "Very Low"
    if n < 30:  return "Low — preliminary only"
    if n < 50:  return "Preliminary — trends visible"
    if n < 100: return "Moderate — useful patterns"
    return "Good — analysis is meaningful"


def get_type(t: dict) -> str:
    """
    Normalized exit type — exactly one value per trade.
    Uses exit_type field first, then exit_reason, then P/L as last resort.
    Falls back to UNKNOWN_* rather than guessing category.
    """
    et = t.get("exit_type","")
    if et and et not in ("UNKNOWN",""):
        return et

    r  = t.get("exit_reason","").lower()
    pl = float(t.get("pl", 0))

    if "partial tp"  in r: return "PARTIAL_TP"
    if "take profit" in r: return "TP"
    if "timeout"     in r: return "TIMEOUT"
    if "structure"   in r: return "STRUCTURE"
    if "manual"      in r: return "MANUAL"
    if "break-even"  in r or "break_even" in r: return "BREAK_EVEN"
    if "profit-lock" in r or "profit_lock" in r: return "PROFIT_LOCK"
    if "actual loss" in r: return "ACTUAL_SL"

    # Last resort: use UNKNOWN_* so we know classification is uncertain
    if pl > 0.50:    return "UNKNOWN_PROFIT"
    if pl > -0.10:   return "UNKNOWN_BE"
    return "UNKNOWN_LOSS"


def generate() -> list:
    trades = load_closed_trades(999)
    total  = len(trades)
    recs   = []

    if total == 0:
        return [{
            "priority":"INFO","emoji":"🚀",
            "title":"Aria is running — no completed trades yet",
            "detail":("Aria scans every 60 seconds. Once a trade closes, "
                      "full analysis appears here."),
            "confidence":"—","action":"No action needed.",
            "evidence":"0 completed trades",
        }]

    if total < 3:
        return [{
            "priority":"INFO","emoji":"📊",
            "title":f"Collecting data — {total}/3 minimum",
            "detail":f"Need 3+ completed trades to start.",
            "confidence":"—","action":"No action needed.",
            "evidence":f"{total} trade(s)",
        }]

    # ── Core stats ─────────────────────────────────────────────────────────
    wins      = [t for t in trades if float(t.get("pl",0)) > 0]
    losses    = [t for t in trades if float(t.get("pl",0)) < 0]
    total_pl  = round(sum(float(t.get("pl",0)) for t in trades), 2)
    win_rate  = _wr(trades)
    avg_win   = _avg([float(t.get("pl",0)) for t in wins])
    avg_loss  = _avg([float(t.get("pl",0)) for t in losses])
    pf        = _pf(trades)
    expectancy= round((win_rate/100*avg_win)+((1-win_rate/100)*avg_loss), 2)

    # Planned R:R
    rr_vals     = [float(t.get("rr",0)) for t in trades if float(t.get("rr",0))>0]
    avg_plan_rr = _avg(rr_vals)

    # Realized R from database
    realized_rs = [float(t.get("realized_r",0)) for t in trades
                   if float(t.get("realized_r",0)) != 0]
    if not realized_rs:
        # Calculate from P/L and risk
        for t in trades:
            r1 = float(t.get("risk_1r",0) or t.get("risk",0))
            pl = float(t.get("pl",0))
            if r1 > 0:
                realized_rs.append(round(pl/r1, 3))
    avg_realized_r = _avg(realized_rs)

    # Capture ratio: what fraction of planned R was actually captured
    capture_ratio  = round(avg_realized_r / avg_plan_rr, 3) if avg_plan_rr > 0 else 0

    # MFE/MAE from database
    mfe_vals = [float(t.get("mfe_r",0)) for t in trades if float(t.get("mfe_r",0)) > 0]
    mae_vals = [float(t.get("mae_r",0)) for t in trades if float(t.get("mae_r",0)) < 0]
    avg_mfe  = _avg(mfe_vals) if mfe_vals else 0
    avg_mae  = _avg(mae_vals) if mae_vals else 0

    # BE trigger R from database
    be_rs_db = [float(t.get("be_trigger_r",0)) for t in trades
                if float(t.get("be_trigger_r",0)) > 0]
    avg_be_r = _avg(be_rs_db) if be_rs_db else 0

    sample_lbl = _sample_label(total)

    # ── Classify exits (get_type called directly, no exit_map) ────────────
    tp_t      = [t for t in trades if get_type(t) == "TP"]
    partial_t = [t for t in trades if get_type(t) == "PARTIAL_TP"]
    pl_t      = [t for t in trades if get_type(t) == "PROFIT_LOCK"]
    be_t      = [t for t in trades if get_type(t) == "BREAK_EVEN"]
    sl_t      = [t for t in trades if get_type(t) == "ACTUAL_SL"]
    timeout_t = [t for t in trades if get_type(t) == "TIMEOUT"]
    struct_t  = [t for t in trades if get_type(t) == "STRUCTURE"]
    manual_t  = [t for t in trades if get_type(t) == "MANUAL"]
    unknown_t = [t for t in trades if get_type(t).startswith("UNKNOWN")]

    classified = (len(tp_t)+len(partial_t)+len(pl_t)+len(be_t)+
                  len(sl_t)+len(timeout_t)+len(struct_t)+
                  len(manual_t)+len(unknown_t))

    tp_rate = round(len(tp_t)/max(total,1)*100,1)
    pl_rate = round(len(pl_t)/max(total,1)*100,1)
    be_rate = round(len(be_t)/max(total,1)*100,1)
    sl_rate = round(len(sl_t)/max(total,1)*100,1)
    unk_rate= round(len(unknown_t)/max(total,1)*100,1)

    # ── 1. SAMPLE SIZE ────────────────────────────────────────────────────
    target = 50
    recs.append({
        "priority":"HIGH" if total < 30 else "MEDIUM" if total < 50 else "INFO",
        "emoji":"📊",
        "title":f"Sample: {total} trades — {sample_lbl}",
        "detail":(
            f"Progress toward 50-trade milestone: "
            f"{_bar(min(total,target),target)} {total}/{target}\n\n"
            f"Win rate: {win_rate}% | P/L: ${total_pl:+.2f} | "
            f"Profit factor: {pf if pf!=999.0 else '∞'}\n\n"
            + ("⚠️ Too few trades for any reliable conclusions. "
               "Do not change rules." if total < 30 else
               "📊 Preliminary analysis available. "
               "Patterns are visible but not validated." if total < 50 else
               "✅ Meaningful sample. Findings are more reliable.")
        ),
        "confidence":sample_lbl,
        "action":"Keep rules unchanged. Collect more data." if total < 30 else
                 "Review patterns. No changes until 50+ trades." if total < 50 else
                 "Act on high-confidence findings only.",
        "evidence":f"{total} completed trades",
    })

    # ── 2. EXIT QUALITY ───────────────────────────────────────────────────
    recs.append({
        "priority":"HIGH" if sl_rate > 50 else "MEDIUM" if tp_rate < 15 else "INFO",
        "emoji":"🎯",
        "title":f"Exit Quality: {tp_rate}% TP | {pl_rate}% Profit-lock | {sl_rate}% Real losses",
        "detail":(
            f"EXIT BREAKDOWN — mutually exclusive "
            f"({classified}/{total} classified):\n\n"
            f"  ✅ Take Profit:      {len(tp_t)}/{total} ({tp_rate}%)\n"
            f"  🔒 Profit-lock:     {len(pl_t)}/{total} ({pl_rate}%)\n"
            f"  ⚡ Break-Even:      {len(be_t)}/{total} ({be_rate}%)\n"
            f"  ❌ Actual losses:   {len(sl_t)}/{total} ({sl_rate}%)\n"
            f"  ⏱ Timeout:         {len(timeout_t)}/{total}\n"
            f"  🔄 Structure exit:  {len(struct_t)}/{total}\n"
            f"  ✋ Manual:          {len(manual_t)}/{total}\n"
            + (f"  ❓ Unclassified:   {len(unknown_t)}/{total} ({unk_rate}%)\n"
               if unknown_t else "") +
            f"\n  Planned avg R:R:    1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:     {avg_realized_r:+.2f}R\n"
            f"  Capture ratio:      {capture_ratio*100:.1f}% of planned target\n"
            + (f"\n  Avg MFE: +{avg_mfe:.2f}R | Avg MAE: {avg_mae:.2f}R"
               if mfe_vals else "")
        ),
        "confidence":sample_lbl,
        "action":"Investigate exit timing." if be_rate > 15 or capture_ratio < 0.3 else
                 "Exit management acceptable.",
        "evidence":f"TP:{len(tp_t)} PL:{len(pl_t)} BE:{len(be_t)} SL:{len(sl_t)}",
    })

    # ── 3. MFE/MAE ANALYSIS ───────────────────────────────────────────────
    if mfe_vals:
        # Can the market move enough to reach targets?
        trades_mfe_reached_tp = sum(1 for v in mfe_vals if v >= avg_plan_rr * 0.9)
        mfe_tp_rate = round(trades_mfe_reached_tp / len(mfe_vals) * 100, 1)

        recs.append({
            "priority":"MEDIUM" if capture_ratio < 0.3 else "INFO",
            "emoji":"📐",
            "title":f"MFE/MAE: avg peak +{avg_mfe:.2f}R | avg worst {avg_mae:.2f}R",
            "detail":(
                f"MFE = how far trades moved in your favor before closing\n"
                f"MAE = how far trades moved against you\n\n"
                f"  Average MFE:       +{avg_mfe:.2f}R\n"
                f"  Average MAE:       {avg_mae:.2f}R\n"
                f"  Average planned:   +{avg_plan_rr:.2f}R\n"
                f"  Average realized:  {avg_realized_r:+.2f}R\n"
                f"  Capture ratio:     {capture_ratio*100:.1f}%\n"
                f"  MFE reached TP zone: {mfe_tp_rate}% of trades\n\n"
                + (f"The market moved far enough ({avg_mfe:.2f}R avg peak) "
                   f"but only {capture_ratio*100:.1f}% of that was captured. "
                   f"This points to exit management cutting profits early."
                   if avg_mfe >= avg_plan_rr * 0.7 and capture_ratio < 0.4 else
                   f"Average peak movement ({avg_mfe:.2f}R) is below planned target "
                   f"({avg_plan_rr:.2f}R). The targets may be too ambitious for "
                   f"current market conditions OR entries are slightly late.")
            ),
            "confidence":sample_lbl,
            "action":"Collect more MFE/MAE data. Available after next 20 trades.",
            "evidence":f"{len(mfe_vals)} trades with MFE data",
        })

    # ── 4. BE ANALYSIS ────────────────────────────────────────────────────
    if len(be_t) > 0:
        be_detail = (
            f"{len(be_t)} trades moved in the right direction then closed at ~$0.\n\n"
        )
        if be_rs_db:
            be_detail += (
                f"BE trigger R data available ({len(be_rs_db)} trades):\n"
                f"  Average R at BE trigger: +{avg_be_r:.2f}R\n\n"
            )
        else:
            be_detail += (
                f"BE trigger R data not yet in database (older trades).\n"
                f"New trades will record exact R at which BE fires.\n\n"
            )
        be_detail += (
            f"The R-based BE (now at +1.25R) standardizes the trigger.\n"
            f"Whether this reduces premature BE exits depends on how price\n"
            f"behaves after +1.25R — the next 20-50 trades will show this.\n\n"
            f"DO NOT change BE trigger again until that data is collected."
        )

        recs.append({
            "priority":"MEDIUM",
            "emoji":"⚡",
            "title":f"Break-Even: {len(be_t)} trades closed at $0",
            "detail":be_detail,
            "confidence":sample_lbl,
            "action":"Monitor next 20-50 trades after R-based BE.",
            "evidence":f"{len(be_t)} BE exits | {len(be_rs_db)} with R data",
        })

    # ── 5. ENGINE HEALTH (execution only, not performance) ────────────────
    health  = 100
    issues  = []
    try:
        journal  = load_journal()
        opens    = [j for j in journal if j.get("action")=="OPEN"]
        low_conf = [j for j in opens if int(j.get("confidence",100)) < 60]
        if low_conf:
            health -= 20
            issues.append(f"{len(low_conf)} trades opened below 60% confidence "
                          f"(possible rule bypass)")
    except Exception:
        pass

    # Check for missing SL or TP in recent trades
    no_sl = [t for t in trades[-20:] if float(t.get("stop_loss",0)) == 0]
    if no_sl:
        health -= 15
        issues.append(f"{len(no_sl)} recent trades had no stop loss recorded")

    if unknown_t:
        health -= 10
        issues.append(f"{len(unknown_t)} trades have unclassified exit type")

    recs.append({
        "priority":"HIGH" if health < 70 else "INFO",
        "emoji":"🏥",
        "title":f"Engine Health: {health}/100",
        "detail":(
            f"ENGINE HEALTH = execution correctness only\n"
            f"(NOT strategy performance — engine can be 100/100 and still lose)\n\n"
            + ("\n".join(f"  ⚠️ {i}" for i in issues) if issues
               else "  ✅ No execution issues detected\n") +
            f"\n\nPerformance metrics are in the Expectancy card above."
        ),
        "confidence":"High",
        "action":"Fix execution issues first." if issues else
                 "Engine running correctly.",
        "evidence":f"Based on {total} trades and journal entries",
    })

    # ── 6. EXPECTANCY ─────────────────────────────────────────────────────
    recs.append({
        "priority":"HIGH" if expectancy < -0.50 else
                   "MEDIUM" if expectancy < 0 else "INFO",
        "emoji":"💰",
        "title":f"Expectancy: ${expectancy:+.2f}/trade | PF: {pf if pf!=999.0 else '∞'}",
        "detail":(
            f"ACTUAL MEASUREMENTS:\n\n"
            f"  Expectancy:         ${expectancy:+.2f} per trade\n"
            f"  Profit factor:      {pf if pf!=999.0 else '∞'}\n"
            f"  Win rate:           {win_rate}%\n"
            f"  Average winner:     ${avg_win:+.2f}\n"
            f"  Average loser:      ${avg_loss:+.2f}\n"
            f"  Planned avg R:R:    1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:     {avg_realized_r:+.2f}R\n"
            f"  Capture ratio:      {capture_ratio*100:.1f}% of planned\n"
            f"  Best trade:         ${float(max(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n"
            f"  Worst trade:        ${float(min(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n\n"
            + ("⚠️ Negative expectancy. No evidence currently justifies changing "
               "entry rules — investigate exit management first."
               if expectancy < 0 else
               "✅ Positive expectancy. Protect this edge by maintaining discipline.")
        ),
        "confidence":sample_lbl,
        "action":"Investigate exit management." if expectancy < 0 else
                 "Keep rules unchanged.",
        "evidence":f"{total} completed trades",
    })

    # ── 7. PATTERN DISCOVERY ─────────────────────────────────────────────
    btc_t  = [t for t in trades if t.get("symbol","")=="BTCUSD"]
    eth_t  = [t for t in trades if t.get("symbol","")=="ETHUSD"]
    buy_t  = [t for t in trades if t.get("side","")=="BUY"]
    sell_t = [t for t in trades if t.get("side","")=="SELL"]

    if btc_t and eth_t and abs(_wr(btc_t)-_wr(eth_t)) >= 20:
        better = "BTC" if _wr(btc_t) > _wr(eth_t) else "ETH"
        recs.append({
            "priority":"MEDIUM","emoji":"📈",
            "title":f"{better} outperforming — preliminary pattern",
            "detail":(
                f"BTC: {_wr(btc_t)}% WR ({len(btc_t)} trades)\n"
                f"ETH: {_wr(eth_t)}% WR ({len(eth_t)} trades)\n\n"
                f"Gap of {abs(_wr(btc_t)-_wr(eth_t)):.0f}% — visible but not validated.\n"
                f"Needs 50+ trades per symbol to confirm."
            ),
            "confidence":"Low",
            "action":"Watch but do not change rules yet.",
            "evidence":f"{len(btc_t)} BTC + {len(eth_t)} ETH",
        })

    if buy_t and sell_t and abs(_wr(buy_t)-_wr(sell_t)) >= 25:
        better = "BUY" if _wr(buy_t) > _wr(sell_t) else "SELL"
        recs.append({
            "priority":"MEDIUM","emoji":"🎯",
            "title":f"{better} trades outperforming — preliminary",
            "detail":(
                f"BUY: {_wr(buy_t)}% WR ({len(buy_t)} trades)\n"
                f"SELL: {_wr(sell_t)}% WR ({len(sell_t)} trades)"
            ),
            "confidence":"Low",
            "action":"No change yet. Needs 50+ trades to confirm.",
            "evidence":f"{len(buy_t)} buys + {len(sell_t)} sells",
        })

    # ── 8. SUMMARY ────────────────────────────────────────────────────────
    recs.append({
        "priority":"INFO","emoji":"🧠",
        "title":"Summary: What does the data say?",
        "detail":(
            f"BTC: {len(btc_t)}/{total} | ETH: {len(eth_t)}/{total}\n"
            f"BUY: {len(buy_t)}/{total} | SELL: {len(sell_t)}/{total}\n\n"
            f"No evidence currently justifies changing entry rules.\n\n"
            f"Priority investigation order:\n"
            f"  1. Exit management (BE trigger timing)\n"
            f"  2. Realized R vs planned R (capture ratio)\n"
            f"  3. MFE data — was movement available to reach targets?\n\n"
            f"Next milestone: {max(0,50-total)} more trades for stronger patterns."
        ),
        "confidence":"High",
        "action":"Collect data. Keep rules unchanged.",
        "evidence":f"{total}/{50} target trades",
    })

    return recs
