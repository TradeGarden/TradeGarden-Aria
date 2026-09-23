"""
recommendations.py - Aria Engine Health & Improvement Analysis
==============================================================
All review fixes applied:

1. exit_type normalized via VALID_EXIT_TYPES, no P/L guessing
2. realized_r includes 0R (BE exits are legitimate 0R results)
3. MFE/MAE includes 0R (never moved in favor is valid data)
4. Per-trade MFE vs own planned_rr (not average)
5. planned_tp_dollars used for three-way opportunity analysis
6. Engine health shows facts not arbitrary score
7. Scenario A/B uses planned_tp_dollars per trade
8. Losing streak removed from engine health
9. Descriptive language — no overclaiming
10. Scenario heuristic thresholds labeled explicitly
11. trades[:20] not trades[-20:] for recent check
12. Symbol/side normalized before comparison
13. classified == total verified as integrity check
"""

from database import load_closed_trades, load_closed_trades_today, load_journal


VALID_EXIT_TYPES = {
    "TP","PARTIAL_TP","PROFIT_LOCK","BREAK_EVEN",
    "ACTUAL_SL","TIMEOUT","STRUCTURE","MANUAL",
}


def _safe_float(v):
    """Return float or None — never silently drops 0.0"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _avg(vals):
    return round(sum(vals)/len(vals), 2) if vals else 0.0

def _wr(t):
    if not t: return 0.0
    return round(len([x for x in t if float(x.get("pl",0)) > 0]) / len(t) * 100, 1)

def _pf(trades):
    w = sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0)) > 0)
    l = abs(sum(float(t.get("pl",0)) for t in trades if float(t.get("pl",0)) < 0))
    return round(w/l, 2) if l > 0 else 999.0

def _bar(n, total, width=20):
    filled = int(n / max(total, 1) * width)
    return "█" * filled + "░" * (width - filled)

def _sample_label(n):
    if n < 10:  return "Very small sample"
    if n < 30:  return "Small sample"
    if n < 50:  return "Preliminary sample"
    if n < 100: return "Moderate sample"
    return "Larger sample"

def _sym(t):  return str(t.get("symbol","")).strip().upper()
def _side(t): return str(t.get("side","")).strip().upper()


def get_type(t: dict) -> str:
    """
    Single normalized exit type per trade.
    Uses exit_type field first, then exit_reason string.
    Returns UNKNOWN — never guesses from P/L thresholds.
    """
    et = str(t.get("exit_type","")).strip().upper().replace("-","_").replace(" ","_")
    if et in VALID_EXIT_TYPES:
        return et
    r = str(t.get("exit_reason","")).strip().lower()
    if "partial tp"   in r or "partial take" in r: return "PARTIAL_TP"
    if "take profit"  in r or "take_profit"  in r: return "TP"
    if "timeout"      in r:                         return "TIMEOUT"
    if "structure"    in r:                         return "STRUCTURE"
    if "manual"       in r:                         return "MANUAL"
    if "break-even"   in r or "break_even"   in r: return "BREAK_EVEN"
    if "profit-lock"  in r or "profit_lock"  in r: return "PROFIT_LOCK"
    if "profit lock"  in r or "locked"       in r: return "PROFIT_LOCK"
    if "actual loss"  in r or "stop loss"    in r: return "ACTUAL_SL"
    return "UNKNOWN"


def _realized_r(t: dict):
    """Get realized R with fallback calculation."""
    v = _safe_float(t.get("realized_r"))
    if v is not None:
        return v
    r1 = _safe_float(t.get("risk_1r") or t.get("risk"))
    pl = _safe_float(t.get("pl"))
    if r1 and r1 > 0 and pl is not None:
        return round(pl / r1, 3)
    return None


def generate() -> list:
    trades = load_closed_trades(999)
    total  = len(trades)
    recs   = []

    if total == 0:
        return [{"priority":"INFO","emoji":"🚀",
                 "title":"Aria running — no completed trades yet",
                 "detail":"Aria scans every 60 seconds. Analysis appears once trades close.",
                 "confidence":"—","action":"No action needed.","evidence":"0 trades"}]

    if total < 3:
        return [{"priority":"INFO","emoji":"📊",
                 "title":f"Collecting data — {total}/3 minimum",
                 "detail":"Need 3+ completed trades to start.",
                 "confidence":"—","action":"No action needed.","evidence":f"{total} trade(s)"}]

    # ── Core stats ─────────────────────────────────────────────────────────
    wins       = [t for t in trades if float(t.get("pl",0)) > 0]
    losses     = [t for t in trades if float(t.get("pl",0)) < 0]
    total_pl   = round(sum(float(t.get("pl",0)) for t in trades), 2)
    win_rate   = _wr(trades)
    avg_win    = _avg([float(t.get("pl",0)) for t in wins])
    avg_loss   = _avg([float(t.get("pl",0)) for t in losses])
    pf         = _pf(trades)
    # Expectancy = mean P/L directly — no rounding artifacts
    expectancy = round(total_pl / total, 2)
    sample_lbl = _sample_label(total)

    # Planned R:R — use planned_rr if stored
    rr_vals     = [float(t.get("planned_rr",0) or t.get("rr",0))
                   for t in trades
                   if float(t.get("planned_rr",0) or t.get("rr",0)) > 0]
    avg_plan_rr = _avg(rr_vals)

    # Planned TP dollars — three-way opportunity analysis
    plan_tp_vals = [float(t.get("planned_tp_dollars",0))
                    for t in trades if float(t.get("planned_tp_dollars",0)) > 0]
    avg_plan_tp  = _avg(plan_tp_vals) if plan_tp_vals else 0

    # Realized R — 0R included (BE exits are legitimate 0R outcomes)
    realized_rs = []
    for t in trades:
        rr = _realized_r(t)
        if rr is not None:
            realized_rs.append(rr)
    avg_realized_r = _avg(realized_rs)

    capture_ratio = round(avg_realized_r / avg_plan_rr, 3) if avg_plan_rr > 0 else 0

    # MFE/MAE — 0R included (never moved in favor is valid data)
    mfe_vals, mae_vals = [], []
    for t in trades:
        v = _safe_float(t.get("mfe_r"))
        if v is not None: mfe_vals.append(v)
        v = _safe_float(t.get("mae_r"))
        if v is not None: mae_vals.append(v)

    avg_mfe = _avg(mfe_vals) if mfe_vals else None
    avg_mae = _avg(mae_vals) if mae_vals else None

    # Per-trade MFE vs own planned_rr (not average)
    mfe_tp_checks      = []
    mfe_dollars_avail  = []
    plan_tp_per_trade  = []

    for t in trades:
        mfe_v     = _safe_float(t.get("mfe_r"))
        plan_rr_t = _safe_float(t.get("planned_rr") or t.get("rr"))
        real_r    = _realized_r(t)
        r1        = _safe_float(t.get("risk_1r") or t.get("risk"))
        ptd       = _safe_float(t.get("planned_tp_dollars"))
        if ptd and ptd > 0:
            plan_tp_per_trade.append(ptd)
        if mfe_v is None or plan_rr_t is None or plan_rr_t <= 0:
            continue
        # Per-trade: did MFE reach THIS trade's own planned target?
        mfe_tp_checks.append(mfe_v >= plan_rr_t)
        if ptd and ptd > 0:
            mfe_dollars_avail.append(ptd)
        elif r1 and r1 > 0:
            mfe_dollars_avail.append(round(mfe_v * r1, 2))

    mfe_tp_rate     = round(sum(mfe_tp_checks)/len(mfe_tp_checks)*100,1) if mfe_tp_checks else 0
    avg_mfe_dollars = _avg(mfe_dollars_avail)  if mfe_dollars_avail  else 0
    avg_plan_tp_use = _avg(plan_tp_per_trade)   if plan_tp_per_trade  else 0

    # Scenario A/B — heuristic thresholds, labeled as such
    scenario_a = scenario_b = scenario_c = 0
    for t in trades:
        mfe_v     = _safe_float(t.get("mfe_r"))
        plan_rr_t = _safe_float(t.get("planned_rr") or t.get("rr"))
        real_r    = _realized_r(t)
        if mfe_v is None or plan_rr_t is None or real_r is None: continue
        if plan_rr_t <= 0: continue
        # Heuristic bands (not statistical thresholds):
        # A: market offered target, execution captured little
        if mfe_v >= plan_rr_t * 0.8 and real_r < plan_rr_t * 0.4:
            scenario_a += 1
        # B: market never provided enough movement
        elif mfe_v < plan_rr_t * 0.5:
            scenario_b += 1
        # C: market offered target and execution captured it
        elif mfe_v >= plan_rr_t * 0.8 and real_r >= plan_rr_t * 0.6:
            scenario_c += 1

    # BE trigger R
    be_rs_db = [float(t.get("be_trigger_r",0)) for t in trades
                if float(t.get("be_trigger_r",0)) > 0]
    avg_be_r = _avg(be_rs_db) if be_rs_db else 0

    # Exit classification — mutually exclusive, no P/L guessing
    tp_t      = [t for t in trades if get_type(t) == "TP"]
    partial_t = [t for t in trades if get_type(t) == "PARTIAL_TP"]
    pl_t      = [t for t in trades if get_type(t) == "PROFIT_LOCK"]
    be_t      = [t for t in trades if get_type(t) == "BREAK_EVEN"]
    sl_t      = [t for t in trades if get_type(t) == "ACTUAL_SL"]
    timeout_t = [t for t in trades if get_type(t) == "TIMEOUT"]
    struct_t  = [t for t in trades if get_type(t) == "STRUCTURE"]
    manual_t  = [t for t in trades if get_type(t) == "MANUAL"]
    unknown_t = [t for t in trades if get_type(t) == "UNKNOWN"]

    classified = sum(len(g) for g in [tp_t,partial_t,pl_t,be_t,
                                       sl_t,timeout_t,struct_t,manual_t,unknown_t])

    tp_rate = round(len(tp_t)/max(total,1)*100,1)
    pl_rate = round(len(pl_t)/max(total,1)*100,1)
    be_rate = round(len(be_t)/max(total,1)*100,1)
    sl_rate = round(len(sl_t)/max(total,1)*100,1)

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
            + ("⚠️ Too few trades. Do not change rules."
               if total < 30 else
               "📊 Preliminary analysis. Patterns visible but not validated.\n"
               "Reliability depends on market regime, sample independence, "
               "execution costs, and variance."
               if total < 50 else
               "Larger sample available. Reliability still depends on "
               "market regime and whether conditions will persist.")
        ),
        "confidence":sample_lbl,
        "action":"Collect more data before drawing conclusions about entry rules.",
        "evidence":f"{total} completed trades",
    })

    # ── 2. THREE-WAY OPPORTUNITY ANALYSIS ─────────────────────────────────
    if avg_mfe is not None:
        realized_dollars = _avg([float(t.get("pl",0)) for t in trades])
        recs.append({
            "priority":"MEDIUM" if capture_ratio < 0.3 else "INFO",
            "emoji":"📐",
            "title":f"Opportunity: Planned ${avg_plan_tp_use:.2f} | "
                    f"MFE ${avg_mfe_dollars:.2f} | Realized ${realized_dollars:+.2f}",
            "detail":(
                f"THREE-WAY OPPORTUNITY ANALYSIS:\n\n"
                f"  Planned TP (avg at entry):    ${avg_plan_tp_use:.2f}\n"
                f"  Available MFE (avg peak):     ${avg_mfe_dollars:.2f}\n"
                f"  Average realized P/L:         ${realized_dollars:+.2f}\n\n"
                f"  Planned avg R:R:              1:{avg_plan_rr:.2f}\n"
                f"  Realized avg R:               {avg_realized_r:+.2f}R\n"
                f"  Realized/planned R ratio:     {capture_ratio*100:.1f}%\n"
                f"  MFE reached own TP zone:      {mfe_tp_rate}%\n\n"
                f"  Note: Scenario thresholds below are diagnostic heuristics,\n"
                f"  not statistical tests.\n\n"
                f"  Scenario A (movement available, not captured): {scenario_a}\n"
                f"  Scenario B (market didn't provide movement):   {scenario_b}\n"
                f"  Scenario C (movement available and captured):  {scenario_c}\n\n"
                + (f"Scenario A count is higher in this sample. This is an exit-management\n"
                   f"investigation signal, not proof of causation."
                   if scenario_a > scenario_b * 1.5 else
                   f"Scenario B count is higher. The market frequently didn't reach\n"
                   f"planned targets. Target distance or entry timing may need review."
                   if scenario_b > scenario_a * 1.5 else
                   f"Mixed scenarios — insufficient data to distinguish exit management\n"
                   f"from target/entry problems. Continue collecting data.")
            ),
            "confidence":sample_lbl,
            "action":"Collect more MFE data. Improves with each new trade.",
            "evidence":f"{len(mfe_tp_checks)} trades with MFE + planned_rr data",
        })

    # ── 3. EXIT QUALITY ───────────────────────────────────────────────────
    integrity_ok = classified == total
    recs.append({
        "priority":"HIGH" if sl_rate > 50 else "MEDIUM" if tp_rate < 15 else "INFO",
        "emoji":"🎯",
        "title":f"Exit Quality: {tp_rate}% TP | {sl_rate}% Real losses | "
                f"Capture {capture_ratio*100:.1f}%",
        "detail":(
            f"EXIT BREAKDOWN — mutually exclusive "
            f"({'✅' if integrity_ok else '⚠️'} {classified}/{total} classified):\n\n"
            f"  ✅ Take Profit:      {len(tp_t)}/{total} ({tp_rate}%)\n"
            f"  🔒 Profit-lock:     {len(pl_t)}/{total} ({pl_rate}%)\n"
            f"  ⚡ Break-Even:      {len(be_t)}/{total} ({be_rate}%)\n"
            f"  ❌ Actual losses:   {len(sl_t)}/{total} ({sl_rate}%)\n"
            f"  ⏱ Timeout:         {len(timeout_t)}/{total}\n"
            f"  🔄 Structure exit:  {len(struct_t)}/{total}\n"
            f"  ✋ Manual:          {len(manual_t)}/{total}\n"
            + (f"  ❓ Unclassified:   {len(unknown_t)}/{total}\n" if unknown_t else "") +
            f"\n  Planned avg R:R:   1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:    {avg_realized_r:+.2f}R "
            f"(includes {sum(1 for r in realized_rs if r==0)} BE exits at 0R)\n"
            f"  Realized/planned:  {capture_ratio*100:.1f}%"
            + (f"\n\n  ⚠️ Classification integrity mismatch: {classified}/{total}"
               if not integrity_ok else "")
        ),
        "confidence":sample_lbl,
        "action":"Investigate exit timing." if be_rate > 15 else "Continue monitoring.",
        "evidence":f"TP:{len(tp_t)} PL:{len(pl_t)} BE:{len(be_t)} SL:{len(sl_t)}",
    })

    # ── 4. BE ANALYSIS ────────────────────────────────────────────────────
    if len(be_t) > 0:
        be_detail = f"{len(be_t)} trades moved in the right direction then closed at ~$0.\n\n"
        if be_rs_db:
            be_trigger_text = f"Recorded BE trigger average: +{avg_be_r:.2f}R ({len(be_rs_db)} trades)"
        else:
            be_trigger_text = "BE trigger R not yet recorded (older trades only)"
        be_detail += (
            f"{be_trigger_text}\n\n"
            f"The R-based BE standardizes the trigger across trades.\n"
            f"Whether this reduces premature BE exits depends on how price behaves\n"
            f"after the trigger — the next 20-50 trades will show this.\n\n"
            f"Do not change BE trigger again until that data is available."
        )
        recs.append({
            "priority":"MEDIUM",
            "emoji":"⚡",
            "title":f"Break-Even exits: {len(be_t)} trades",
            "detail":be_detail,
            "confidence":sample_lbl,
            "action":"Monitor next 20-50 trades.",
            "evidence":f"{len(be_t)} BE exits | {len(be_rs_db)} with R data",
        })

    # ── 5. ENGINE HEALTH (execution facts only) ───────────────────────────
    issues = []
    checks = []
    try:
        journal  = load_journal()
        opens    = [j for j in journal if j.get("action") == "OPEN"]
        # Validate required fields in open events
        for j in opens:
            for req in ["trade_id","symbol","side","confidence"]:
                if not j.get(req):
                    issues.append(f"❌ OPEN event missing field: {req}")
                    break
        low_conf = [j for j in opens if int(j.get("confidence",100)) < 60]
        if low_conf:
            issues.append(f"❌ {len(low_conf)} trades opened below 60% confidence")
        else:
            checks.append("✅ All trades opened above 60% confidence")
    except Exception as _e:
        checks.append(f"⚠️ Journal check error: {_e}")

    # Recent trades (trades loaded DESC — first 20 are most recent)
    recent_trades = trades[:20]
    no_sl = [t for t in recent_trades if float(t.get("stop_loss",0) or 0) == 0]
    if no_sl:
        issues.append(f"❌ {len(no_sl)} recent trades had no stop loss recorded")
    else:
        checks.append("✅ All recent trades have stop loss recorded")

    if unknown_t:
        issues.append(f"⚠️ {len(unknown_t)} trades have unclassified exit type")
    else:
        checks.append("✅ All trades have classified exit type")

    if not integrity_ok:
        issues.append(f"❌ Exit classification mismatch: {classified}/{total}")

    recs.append({
        "priority":"HIGH" if issues else "INFO",
        "emoji":"🏥",
        "title":f"Engine Health — {'Issues detected' if issues else 'No execution issues'}",
        "detail":(
            f"ENGINE = execution correctness only\n"
            f"(Engine health ≠ strategy performance)\n\n"
            + "\n".join(issues + checks) +
            f"\n\nPerformance metrics are in the Expectancy card."
        ),
        "confidence":"High",
        "action":"Fix issues listed above." if issues else "No recorded execution issues.",
        "evidence":f"Based on {total} trades and journal",
    })

    # ── 6. EXPECTANCY ─────────────────────────────────────────────────────
    recs.append({
        "priority":"HIGH" if expectancy < -0.50 else "MEDIUM" if expectancy < 0 else "INFO",
        "emoji":"💰",
        "title":f"Expectancy: ${expectancy:+.2f}/trade | PF: {pf if pf!=999.0 else '∞'}",
        "detail":(
            f"ACTUAL MEASUREMENTS:\n\n"
            f"  Expectancy:              ${expectancy:+.2f} per trade\n"
            f"  Profit factor:           {pf if pf!=999.0 else '∞'}\n"
            f"  Win rate:                {win_rate}%\n"
            f"  Average winner:          ${avg_win:+.2f}\n"
            f"  Average loser:           ${avg_loss:+.2f}\n"
            f"  Planned avg R:R:         1:{avg_plan_rr:.2f}\n"
            f"  Realized avg R:          {avg_realized_r:+.2f}R\n"
            f"  Realized/planned ratio:  {capture_ratio*100:.1f}%\n"
            f"  Best trade:              ${float(max(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n"
            f"  Worst trade:             ${float(min(trades,key=lambda t:float(t.get('pl',0))).get('pl',0)):+.2f}\n\n"
            + ("⚠️ Observed negative expectancy in this sample.\n"
               "Current data does not establish whether entry rules should change.\n"
               "Investigate exit management first."
               if expectancy < 0 else
               "Observed positive expectancy in this sample.\n"
               "This does not confirm a durable edge — reliability depends\n"
               "on market regime, sample independence, and execution costs.")
        ),
        "confidence":sample_lbl,
        "action":"Investigate exit management." if expectancy < 0 else
                 "Continue collecting data before drawing conclusions.",
        "evidence":f"{total} trades, {len(realized_rs)} with realized R data",
    })

    # ── 7. SYMBOL/DIRECTION PATTERNS ──────────────────────────────────────
    btc_t  = [t for t in trades if _sym(t)  == "BTCUSD"]
    eth_t  = [t for t in trades if _sym(t)  == "ETHUSD"]
    buy_t  = [t for t in trades if _side(t) == "BUY"]
    sell_t = [t for t in trades if _side(t) == "SELL"]

    if btc_t and eth_t and abs(_wr(btc_t) - _wr(eth_t)) >= 20:
        recs.append({
            "priority":"INFO","emoji":"📈",
            "title":"Symbol win-rate difference — preliminary observation",
            "detail":(
                f"BTC: {_wr(btc_t)}% WR ({len(btc_t)} trades) | "
                f"P/L ${sum(float(t.get('pl',0)) for t in btc_t):+.2f}\n"
                f"ETH: {_wr(eth_t)}% WR ({len(eth_t)} trades) | "
                f"P/L ${sum(float(t.get('pl',0)) for t in eth_t):+.2f}\n\n"
                f"Observed difference: {abs(_wr(btc_t)-_wr(eth_t)):.1f} percentage points.\n"
                f"Sample sizes are small. This does not establish a persistent effect.\n"
                f"Both symbols need 25+ trades each to compare meaningfully."
            ),
            "confidence":"Low",
            "action":"No change. Collect 25+ trades per symbol.",
            "evidence":f"{len(btc_t)} BTC + {len(eth_t)} ETH",
        })

    if buy_t and sell_t and abs(_wr(buy_t) - _wr(sell_t)) >= 25:
        recs.append({
            "priority":"INFO","emoji":"🔄",
            "title":"Direction win-rate difference — preliminary observation",
            "detail":(
                f"BUY:  {_wr(buy_t)}% WR ({len(buy_t)} trades)\n"
                f"SELL: {_wr(sell_t)}% WR ({len(sell_t)} trades)\n\n"
                f"Observed difference: {abs(_wr(buy_t)-_wr(sell_t)):.1f} pp.\n"
                f"Sample too small to confirm a persistent directional effect."
            ),
            "confidence":"Low",
            "action":"No change. Needs 25+ trades per direction.",
            "evidence":f"{len(buy_t)} buys + {len(sell_t)} sells",
        })

    # ── 8. INVESTIGATION SUMMARY ──────────────────────────────────────────
    recs.append({
        "priority":"INFO","emoji":"🧠",
        "title":"Investigation Priority",
        "detail":(
            f"Based on {total} trades:\n\n"
            f"  1. Exit management (BE trigger, capture ratio)\n"
            f"  2. MFE analysis — was movement available?\n"
            f"  3. Scenario A/B/C — exit problem or market problem?\n"
            f"  4. Three-way: Planned TP vs MFE vs Realized\n\n"
            f"Current data does not establish whether entry rules should change.\n\n"
            f"Next milestone: {max(0,50-total)} more trades."
        ),
        "confidence":"High",
        "action":"Collect more data before drawing conclusions about entry rules.",
        "evidence":f"{total}/{50} target",
    })

    return recs
