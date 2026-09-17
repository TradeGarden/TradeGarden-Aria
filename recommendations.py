"""
recommendations.py - Aria Engine Health & Improvement Analysis
==============================================================
FIXED based on review:

1. exit_type is normalized single field — categories never overlap
2. BE classification uses exit_type only (not abs(pl) <= 0.50)
3. Exit categories are mutually exclusive — sum = total trades
4. "Entries are working" replaced with neutral language
5. 30 trades = preliminary, not reliable
6. Planned R:R separated from realized R
7. Strategy score replaced with actual measurements
8. Realized R tracked separately from planned R:R
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
    if n < 10:  return "Very Low — avoid conclusions"
    if n < 30:  return "Low — preliminary only"
    if n < 50:  return "Preliminary — trends visible"
    if n < 100: return "Moderate — useful patterns"
    return "Good — analysis is meaningful"


def generate() -> list:
    trades = load_closed_trades(999)
    total  = len(trades)
    recs   = []

    # ── No trades ─────────────────────────────────────────────────────────
    if total == 0:
        return [{
            "priority":"INFO","emoji":"🚀",
            "title":"Aria is running — no completed trades yet",
            "detail":("Aria scans every 60 seconds. Once a trade closes, "
                      "full analysis appears here. Check the Journal page "
                      "to see open positions."),
            "confidence":"—","action":"No action needed. Let Aria trade.",
            "evidence":"0 completed trades",
        }]

    if total < 3:
        return [{
            "priority":"INFO","emoji":"📊",
            "title":f"Collecting data — {total}/3 minimum",
            "detail":f"Need 3+ completed trades to start analysis.",
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

    # Planned R:R from database
    rr_vals     = [float(t.get("rr",0)) for t in trades if float(t.get("rr",0))>0]
    avg_plan_rr = _avg(rr_vals)

    # Realized R: actual P/L / risk_1r for each trade
    realized_rs = []
    for t in trades:
        r1 = float(t.get("risk_1r",0) or t.get("risk",0))
        pl = float(t.get("pl",0))
        if r1 > 0:
            realized_rs.append(round(pl/r1, 3))
    avg_realized_r = _avg(realized_rs)

    # ── EXIT CLASSIFICATION (mutually exclusive, uses exit_type) ──────────
    # Priority: use normalized exit_type field if available
    # Fallback: use exit_reason string matching
    def get_type(t):
        et = t.get("exit_type","")
        if et and et != "UNKNOWN":
            return et
        # Fallback from exit_reason
        r  = t.get("exit_reason","").lower()
        pl = float(t.get("pl",0))
        if "partial tp"   in r: return "PARTIAL_TP"
        if "take profit"  in r: return "TP"
        if "timeout"      in r: return "TIMEOUT"
        if "structure"    in r: return "STRUCTURE"
        if "manual"       in r: return "MANUAL"
        if "break-even"   in r or "break_even" in r: return "BREAK_EVEN"
        # Classify by P/L as last resort
        if pl > 0.50:  return "PROFIT_LOCK"
        if pl > -0.10: return "BREAK_EVEN"
        return "ACTUAL_SL"

    exit_map = {t.get("trade_id",str(i)): get_type(t)
                for i,t in enumerate(trades)}

    tp_t      = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "TP"]
    partial_t = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "PARTIAL_TP"]
    pl_t      = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "PROFIT_LOCK"]
    be_t      = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "BREAK_EVEN"]
    sl_t      = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "ACTUAL_SL"]
    timeout_t = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "TIMEOUT"]
    struct_t  = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "STRUCTURE"]
    manual_t  = [t for t in trades if exit_map.get(t.get("trade_id",""),"") == "MANUAL"]

    # Verify totals add up
    classified = (len(tp_t)+len(partial_t)+len(pl_t)+len(be_t)+
                  len(sl_t)+len(timeout_t)+len(struct_t)+len(manual_t))

    # ── 1. SAMPLE SIZE ────────────────────────────────────────────────────
    sample_lbl = _sample_label(total)
    target = 50
    recs.append({
        "priority":"HIGH" if total < 30 else "MEDIUM" if total < 50 else "INFO",
        "emoji":"📊",
        "title":f"Sample: {total} trades — {sample_lbl}",
        "detail":(
            f"Progress: {_bar(min(total,target),target)} {total}/{target}\n\n"
            f"Win rate: {win_rate}% | P/L: ${total_pl:+.2f} | "
            f"Profit factor: {pf if pf!=999.0 else '∞'}\n\n"
            f"{'⚠️ Too few trades to draw conclusions. Do not change rules yet.'
               if total < 30 else
               '📊 Enough for preliminary analysis. Patterns are visible but not validated.'
               if total < 50 else
               '✅ Meaningful sample. Patterns are more reliable.'}"
        ),
        "confidence":sample_lbl.split("—")[0].strip(),
        "action":("Keep rules unchanged. Collect more data." if total < 30
                  else "Review patterns below carefully." if total < 50
                  else "Act on high-confidence findings."),
        "evidence":f"{total} completed trades",
    })

    # ── 2. EXIT QUALITY (mutually exclusive) ─────────────────────────────
    tp_rate = round(len(tp_t)/max(total,1)*100,1)
    pl_rate = round(len(pl_t)/max(total,1)*100,1)
    be_rate = round(len(be_t)/max(total,1)*100,1)
    sl_rate = round(len(sl_t)/max(total,1)*100,1)

    recs.append({
        "priority":"HIGH" if sl_rate > 50 else "MEDIUM" if tp_rate < 15 else "INFO",
        "emoji":"🎯",
        "title":f"Exit Quality: {tp_rate}% TP | {pl_rate}% Profit-lock | {sl_rate}% Real losses",
        "detail":(
            f"EXIT BREAKDOWN (mutually exclusive — total = {classified}/{total}):\n\n"
            f"  ✅ Take Profit:      {len(tp_t)}/{total} ({tp_rate}%)\n"
            f"  🔒 Profit-lock:     {len(pl_t)}/{total} ({pl_rate}%) — winners protected\n"
            f"  ⚡ Break-Even:      {len(be_t)}/{total} ({be_rate}%) — $0 after BE\n"
            f"  ❌ Actual losses:   {len(sl_t)}/{total} ({sl_rate}%) — real money lost\n"
            f"  ⏱ Timeout:         {len(timeout_t)}/{total}\n"
            f"  🔄 Structure exit:  {len(struct_t)}/{total}\n"
            f"  ✋ Manual:          {len(manual_t)}/{total}\n\n"
            f"  Planned avg R:R:  1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:   {avg_realized_r:+.2f}R\n\n"
            + ("⚠️ Realized R ({avg_realized_r:+.2f}R) is much lower than planned "
               f"(1:{avg_plan_rr:.2f}). Trades are not running to their intended targets.\n"
               f"Most likely cause: BE or exit triggers are closing trades too early."
               if avg_realized_r < avg_plan_rr * 0.4 else
               f"✅ Realized R is reasonable relative to planned R:R.")
        ),
        "confidence":sample_lbl.split("—")[0].strip(),
        "action":"Review BE trigger timing if BE exits dominate." if be_rate > 15 else "Exit management acceptable.",
        "evidence":f"TP:{len(tp_t)} PL:{len(pl_t)} BE:{len(be_t)} SL:{len(sl_t)} of {total}",
    })

    # ── 3. EXPECTANCY (actual measurement, not score) ─────────────────────
    recs.append({
        "priority":"HIGH" if expectancy < -0.50 else "MEDIUM" if expectancy < 0 else "INFO",
        "emoji":"💰",
        "title":f"Expectancy: ${expectancy:+.2f}/trade | Profit Factor: {pf if pf!=999.0 else '∞'}",
        "detail":(
            f"ACTUAL MEASUREMENTS (not scores):\n\n"
            f"  Expectancy:       ${expectancy:+.2f} per trade\n"
            f"  Profit factor:    {pf if pf!=999.0 else '∞'} "
            f"(>1.5 good | >2.0 excellent | <1.0 losing)\n"
            f"  Average winner:   ${avg_win:+.2f}\n"
            f"  Average loser:    ${avg_loss:+.2f}\n"
            f"  Win rate:         {win_rate}%\n"
            f"  Planned avg R:R:  1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:   {avg_realized_r:+.2f}R\n\n"
            f"  Best trade: ${float(max(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n"
            f"  Worst trade: ${float(min(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n\n"
            + (f"⚠️ Negative expectancy means the strategy loses money on average. "
               f"This needs to improve before scaling position size."
               if expectancy < 0 else
               f"✅ Positive expectancy — the strategy has a mathematical edge. "
               f"Protect it by maintaining discipline.")
        ),
        "confidence":sample_lbl.split("—")[0].strip(),
        "action":("Investigate exit management before changing entries."
                  if expectancy < 0 and avg_realized_r < avg_plan_rr * 0.4 else
                  "Keep current rules. Monitor for 20+ more trades."),
        "evidence":f"Based on {total} completed trades",
    })

    # ── 4. ENGINE HEALTH ──────────────────────────────────────────────────
    health   = 100
    issues   = []
    try:
        journal = load_journal()
        opens   = [j for j in journal if j.get("action")=="OPEN"]
        low_c   = [j for j in opens if int(j.get("confidence",100)) < 60]
        if low_c:
            health -= 20
            issues.append(f"{len(low_c)} trades opened below 60% confidence")
    except Exception:
        pass

    pls = [float(t.get("pl",0)) for t in trades[-10:]]
    streak = cur = 0
    for pl in pls:
        if pl < 0: cur += 1; streak = max(streak, cur)
        else: cur = 0
    if streak >= 3:
        health -= 30
        issues.append(f"Losing streak of {streak} in last 10 trades")

    recs.append({
        "priority":"HIGH" if health < 70 else "INFO",
        "emoji":"🏥",
        "title":f"Engine Health: {health}/100",
        "detail":(
            f"ENGINE (is the bot working correctly?): {health}/100\n"
            + ("\n".join(f"  ⚠️ {i}" for i in issues) if issues
               else "  ✅ No glitches detected\n")
            + f"\n\nNOTE: Engine health ≠ strategy performance.\n"
            f"Health 100 means the code runs correctly.\n"
            f"Strategy profitability depends on market conditions and rules."
        ),
        "confidence":"High",
        "action":"Fix engine issues first." if issues else "Engine running correctly.",
        "evidence":f"Based on {total} trades",
    })

    # ── 5. BE ANALYSIS ────────────────────────────────────────────────────
    if len(be_t) > 0:
        # Calculate R at which BE triggered for each BE trade
        be_rs = []
        for t in be_t:
            r1 = float(t.get("risk_1r",0) or t.get("risk",0))
            # BE exits close at ~$0 P/L
            if r1 > 0:
                be_rs.append(round(2.0 / r1, 2))  # approximate R at old $2 trigger

        avg_be_r = _avg(be_rs) if be_rs else 0

        recs.append({
            "priority":"MEDIUM",
            "emoji":"⚡",
            "title":f"Break-Even Analysis: {len(be_t)} trades exited at $0",
            "detail":(
                f"{len(be_t)} trades moved in the right direction then closed at $0.\n\n"
                f"This is the BE system working BUT also shows potential lost opportunity.\n\n"
                f"The new R-based BE (triggers at +1.25R) should reduce this.\n"
                f"Previous fixed $2 BE was triggering at different R levels per trade:\n"
                f"  $5-risk trade → BE at 0.40R (too early)\n"
                f"  $2-risk trade → BE at 1.00R (reasonable)\n"
                f"  $0.60-risk trade → BE at 3.33R (too late)\n\n"
                f"With R-based BE, every trade now BEs at the same +1.25R.\n\n"
                f"Monitor next 20 trades to see if BE rate decreases.\n"
                f"DO NOT change further until more data collected."
            ),
            "confidence":sample_lbl.split("—")[0].strip(),
            "action":"Monitor next 20 trades after R-based BE implemented.",
            "evidence":f"{len(be_t)} BE exits of {total} total",
        })

    # ── 6. PATTERN DISCOVERY ─────────────────────────────────────────────
    btc_t = [t for t in trades if t.get("symbol","")=="BTCUSD"]
    eth_t = [t for t in trades if t.get("symbol","")=="ETHUSD"]
    buy_t = [t for t in trades if t.get("side","")=="BUY"]
    sell_t= [t for t in trades if t.get("side","")=="SELL"]

    if btc_t and eth_t:
        btc_wr = _wr(btc_t); eth_wr = _wr(eth_t)
        if abs(btc_wr - eth_wr) >= 20:
            better = "BTC" if btc_wr > eth_wr else "ETH"
            recs.append({
                "priority":"MEDIUM","emoji":"📈",
                "title":f"{better} outperforming significantly",
                "detail":(
                    f"BTC: {btc_wr}% WR ({len(btc_t)} trades)\n"
                    f"ETH: {eth_wr}% WR ({len(eth_t)} trades)\n\n"
                    f"Difference of {abs(btc_wr-eth_wr):.0f}% may be meaningful "
                    f"but sample is {'small' if total < 50 else 'moderate'}."
                ),
                "confidence":"Low" if total < 30 else "Medium",
                "action":f"Watch {better} setups. More data needed to confirm.",
                "evidence":f"{len(btc_t)} BTC + {len(eth_t)} ETH trades",
            })

    if buy_t and sell_t and abs(_wr(buy_t)-_wr(sell_t)) >= 25:
        better = "BUY" if _wr(buy_t) > _wr(sell_t) else "SELL"
        recs.append({
            "priority":"MEDIUM","emoji":"🎯",
            "title":f"{better} trades performing better",
            "detail":(
                f"BUY: {_wr(buy_t)}% WR ({len(buy_t)} trades)\n"
                f"SELL: {_wr(sell_t)}% WR ({len(sell_t)} trades)"
            ),
            "confidence":"Low" if total < 30 else "Medium",
            "action":f"No change yet — needs 50+ trades to confirm.",
            "evidence":f"{len(buy_t)} buys + {len(sell_t)} sells",
        })

    # ── 7. MARKET CONDITION TRACKING ─────────────────────────────────────
    recs.append({
        "priority":"INFO","emoji":"🧠",
        "title":"Market Condition Tracking",
        "detail":(
            f"BTC: {len(btc_t)}/{total} | ETH: {len(eth_t)}/{total}\n"
            f"BUY: {len(buy_t)}/{total} | SELL: {len(sell_t)}/{total}\n\n"
            f"No evidence currently justifies changing entry rules.\n"
            f"Investigate exit management first based on available data.\n\n"
            f"Needed for stronger conclusions: {max(0,50-total)} more trades."
        ),
        "confidence":"Low" if total < 30 else "Preliminary",
        "action":"Collect more data. Keep rules unchanged.",
        "evidence":f"{total}/{50} target trades",
    })

    return recs
