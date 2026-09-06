"""
main.py - Aria AI Trading Engine
==================================
Starts the FastAPI server and connects all modules.
Contains ZERO trading logic - that lives in the modules below.

On startup:
  - Auto-trading loop begins immediately (executor.start_auto_trading)
  - Scans BTC and ETH every 60 seconds
  - Opens trades automatically when ALL conditions are met
  - trade_manager.py runs as a separate Render background worker

Module map:
  scanner.py          Stage 1 - Fetch market data
  analyzer.py         Stage 2 - Calculate all indicators
  decision_engine.py  Stage 3 - Make one decision: BUY / SELL / WAIT
  executor.py         Stage 4 - Auto-trade with full rule enforcement
  trade_manager.py    Stage 5 - Background position monitoring
  journal.py          Stage 6 - Permanent trade storage
  reports.py          Stage 7 - Daily/weekly/monthly performance reports
  recommendations.py  Stage 8 - Self-analysis and improvement suggestions
  config.py           All system settings
"""

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import os

# ── Module imports ──
from database        import (
    setup_database, get_account, load_balance,
    get_open_positions, get_open_positions_count, load_position,
)
from scanner         import scan, fetch_current_price
from analyzer        import analyze
from decision_engine import decide
from executor        import (
    open_trade, close_trade,
    todays_trade_count, todays_loss_pct,
    start_auto_trading, stop_auto_trading, get_auto_status,
)
from journal         import load_recent
from reports         import daily_report, weekly_report, monthly_report
from recommendations import generate as get_recommendations
from config          import (
    VALID_SYMBOLS, TRADINGVIEW_SYMBOLS,
    DASHBOARD_REFRESH_SECONDS, MIN_CONFIDENCE,
    MAX_TRADES_PER_DAY, DAILY_LOSS_LIMIT_PCT,
    RISK_PER_TRADE_PCT,
)


# ══════════════════════════════════════════════
#  STARTUP - begin auto-trading when server launches
# ══════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        setup_database()
        print("[Aria] Database ready")
    except Exception as e:
        print(f"[Aria] DB setup error: {e} — continuing without DB")
    try:
        start_auto_trading()
        print("[Aria] Auto-trading started")
    except Exception as e:
        print(f"[Aria] Trading start error: {e}")
    yield
    try:
        stop_auto_trading()
    except Exception:
        pass


app = FastAPI(title="Aria AI Trading Engine", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


# ══════════════════════════════════════════════
#  SHARED CSS
# ══════════════════════════════════════════════

def base_css() -> str:
    return """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;background:#090909;color:#d0d0d0;min-height:100vh}
.topbar{background:#0f0f0f;border-bottom:1px solid #1a1a1a;padding:14px 24px;
        display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.topbar h1{color:#fff;font-size:17px;letter-spacing:1px}
.sym-btns{display:flex;gap:6px;flex-wrap:wrap}
.sym-btn{padding:6px 14px;border-radius:20px;border:1px solid #222;
         background:#141414;color:#666;text-decoration:none;font-size:12px}
.sym-btn.active,.sym-btn:hover{border-color:#2ecc71;color:#2ecc71;background:#0d2e1a}
.page{display:grid;grid-template-columns:1fr 360px;min-height:calc(100vh - 53px)}
.left{padding:20px;overflow-y:auto;border-right:1px solid #111}
.right{padding:20px;background:#0c0c0c;overflow-y:auto}
.card{background:#111;border:1px solid #1a1a1a;border-radius:10px;padding:16px;margin-bottom:12px}
.card-title{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:2px;margin-bottom:8px}
.decision-BUY {font-size:34px;font-weight:bold;color:#2ecc71;letter-spacing:3px}
.decision-SELL{font-size:34px;font-weight:bold;color:#e74c3c;letter-spacing:3px}
.decision-WAIT{font-size:34px;font-weight:bold;color:#f39c12;letter-spacing:3px}
.conf-bar-bg{background:#1a1a1a;border-radius:6px;height:8px;overflow:hidden;margin-top:6px}
.conf-bar-fill{height:100%;border-radius:6px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-top:8px}
.stat{background:#0d0d0d;border-radius:8px;padding:10px;text-align:center}
.stat .v{font-size:13px;font-weight:bold;color:#fff}
.stat .l{font-size:10px;color:#444;margin-top:3px;text-transform:uppercase}
.reason-list{list-style:none;padding:0;margin:4px 0 0}
.reason-list li{padding:6px 0;font-size:13px;color:#bbb;border-bottom:1px solid #161616;
                display:flex;gap:8px;align-items:flex-start}
.reason-list li:last-child{border-bottom:none}
.reason-list li::before{content:"•";color:#333;flex-shrink:0}
.tf-row{display:flex;justify-content:space-between;align-items:center;
        padding:7px 0;border-bottom:1px solid #141414;font-size:13px}
.tf-row:last-child{border-bottom:none}
.adv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-top:12px}
.adv-card{background:#0d0d0d;border:1px solid #161616;border-radius:10px;padding:14px}
.adv-card h4{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:2px;margin-bottom:8px}
.adv-card .explain{font-size:11px;color:#333;margin-bottom:8px;
                   border-left:2px solid #1e1e1e;padding-left:8px;font-style:italic}
.sec-label{font-size:10px;color:#333;text-transform:uppercase;letter-spacing:2px;
           margin:20px 0 8px;padding-top:16px;border-top:1px solid #111}
.pos-card{border-radius:10px;padding:14px;margin-bottom:12px}
.pos-buy {background:#0d2e1a;border:1px solid #1a5c2e}
.pos-sell{background:#2e0d0d;border:1px solid #5c1a1a}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.btn{display:inline-block;padding:9px 18px;border-radius:8px;
     border:1px solid #2a2a2a;background:#1a1a1a;color:#ccc;text-decoration:none;font-size:13px}
.btn-buy {background:#0d2e1a;border-color:#1a5c2e;color:#2ecc71}
.btn-sell{background:#2e0d0d;border-color:#5c1a1a;color:#e74c3c}
.btn-close{background:#2a1a00;border-color:#5c3d00;color:#f39c12}
.btn-danger{background:#1a0000;border-color:#3c0000;color:#e74c3c}
.scan-dot{display:inline-block;width:7px;height:7px;border-radius:50%;
          background:#2ecc71;margin-right:6px;animation:pulse 2s infinite}
.scan-dot.off{background:#e74c3c;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.lv-bar-bg{background:#161616;border-radius:3px;height:4px;margin-top:4px}
.trade-row{background:#0d0d0d;border:1px solid #161616;border-radius:8px;
           padding:12px;margin-bottom:8px;font-size:12px}
.wk-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin:10px 0}
.wk-stat{background:#0d0d0d;border-radius:8px;padding:12px;text-align:center}
.wk-stat .v{font-size:18px;font-weight:bold;color:#fff}
.wk-stat .l{font-size:10px;color:#444;margin-top:4px;text-transform:uppercase}
.rec-item{background:#0d0d0d;border:1px solid #161616;border-radius:10px;
          margin-bottom:8px;overflow:hidden}
.rec-header{padding:12px 16px;cursor:pointer;display:flex;justify-content:space-between;
            align-items:center;font-size:13px;color:#ccc}
.rec-body{padding:0 16px 14px;font-size:13px;color:#666;line-height:1.8;display:none}
.rec-body.open{display:block}
.rules-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
.rule-box{background:#0d0d0d;border-radius:8px;padding:12px;font-size:12px}
.rule-box h5{color:#888;font-size:10px;text-transform:uppercase;
             letter-spacing:1px;margin-bottom:8px}
.rule-row{display:flex;justify-content:space-between;padding:3px 0;
          border-bottom:1px solid #141414;color:#bbb}
.rule-row:last-child{border-bottom:none}
.aria-status{background:#0d1a0d;border:1px solid #1a3a1a;border-radius:10px;
             padding:12px 16px;margin-bottom:12px;font-size:12px}
@media(max-width:900px){
  .page{grid-template-columns:1fr}
  .right{border-top:1px solid #111}
}
</style>"""


# ══════════════════════════════════════════════
#  HTML HELPERS
# ══════════════════════════════════════════════

def _tc(trend): return {"Bullish":"#2ecc71","Bearish":"#e74c3c"}.get(trend,"#f39c12")
def _cc(c):     return "#2ecc71" if c>=65 else ("#f39c12" if c>=40 else "#e74c3c")

def _topbar(symbol: str = "", active: str = "") -> str:
    def cls(s): return "active" if symbol==s else ""
    def acls(s): return "active" if active==s else ""
    status = get_auto_status()
    dot_cls = "scan-dot" if status["running"] else "scan-dot off"
    dot_lbl = "Auto-trading ON" if status["running"] else "Auto-trading OFF"
    return f"""
<div class="topbar">
  <h1>🌱 Aria AI</h1>
  <div class="sym-btns">
    <a href="/analyze?symbol=BTCUSD" class="sym-btn {cls('BTCUSD')}">₿ BTC</a>
    <a href="/analyze?symbol=ETHUSD" class="sym-btn {cls('ETHUSD')}">Ξ ETH</a>
  </div>
  <div style="font-size:11px;color:#444">
    <span class="{dot_cls}"></span>{dot_lbl} ·
    Last scan: {status['last_scan']} · {datetime.utcnow().strftime('%H:%M UTC')}
  </div>
  <div class="sym-btns">
    <a href="/weekly"          class="sym-btn {acls('weekly')}">📊 Reports</a>
    <a href="/recommendations" class="sym-btn {acls('recs')}">💡 Recs</a>
    <a href="/journal"         class="sym-btn {acls('journal')}">📖 Journal</a>
    <a href="/intelligence"    class="sym-btn {acls('intel')}">🧠 Intel</a>
    <a href="/rules"           class="sym-btn {acls('rules')}">⚙️ Rules</a>
  </div>
</div>"""

def _tv_chart(symbol: str, height: int = 500) -> str:
    tv = TRADINGVIEW_SYMBOLS.get(symbol, "BITSTAMP:BTCUSD")
    return f"""
<div style="height:{height}px;background:#0f0f0f;border:1px solid #1a1a1a;
            border-radius:10px;overflow:hidden;margin-bottom:16px">
  <div class="tradingview-widget-container" style="height:100%">
    <div id="tv_chart" style="height:100%"></div>
    <script src="https://s3.tradingview.com/tv.js"></script>
    <script>
    new TradingView.widget({{
      autosize:true, symbol:"{tv}", interval:"D",
      timezone:"Etc/UTC", theme:"dark", style:"1", locale:"en",
      toolbar_bg:"#0f0f0f", hide_side_toolbar:false,
      allow_symbol_change:false, container_id:"tv_chart",
      studies:["EMA@tv-basicstudies","RSI@tv-basicstudies",
               "MACD@tv-basicstudies","Volume@tv-basicstudies"],
      overrides:{{
        "paneProperties.background":"#0a0a0a",
        "paneProperties.vertGridProperties.color":"#111",
        "paneProperties.horzGridProperties.color":"#111"
      }}
    }});
    </script>
  </div>
</div>"""

def _reasons(reasons):
    return "".join(f"<li>{r}</li>" for r in reasons)

def _tf_rows(frames):
    out = ""
    for f in frames:
        sc  = "#2ecc71" if f["decision"]=="BUY" else ("#e74c3c" if f["decision"]=="SELL" else "#444")
        tc2 = _tc(f["trend"]) if f["trend"] in ("Bullish","Bearish") else "#444"
        out += (f"<div class='tf-row'>"
                f"<span style='color:#555;width:45px'>{f['label']}</span>"
                f"<span style='color:{sc};font-weight:bold;width:48px'>{f['decision']}</span>"
                f"<span style='color:{tc2};font-size:12px'>{f['structure']}</span>"
                f"<span style='color:#333;font-size:11px'>RSI {f['rsi']}</span></div>")
    return out

def _patterns_html(patterns):
    if not patterns:
        return "<span style='color:#333;font-size:13px'>No significant pattern detected on any timeframe</span>"
    # Priority: 4H and Daily with High reliability first (most meaningful)
    priority = [p for p in patterns
                if p.get("timeframe") in ("4H","Daily")
                and p.get("reliability","") in ("High","Medium-High")]
    # Fallback: 1H with high reliability
    if not priority:
        priority = [p for p in patterns
                    if p.get("timeframe") == "1H"
                    and p.get("reliability","") in ("High","Medium-High")]
    # Last resort: any pattern
    display = priority if priority else patterns[:2]
    if not display:
        return "<span style='color:#333;font-size:13px'>No high-probability patterns detected</span>"
    out = ""
    for p in display:
        c  = "#2ecc71" if p["direction"]=="Bullish" else ("#e74c3c" if p["direction"]=="Bearish" else "#555")
        tf = p.get("timeframe","")
        # Timeframe badge color
        tf_c = "#f7931a" if tf=="Daily" else "#9b59b6" if tf=="4H" else "#3498db" if tf=="1H" else "#555"
        tf_badge = f"<span style='background:{tf_c}22;color:{tf_c};font-size:9px;padding:2px 6px;border-radius:3px;margin-left:6px'>{tf}</span>" if tf else ""
        out += (
            f"<div style='padding:8px;background:#0d0d0d;border-radius:6px;"
            f"border-left:3px solid {c};margin-bottom:6px'>"
            f"<div style='display:flex;align-items:center'>"
            f"<span style='color:{c};font-weight:bold;font-size:13px'>{p['name']}</span>"
            f"{tf_badge}</div>"
            f"<div style='font-size:11px;color:#444;margin-top:3px'>"
            f"{p['direction']} · {p['strength']} · Reliability: {p['reliability']}</div></div>"
        )
    return out

def _fvg_html(fvgs):
    if not fvgs:
        return "<span style='color:#333;font-size:13px'>No FVG detected</span>"
    out = ""
    for g in reversed(fvgs):
        c = "#2ecc71" if g["type"]=="Bullish" else "#e74c3c"
        out += (f"<div style='padding:6px 0;border-bottom:1px solid #141414'>"
                f"<span style='color:{c};font-weight:bold'>{g['type']} FVG</span>"
                f"<span style='color:#555;font-size:12px;margin-left:8px'>"
                f"${g['low']:,.2f} – ${g['high']:,.2f}</span></div>")
    return out

def _liq_html(liq):
    bsl = liq.get("bsl", 0)
    ssl = liq.get("ssl", 0)
    eq_highs = liq.get("equal_highs", [])
    eq_lows  = liq.get("equal_lows",  [])
    swept_bsl = liq.get("swept_bsl", False)
    swept_ssl = liq.get("swept_ssl", False)

    out = (f"<div style='padding:5px 0;font-size:13px'>"
           f"<span style='color:#2ecc71'>BSL</span>"
           f"<span style='color:#444;font-size:12px;margin-left:8px'>${bsl:,.2f}</span>"
           + (" <span style='color:#e74c3c;font-size:10px'>SWEPT</span>" if swept_bsl else "") +
           f"</div>"
           f"<div style='padding:5px 0;font-size:13px;border-bottom:1px solid #141414'>"
           f"<span style='color:#e74c3c'>SSL</span>"
           f"<span style='color:#444;font-size:12px;margin-left:8px'>${ssl:,.2f}</span>"
           + (" <span style='color:#2ecc71;font-size:10px'>SWEPT ✓</span>" if swept_ssl else "") +
           f"</div>")

    for h in eq_highs[:2]:
        out += (f"<div style='color:#f39c12;font-size:12px;padding:3px 0'>"
                f"Equal Highs @ ${h:,.2f} - BSL above</div>")
    for l in eq_lows[:2]:
        out += (f"<div style='color:#f39c12;font-size:12px;padding:3px 0'>"
                f"Equal Lows @ ${l:,.2f} - SSL below</div>")

    if swept_ssl:
        out += ("<div style='color:#2ecc71;font-size:11px;padding:4px 0'>"
                "SSL swept — stop hunt complete, potential bullish reversal</div>")
    if swept_bsl:
        out += ("<div style='color:#e74c3c;font-size:11px;padding:4px 0'>"
                "BSL swept — stop hunt complete, potential bearish reversal</div>")
    return out
def _levels_html(levels):
    def bar(s):
        w = {"Strong":100,"Moderate":60,"Weak":30}.get(s,40)
        c = {"Strong":"#2ecc71","Moderate":"#f39c12","Weak":"#e74c3c"}.get(s,"#555")
        return (f"<div class='lv-bar-bg'>"
                f"<div style='width:{w}%;height:100%;background:{c};border-radius:3px'></div></div>")
    out = ""
    # support/resistance are now lists - get nearest (first item)
    sup_list = levels.get("support", [])
    res_list = levels.get("resistance", [])
    sup_price = sup_list[0] if isinstance(sup_list, list) and sup_list else levels.get("nearest_support", 0)
    res_price = res_list[0] if isinstance(res_list, list) and res_list else levels.get("nearest_resistance", 0)

    for lbl, lvl, touches, strength, color in [
        ("Support",    sup_price, levels.get("support_touches",    1), levels.get("support_strength",    "Weak"), "#2ecc71"),
        ("Resistance", res_price, levels.get("resistance_touches", 1), levels.get("resistance_strength", "Weak"), "#e74c3c"),
    ]:
        price_str = f"${float(lvl):,.2f}" if lvl else "—"
        out += (f"<div style='padding:7px 0;border-bottom:1px solid #141414'>"
                f"<div style='display:flex;justify-content:space-between'>"
                f"<span style='color:{color};font-size:13px;font-weight:bold'>{lbl}</span>"
                f"<span style='color:#fff;font-size:13px'>{price_str}</span></div>"
                f"<div style='color:#333;font-size:11px;margin-top:2px'>"
                f"Tested {touches}× · {strength}</div>"
                f"{bar(strength)}</div>")
    return out

def _conf_breakdown(conf):
    maxes = {
        "Market Structure": 25, "EMA Alignment": 25,
        "EMA": 15, "RSI": 15,
        "Candle Strength": 20, "Candlestick": 20,
        "Volume": 15, "Location": 35, "Timeframes": 5,
    }
    breakdown = conf.get("breakdown", {}) if isinstance(conf, dict) else {}
    if not breakdown:
        return "<div style='color:#444;padding:8px;font-size:12px'>No breakdown</div>"
    out = ""
    for k, v in breakdown.items():
        mx  = maxes.get(k, 20)
        pct = int(v / mx * 100) if mx > 0 else 0
        bc  = "#2ecc71" if pct>=70 else ("#f39c12" if pct>=40 else "#222")
        out += (f"<div style='padding:5px 0;border-bottom:1px solid #141414'>"
                f"<div style='display:flex;justify-content:space-between;font-size:12px'>"
                f"<span style='color:#555'>{k}</span>"
                f"<span style='color:#ccc'>{v}/{mx}</span></div>"
                f"<div style='background:#161616;border-radius:3px;height:3px;margin-top:3px'>"
                f"<div style='width:{pct}%;height:100%;background:{bc};border-radius:3px'>"
                f"</div></div></div>")
    return out

def _report_block(r):
    if r.get("trades", 0) == 0:
        return (f"<div style='color:#444;padding:20px;text-align:center'>"
                f"{r.get('message','No completed trades yet.')}</div>")
    wr_c  = "#2ecc71" if r["win_rate"]>=55 else ("#f39c12" if r["win_rate"]>=40 else "#e74c3c")
    pl_c  = "#2ecc71" if r["total_pl"]>=0 else "#e74c3c"
    best  = r.get("best_trade",{})
    worst = r.get("worst_trade",{})

    # Exit reasons breakdown
    reasons_html = ""
    for reason, count in sorted(
            r.get("exit_reasons",{}).items(),
            key=lambda x: x[1], reverse=True):
        rc = ("#2ecc71" if "Profit" in reason or "Partial" in reason
              else "#e74c3c" if "Stop Loss" in reason or "Structure" in reason
              else "#888")
        pct = round(count / r["trades"] * 100)
        reasons_html += (
            f"<div style='display:flex;justify-content:space-between;"
            f"padding:4px 0;border-bottom:1px solid #141414;font-size:12px'>"
            f"<span style='color:{rc}'>{reason}</span>"
            f"<span style='color:#555'>{count}x ({pct}%)</span></div>"
        )

    out = (
        f"<div class='wk-grid'>"
        f"<div class='wk-stat'><div class='v'>{r['trades']}</div><div class='l'>Trades</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#2ecc71'>{r['wins']}</div><div class='l'>Wins</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#e74c3c'>{r['losses']}</div><div class='l'>Losses</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:{wr_c}'>{r['win_rate']}%</div><div class='l'>Win Rate</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#2ecc71'>${r['avg_win']}</div><div class='l'>Avg Win</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#e74c3c'>${r['avg_loss']}</div><div class='l'>Avg Loss</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:{pl_c}'>${r['total_pl']}</div><div class='l'>Total P/L</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#2ecc71'>${best.get('pl',0)}</div>"
        f"<div class='l'>Best ({best.get('symbol','')} {best.get('side','')})</div></div>"
        f"<div class='wk-stat'><div class='v' style='color:#e74c3c'>${worst.get('pl',0)}</div>"
        f"<div class='l'>Worst ({worst.get('symbol','')} {worst.get('side','')})</div></div>"
        f"</div>"
    )
    if reasons_html:
        out += (
            f"<div style='margin-top:12px'>"
            f"<div style='font-size:10px;color:#444;text-transform:uppercase;"
            f"letter-spacing:1px;margin-bottom:8px'>Exit Reasons</div>"
            f"{reasons_html}</div>"
        )
    return out

def _aria_status_card() -> str:
    s = get_auto_status()
    dot = "🟢" if s["running"] else "🔴"
    decision_div = (f"<div style='color:#888;font-size:11px;margin-top:3px'>{s['last_decision']}</div>"
                    if s['last_decision'] else "")
    return (f"<div class='aria-status'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<span style='color:#2ecc71;font-weight:bold'>{dot} Aria Auto-Trading</span>"
            f"<span style='color:#333;font-size:11px'>Paper Mode</span></div>"
            f"<div style='color:#444;font-size:11px;margin-top:6px'>"
            f"Last scan: {s['last_scan']}</div>"
            f"<div style='color:#555;font-size:11px;margin-top:3px;line-height:1.5'>"
            f"{s['last_action']}</div>"
            + decision_div
            + "</div>")


# ══════════════════════════════════════════════
#  MAIN DASHBOARD
# ══════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS:
        symbol = "BTCUSD"

    try:
        scan_data = scan(symbol)
    except Exception as e:
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#e74c3c;"
            f"font-family:Arial;padding:30px'>"
            f"<h2>Scan error: {e}</h2>"
            f"<a href='/' style='color:#444;display:block;margin-top:16px'>Retry</a>"
            f"</body></html>"
        )

    if not scan_data["candles"]:
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#e74c3c;"
            f"font-family:Arial;padding:30px'>"
            f"<h2>No candle data from Kraken. Will retry on next refresh.</h2>"
            f"<a href='/' style='color:#2ecc71;display:block;margin-top:16px'>Retry now</a>"
            f"</body></html>"
        )

    try:
        analysis = analyze(scan_data)
        decision = decide(analysis)
        account        = get_account()
        balance        = float(account['balance'])
        open_positions = get_open_positions()
        # Intelligence available at /intelligence page
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#e74c3c;"
            f"font-family:Arial;padding:30px;white-space:pre'>"
            f"<h2>Dashboard error:</h2>{err}"
            f"<br><a href='/' style='color:#2ecc71'>Retry</a>"
            f"</body></html>"
        )
    position       = open_positions[0] if open_positions else None
    price    = scan_data["price"]
    dec      = decision["decision"]
    conf     = decision["confidence"]["total"]
    levels   = decision["levels"]
    tc_color = _tc(decision["trend"])
    cc_color = _cc(conf)

    today_trades = todays_trade_count()
    today_loss   = todays_loss_pct(balance)
    risk_usd     = round(balance * RISK_PER_TRADE_PCT / 100, 2)

    # ── Open positions block (up to 2) ──
    pos_block = ""
    for pos in open_positions:
        p_entry = pos["entry_price"]
        p_size  = pos["size"]
        p_side  = pos["side"]
        p_sym   = pos["symbol"]
        p_pl    = (price-p_entry)*p_size if p_side=="BUY" else (p_entry-price)*p_size
        p_pl    = round(p_pl, 2)
        p_col   = "#2ecc71" if p_pl >= 0 else "#e74c3c"
        tid     = pos.get("trade_id","")
        pos_block += (
            f"<div class='pos-card pos-{p_side.lower()}'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<span style='font-weight:bold;color:#fff'>{p_side} {p_size} {p_sym[:3]}</span>"
            f"<span style='color:#444;font-size:11px'>#{tid}</span></div>"
            f"<div style='margin-top:5px;font-size:12px;color:#555'>"
            f"Entry ${p_entry:,.2f} · SL ${pos['stop_loss']:,.2f} · TP ${pos['take_profit']:,.2f}</div>"
            f"<div id='live-pl-{tid}' style='color:{p_col};font-size:18px;font-weight:bold;margin-top:6px'>"
            f"P/L: ${p_pl:,.2f}</div>"
            f"<a href='/close?symbol={p_sym}&trade_id={tid}' class='btn btn-close' "
            f"style='display:inline-block;margin-top:8px'>Close #{tid[:6]}</a></div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{DASHBOARD_REFRESH_SECONDS}">
<title>Aria - {symbol}</title>
{base_css()}
</head><body>
{_topbar(symbol)}
<div class="page">

<!-- ════ LEFT: CHART + FULL ANALYSIS ════ -->
<div class="left">
  {_tv_chart(symbol)}

  <div class="sec-label">Full Market Analysis</div>
  <div class="adv-grid">

    <div class="adv-card">
      <h4>Market Structure</h4>
      <div class="explain">HH/HL = Bullish. LH/LL = Bearish. Sequence shows the last 5 swing points.</div>
      <div style="font-size:15px;color:{tc_color};font-weight:bold;margin-bottom:10px">
        {analysis['ms']['sequence']}
      </div>
      <div class="stat-grid">
        <div class="stat"><div class="v">{analysis['ms']['structure']}</div><div class="l">Type</div></div>
        <div class="stat"><div class="v" style="color:{tc_color}">{analysis['ms']['trend']}</div><div class="l">Trend</div></div>
        <div class="stat"><div class="v">{analysis['ms']['strength_label']}</div><div class="l">Strength</div></div>
        <div class="stat"><div class="v">{analysis['ms']['strength_pct']}%</div><div class="l">Strength %</div></div>
        <div class="stat"><div class="v">${analysis['ms']['swing_high']:,.0f}</div><div class="l">Swing High</div></div>
        <div class="stat"><div class="v">${analysis['ms']['swing_low']:,.0f}</div><div class="l">Swing Low</div></div>
      </div>
      {"<div style='margin-top:8px;padding:7px;background:#0d2e1a;border-radius:6px;color:#2ecc71;font-size:12px'>✅ BOS - Break of Structure confirmed</div>" if analysis['ms']['bos'] else ""}
      {"<div style='margin-top:8px;padding:7px;background:#2e1a0d;border-radius:6px;color:#f39c12;font-size:12px'>⚠️ CHoCH - Change of Character detected</div>" if analysis['ms']['choch'] else ""}
    </div>

    <div class="adv-card">
      <h4>EMA · RSI · MACD · ATR</h4>
      <div class="explain">EMA trend · RSI momentum · MACD crossover · ATR volatility</div>
      <div class="stat-grid">
        <div class="stat"><div class="v">${analysis['ema20']:,.0f}</div><div class="l">EMA 20</div></div>
        <div class="stat"><div class="v">${analysis['ema50']:,.0f}</div><div class="l">EMA 50</div></div>
        <div class="stat"><div class="v" style="color:{'#e74c3c' if analysis['rsi_label']=='Overbought' else '#2ecc71' if analysis['rsi_label']=='Oversold' else '#888'}">{analysis['rsi14']}</div><div class="l">RSI · {analysis['rsi_label']}</div></div>
        <div class="stat"><div class="v">{analysis['macd_line']:+.0f}</div><div class="l">MACD</div></div>
        <div class="stat"><div class="v">{analysis['macd_signal']:+.0f}</div><div class="l">Signal</div></div>
        <div class="stat"><div class="v">${analysis['atr14']:,.0f}</div><div class="l">ATR 14</div></div>
      </div>
    </div>

    <div class="adv-card">
      <h4>Volume</h4>
      <div class="explain">Confirms whether buyers or sellers support the move.</div>
      <div class="stat-grid">
        <div class="stat"><div class="v">{analysis['vol']['current']:.1f}</div><div class="l">Current</div></div>
        <div class="stat"><div class="v">{analysis['vol']['avg20']:.1f}</div><div class="l">20-Avg</div></div>
        <div class="stat"><div class="v">x{analysis['vol']['relative']}</div><div class="l">Relative</div></div>
        <div class="stat"><div class="v" style="color:#2ecc71">{analysis['vol']['buy_pressure']}%</div><div class="l">Buy</div></div>
        <div class="stat"><div class="v" style="color:#e74c3c">{analysis['vol']['sell_pressure']}%</div><div class="l">Sell</div></div>
        <div class="stat"><div class="v">{analysis['vol']['label']}</div><div class="l">Label</div></div>
      </div>
    </div>

    <div class="adv-card">
      <h4>Candlestick Pattern</h4>
      <div class="explain">Entry confirmation. High-reliability patterns carry more weight.</div>
      {_patterns_html(analysis['patterns'])}
    </div>

    <div class="adv-card">
      <h4>Support &amp; Resistance</h4>
      <div class="explain">More touches = stronger level.</div>
      {_levels_html(analysis['levels'])}
    </div>

    <div class="adv-card">
      <h4>Fair Value Gap (FVG)</h4>
      <div class="explain">Price often returns to these areas before continuing.</div>
      {_fvg_html(analysis['fvgs'])}
    </div>

    <div class="adv-card">
      <h4>Liquidity</h4>
      <div class="explain">BSL = stops above highs. SSL = stops below lows.</div>
      {_liq_html(analysis['liq'])}
    </div>

    <div class="adv-card">
      <h4>Multi-Timeframe · {analysis['bias']}</h4>
      <div class="explain">Daily → 4H → 1H → 15m. 2+ aligned required to trade.</div>
      {_tf_rows(analysis['frames'])}
    </div>

    <div class="adv-card">
      <h4>Confidence Breakdown</h4>
      <div class="explain">5 indicators · max 100 pts · min {MIN_CONFIDENCE}% to trade · MACD removed</div>
      {_conf_breakdown(decision['confidence'])}
    </div>

    <div class="adv-card" style="grid-column:1/-1">
      <h4>AI Reasoning</h4>
      <div class="explain">Aria's full narrative analysis of the current setup.</div>
      <div style="font-size:13px;line-height:1.9;color:#bbb;white-space:pre-wrap;margin-top:8px">
        {decision['narrative']}
      </div>
    </div>

    <div class="adv-card" style="grid-column:1/-1">
      <h4>Intelligence Feed</h4>
      <div class="explain">News · Funding · Whales · Economic Events · Fear and Greed · Open Interest</div>
      <div style="margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <a href="/intelligence" style="display:inline-block;padding:12px 24px;background:#0d2e1a;
           border:1px solid #1a5c2e;border-radius:8px;color:#2ecc71;font-size:14px;font-weight:bold;
           text-decoration:none">🧠 Open Full Intelligence Page</a>
        <span style="font-size:12px;color:#333">
          Live news · Whale transactions · Funding rates · Exchange flows ·
          Economic calendar · Fear &amp; Greed · Open Interest · DXY
        </span>
      </div>
    </div>

  </div>
</div>

<!-- ════ RIGHT: DECISION PANEL ════ -->
<div class="right">

  {_aria_status_card()}

  <div class="card">
    <div class="card-title">Current Price · {scan_data['session']} Session</div>
    <div style="font-size:26px;font-weight:bold;color:#fff">${price:,.2f}</div>
  </div>

  <div class="card">
    <div class="card-title">Aria's Decision</div>
    <div class="decision-{dec}">{dec}</div>
  </div>

  <div class="card">
    <div class="card-title">Confidence · Min {MIN_CONFIDENCE}% required</div>
    <div style="font-size:26px;font-weight:bold;color:{cc_color}">{conf}%</div>
    <div class="conf-bar-bg">
      <div class="conf-bar-fill" style="width:{conf}%;background:{cc_color}"></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Trend &amp; Structure</div>
    <div style="color:{tc_color};font-size:16px;font-weight:bold">{decision['trend']}</div>
    <div style="color:{tc_color};font-size:13px;opacity:.7;margin-top:3px">
      {analysis['ms']['sequence']}
    </div>
    <div style="color:#333;font-size:11px;margin-top:3px">
      {analysis['ms']['strength_label']} · {analysis['ms']['strength_pct']}%
    </div>
  </div>

  <div class="card">
    <div class="card-title">Reason</div>
    <ul class="reason-list">{_reasons(decision['reasons'])}</ul>
  </div>

  <div class="card">
    <div class="card-title">Trade Levels</div>
    <div class="stat-grid">
      <div class="stat"><div class="v">${price:,.2f}</div><div class="l">Entry</div></div>
      <div class="stat"><div class="v" style="color:#e74c3c">${levels['stop_loss']:,.2f}</div><div class="l">Stop Loss</div></div>
      <div class="stat"><div class="v" style="color:#2ecc71">${levels['take_profit']:,.2f}</div><div class="l">Take Profit</div></div>
      <div class="stat"><div class="v">1:{levels['rr']}</div><div class="l">R/R</div></div>
      <div class="stat"><div class="v">${risk_usd:.2f}</div><div class="l">Risk (1%)</div></div>
      <div class="stat"><div class="v">${balance:,.2f}</div><div class="l">Balance</div></div>
      <div class="stat"><div class="v" id="live-equity" style="color:#2ecc71">${float(account['equity']):,.2f}</div><div class="l">Equity</div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">Today</div>
    <div class="stat-grid">
      <div class="stat">
        <div class="v">{today_trades} / {MAX_TRADES_PER_DAY}</div>
        <div class="l">Trades</div>
      </div>
      <div class="stat">
        <div class="v" style="color:{'#e74c3c' if today_loss>=DAILY_LOSS_LIMIT_PCT else '#888'}">
          {today_loss:.1f}% / {DAILY_LOSS_LIMIT_PCT}%
        </div>
        <div class="l">Loss Limit</div>
      </div>
      <div class="stat">
        <div class="v" style="color:{'#2ecc71' if len(open_positions)>0 else '#444'}">
          {len(open_positions)} / 2
        </div>
        <div class="l">Open Trades</div>
      </div>
    </div>
  </div>

  {pos_block}

  <div id="total-floating-pl" style="font-size:13px;color:#888;text-align:center;padding:4px 0;display:{'none' if not open_positions else 'block'}">
    Calculating total P/L...
  </div>

  <div class="card">
    <div class="card-title">Manual Override</div>
    <div style="font-size:11px;color:#333;margin-bottom:8px">
      Aria trades automatically. Use only to manually override.
    </div>
    <div class="btn-row">
      <a href="/execute?symbol={symbol}&side=BUY"  class="btn btn-buy">Force BUY</a>
      <a href="/execute?symbol={symbol}&side=SELL" class="btn btn-sell">Force SELL</a>
    </div>
  </div>

</div>
</div>
<script>
function updateEquity() {{
  fetch('/api/equity').then(r=>r.json()).then(data=>{{
    // Update equity display
    var eq = document.getElementById('live-equity');
    if(eq) {{
      eq.textContent = '$' + data.equity.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}});
      eq.style.color = data.equity >= data.balance ? '#2ecc71' : '#e74c3c';
    }}
    // Update each position P/L individually
    if(data.positions) {{
      data.positions.forEach(function(pos) {{
        var el = document.getElementById('live-pl-' + pos.trade_id);
        if(el) {{
          el.textContent = 'P/L: $' + pos.pl.toFixed(2);
          el.style.color = pos.pl >= 0 ? '#2ecc71' : '#e74c3c';
        }}
      }});
    }}
    // Update total floating P/L if shown
    var totalPl = document.getElementById('total-floating-pl');
    if(totalPl) {{
      totalPl.textContent = 'Floating: $' + data.floating_pl.toFixed(2);
      totalPl.style.color = data.floating_pl >= 0 ? '#2ecc71' : '#e74c3c';
    }}
  }}).catch(function(){{}});
}}
setInterval(updateEquity, 5000);
updateEquity();
</script>
</body></html>"""
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  REPORTS
# ══════════════════════════════════════════════

@app.get("/weekly", response_class=HTMLResponse)
async def reports_page():
    from reports import full_stats
    from database import get_open_positions as _get_pos
    from scanner  import fetch_current_price as _get_price

    d = daily_report(); w = weekly_report()
    m = monthly_report(); at = full_stats()

    # Build live open positions
    open_pos = _get_pos()
    pos_html = ""
    for pos in open_pos:
        try:
            cur = _get_price(pos["symbol"])
        except Exception:
            cur = pos["entry_price"]
        fl   = round(((cur - pos["entry_price"]) * pos["size"]
                      if pos["side"] == "BUY"
                      else (pos["entry_price"] - cur) * pos["size"]), 2)
        pl_c = "#2ecc71" if fl >= 0 else "#e74c3c"
        be   = " · ✅ RISK FREE" if pos.get("be_moved") else ""
        lk   = " · 🔒 $2.50 LOCKED" if pos.get("profit_locked") else ""
        ts   = str(pos.get("opened_at",""))[:16].replace("T"," ")
        pos_html += (
            f"<div style='background:#0d0d0d;border:1px solid #1a1a1a;"
            f"border-left:3px solid {pl_c};border-radius:8px;"
            f"padding:14px;margin-bottom:10px'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-bottom:8px'>"
            f"<span style='font-weight:bold;color:#fff;font-size:15px'>"
            f"{pos['side']} {pos['symbol']}</span>"
            f"<span style='color:{pl_c};font-size:20px;font-weight:bold'>"
            f"${fl:+,.2f}</span></div>"
            f"<div style='font-size:12px;color:#555;line-height:2'>"
            f"Entry: ${pos['entry_price']:,.2f} &nbsp;|&nbsp; "
            f"SL: <span style='color:#e74c3c'>${pos['stop_loss']:,.2f}</span>"
            f" &nbsp;|&nbsp; "
            f"TP: <span style='color:#2ecc71'>${pos['take_profit']:,.2f}</span><br>"
            f"Risk: ${pos.get('risk_amount',0):.2f} &nbsp;|&nbsp; "
            f"R:R 1:{pos.get('rr',0)} &nbsp;|&nbsp; "
            f"Opened: {ts}"
            f"<span style='color:#2ecc71'>{be}</span>"
            f"<span style='color:#9b59b6'>{lk}</span>"
            f"</div></div>"
        )
    if not pos_html:
        pos_html = ("<div style='color:#444;padding:16px;font-size:13px'>"
                    "No open positions right now.</div>")

    html = (
        f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
        f"<meta http-equiv='refresh' content='30'>"
        f"<title>Aria Reports</title>{base_css()}</head><body>"
        f"{_topbar('','weekly')}"
        f"<div style='max-width:960px;margin:20px auto;padding:0 16px'>"
        f"<div style='font-size:10px;color:#444;text-transform:uppercase;"
        f"letter-spacing:2px;margin-bottom:10px'>Live Open Positions</div>"
        f"{pos_html}"
        f"<div style='font-size:10px;color:#444;text-transform:uppercase;"
        f"letter-spacing:2px;margin:20px 0 10px;padding-top:10px;"
        f"border-top:1px solid #111'>Completed Trades</div>"
        f"<div class='card'><div class='card-title'>Daily</div>{_report_block(d)}</div>"
        f"<div class='card'><div class='card-title'>Weekly</div>{_report_block(w)}</div>"
        f"<div class='card'><div class='card-title'>Monthly</div>{_report_block(m)}</div>"
        f"<div class='card'><div class='card-title'>All Time</div>{_report_block(at)}</div>"
        f"</div></body></html>"
    )
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  RECOMMENDATIONS
# ══════════════════════════════════════════════

@app.get("/recommendations", response_class=HTMLResponse)
async def recs_page():
    recs = get_recommendations()

    priority_colors = {
        "HIGH":   "#e74c3c",
        "MEDIUM": "#f39c12",
        "INFO":   "#2ecc71",
        "LOW":    "#3498db",
    }
    priority_order = {"HIGH":0,"MEDIUM":1,"INFO":2,"LOW":3}
    recs_sorted = sorted(recs, key=lambda r: priority_order.get(r.get("priority","INFO"),2))

    cards = ""
    for r in recs_sorted:
        pc    = priority_colors.get(r.get("priority","INFO"), "#888")
        emoji = r.get("emoji","📊")
        title = r.get("title","")
        detail= r.get("detail","").replace("\n","<br>")
        conf  = r.get("confidence","—")
        action= r.get("action","")
        evid  = r.get("evidence","")
        pri   = r.get("priority","INFO")
        cards += (
            f'<div style="background:#111;border:1px solid #1a1a1a;'
            f'border-left:4px solid {pc};border-radius:8px;'
            f'padding:16px;margin-bottom:12px">' +
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:flex-start;margin-bottom:10px">' +
            f'<div style="font-size:15px;color:#fff;font-weight:bold">'
            f'{emoji} {title}</div>' +
            f'<span style="background:{pc}22;color:{pc};font-size:10px;'
            f'padding:3px 8px;border-radius:12px;white-space:nowrap;margin-left:8px">'
            f'{pri}</span></div>' +
            f'<div style="font-size:13px;color:#888;line-height:1.8;margin-bottom:10px">'
            f'{detail}</div>' +
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;'
            f'gap:8px;font-size:11px">' +
            f'<div style="background:#0d0d0d;border-radius:6px;padding:8px">' +
            f'<div style="color:#444;margin-bottom:2px">CONFIDENCE</div>' +
            f'<div style="color:#fff">{conf}</div></div>' +
            f'<div style="background:#0d0d0d;border-radius:6px;padding:8px">' +
            f'<div style="color:#444;margin-bottom:2px">ACTION</div>' +
            f'<div style="color:#2ecc71">{action[:60]}</div></div>' +
            f'<div style="background:#0d0d0d;border-radius:6px;padding:8px">' +
            f'<div style="color:#444;margin-bottom:2px">EVIDENCE</div>' +
            f'<div style="color:#555">{evid}</div></div>' +
            f'</div></div>'
        )

    html = (
        f'<!DOCTYPE html><html><head><meta charset="UTF-8">' +
        f'<title>Aria · Engine Analysis</title>{base_css()}</head><body>' +
        _topbar("","recs") +
        f'<div style="max-width:960px;margin:20px auto;padding:0 16px">' +
        f'<div style="font-size:11px;color:#333;margin-bottom:16px">' +
        f'Aria Engine Health & Improvement Analysis · '
        f'Priority: 🔴 High → 🟠 Medium → 🟢 Info</div>' +
        cards +
        f'</div></body></html>'
    )
    return HTMLResponse(html)

@app.get("/journal", response_class=HTMLResponse)
async def journal_page():
    all_entries = load_recent(300)
    trades = [t for t in all_entries
              if t.get("action","") in
              ("OPEN","CLOSE","PARTIAL_TP","SL_MOVED_BE","PROFIT_LOCKED")][:100]
    rows = ""
    for t in trades:
        action = t.get("action","")
        sym    = t.get("symbol","")
        side2  = t.get("side","")
        tid    = t.get("trade_id","")

        # Clean timestamp
        raw_ts = t.get("closed_at", t.get("opened_at", t.get("timestamp","")))
        try:
            from datetime import datetime as _dt
            ts_clean = _dt.fromisoformat(str(raw_ts).replace("T"," ")[:19])
            date = ts_clean.strftime("%Y-%m-%d %H:%M")
        except Exception:
            date = str(raw_ts)[:16].replace("T"," ")

        # Action color and label
        ac = {"OPEN":"#2ecc71","CLOSE":"#e74c3c",
              "PARTIAL_TP":"#f39c12","SL_MOVED_BE":"#3498db",
              "PROFIT_LOCKED":"#9b59b6"}.get(action,"#555")

        # Build detail lines
        details = ""

        if action == "OPEN":
            entry = t.get("entry",0)
            sl    = t.get("stop_loss",0)
            tp    = t.get("take_profit",0)
            rr    = t.get("rr",0)
            risk  = t.get("risk_1r", t.get("risk",0))
            conf  = t.get("confidence",0)
            trend = t.get("trend","")
            seq   = t.get("sequence","")
            size_lbl = t.get("size_label","")
            details = (
                f"<div style='color:#888;font-size:12px;line-height:1.9'>"
                f"Entry: <b style='color:#fff'>${entry:,.2f}</b> &nbsp;|&nbsp; "
                f"Stop Loss: <b style='color:#e74c3c'>${sl:,.2f}</b> &nbsp;|&nbsp; "
                f"Take Profit: <b style='color:#2ecc71'>${tp:,.2f}</b><br>"
                f"R:R: <b style='color:#fff'>1:{rr}</b> &nbsp;|&nbsp; "
                f"Risk: <b style='color:#fff'>${risk:.2f}</b> &nbsp;|&nbsp; "
                f"Confidence: <b style='color:#fff'>{conf}%</b><br>"
                f"Structure: <b style='color:#2ecc71'>{trend} {seq}</b> &nbsp;|&nbsp; "
                f"{size_lbl}"
                f"</div>"
            )

        elif action in ("CLOSE","PARTIAL_TP"):
            entry  = t.get("entry",0)
            exit_p = t.get("exit",0)
            pl     = float(t.get("pl",0))
            rmult  = t.get("r_multiple",0)
            dur    = t.get("duration","")
            reason = t.get("exit_reason","")
            pl_c   = "#2ecc71" if pl >= 0 else "#e74c3c"
            label  = "50% closed" if action=="PARTIAL_TP" else "Full close"
            details = (
                f"<div style='color:#888;font-size:12px;line-height:1.9'>"
                f"Entry: <b style='color:#fff'>${entry:,.2f}</b> &nbsp;→&nbsp; "
                f"Exit: <b style='color:#fff'>${exit_p:,.2f}</b><br>"
                f"P/L: <b style='color:{pl_c};font-size:14px'>${pl:+,.2f}</b>"
                f"{'&nbsp;|&nbsp;<b style="color:'+pl_c+'">'+str(rmult)+'R</b>' if rmult else ''}"
                f" &nbsp;|&nbsp; {label} &nbsp;|&nbsp; Duration: {dur}<br>"
                f"<span style='color:#444'>{reason}</span>"
                f"</div>"
            )

        elif action == "SL_MOVED_BE":
            new_sl   = t.get("new_sl",0)
            profit_at= t.get("profit_at",0) or t.get("r_at_move",0)
            details = (
                f"<div style='color:#888;font-size:12px;line-height:1.9'>"
                f"Stop Loss moved to Break-Even: "
                f"<b style='color:#3498db'>${new_sl:,.2f}</b><br>"
                f"Profit when triggered: "
                f"<b style='color:#2ecc71'>${float(profit_at):.2f}</b> — "
                f"Trade is now <b style='color:#2ecc71'>RISK FREE</b>"
                f"</div>"
            )

        elif action == "PROFIT_LOCKED":
            new_sl    = t.get("new_sl",0)
            locked    = t.get("locked_usd",0)
            details = (
                f"<div style='color:#888;font-size:12px;line-height:1.9'>"
                f"Profit locked — SL moved to "
                f"<b style='color:#9b59b6'>${new_sl:,.2f}</b><br>"
                f"Minimum guaranteed profit: "
                f"<b style='color:#2ecc71'>${float(locked):.2f}</b>"
                f"</div>"
            )

        rows += (
            f"<div style='background:#111;border:1px solid #1a1a1a;border-radius:8px;"
            f"padding:14px;margin-bottom:10px'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"margin-bottom:8px'>"
            f"<div>"
            f"<span style='font-weight:bold;color:#fff;font-size:14px'>{sym} {side2}</span>"
            f"&nbsp;<span style='color:{ac};font-size:11px;font-weight:bold'>"
            f"[{action}]</span>"
            f"&nbsp;<span style='color:#333;font-size:11px'>#{tid[:8]}</span>"
            f"</div>"
            f"<span style='color:#444;font-size:11px'>{date}</span>"
            f"</div>"
            f"{details}"
            f"</div>"
        )

    if not rows:
        rows = ("<div style='color:#444;padding:40px;text-align:center'>"
                "No trades yet. Aria is scanning every 60 seconds.</div>")

    html = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            f"<title>Aria Journal</title>{base_css()}</head><body>"
            f"{_topbar('','journal')}"
            f"<div style='max-width:960px;margin:20px auto;padding:0 16px'>"
            f"<div style='font-size:11px;color:#333;margin-bottom:12px'>"
            f"Showing OPEN · CLOSE · PARTIAL_TP · SL_MOVED_BE · PROFIT_LOCKED"
            f"</div>"
            f"{rows}</div></body></html>")
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  RULES PAGE
# ══════════════════════════════════════════════

@app.get("/rules", response_class=HTMLResponse)
async def rules_page():
    html = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            f"<title>Aria · Rules</title>{base_css()}</head><body>"
            f"{_topbar('', 'rules')}"
            f"<div style='max-width:960px;margin:24px auto;padding:0 20px'>"
            f"<div class='card'>"
            f"<div class='card-title'>Current Trading Rules - config.py</div>"
            f"<div style='font-size:12px;color:#333;margin-bottom:12px'>"
            f"These rules are enforced on every trade. Aria never breaks them. "
            f"Change them in config.py - Aria applies them automatically.</div>"
            f"<div class='rules-grid'>"
            f"<div class='rule-box'><h5>Risk Management</h5>"
            f"<div class='rule-row'><span>Risk per trade</span><span style='color:#fff'>{RISK_PER_TRADE_PCT}%</span></div>"
            f"<div class='rule-row'><span>Daily loss limit</span><span style='color:#fff'>{DAILY_LOSS_LIMIT_PCT}%</span></div>"
            f"<div class='rule-row'><span>Min Risk/Reward</span><span style='color:#fff'>1:2</span></div>"
            f"<div class='rule-row'><span>Max open positions</span><span style='color:#fff'>1</span></div></div>"
            f"<div class='rule-box'><h5>Trade Frequency</h5>"
            f"<div class='rule-row'><span>Min trades/day</span><span style='color:#fff'>0</span></div>"
            f"<div class='rule-row'><span>Target trades/day</span><span style='color:#fff'>2–4</span></div>"
            f"<div class='rule-row'><span>Max trades/day</span><span style='color:#fff'>{MAX_TRADES_PER_DAY}</span></div>"
            f"<div class='rule-row'><span>Min confidence</span><span style='color:#fff'>{MIN_CONFIDENCE}%</span></div></div>"
            f"<div class='rule-box'><h5>BUY - All required</h5>"
            f"<div class='rule-row'><span>Structure</span><span style='color:#2ecc71'>HH / HL</span></div>"
            f"<div class='rule-row'><span>EMA</span><span style='color:#2ecc71'>EMA20 above EMA50</span></div>"
            f"<div class='rule-row'><span>Volume</span><span style='color:#2ecc71'>Buyers confirmed</span></div>"
            f"<div class='rule-row'><span>Candle</span><span style='color:#2ecc71'>Bullish pattern</span></div>"
            f"<div class='rule-row'><span>RSI</span><span style='color:#2ecc71'>Below 75</span></div>"
            f"<div class='rule-row'><span>Timeframes</span><span style='color:#2ecc71'>2+ aligned</span></div></div>"
            f"<div class='rule-box'><h5>SELL - All required</h5>"
            f"<div class='rule-row'><span>Structure</span><span style='color:#e74c3c'>LH / LL</span></div>"
            f"<div class='rule-row'><span>EMA</span><span style='color:#e74c3c'>EMA20 below EMA50</span></div>"
            f"<div class='rule-row'><span>Volume</span><span style='color:#e74c3c'>Sellers confirmed</span></div>"
            f"<div class='rule-row'><span>Candle</span><span style='color:#e74c3c'>Bearish pattern</span></div>"
            f"<div class='rule-row'><span>RSI</span><span style='color:#e74c3c'>Above 25</span></div>"
            f"<div class='rule-row'><span>Timeframes</span><span style='color:#e74c3c'>2+ aligned</span></div></div>"
            f"</div>"
            f"<div style='margin-top:16px;padding:12px;background:#0d0d0d;border-radius:8px;"
            f"font-size:12px;color:#444;border-left:3px solid #1e1e1e'>"
            f"Timeframe strategy: Daily → 4H → 1H → 15m (entry) · "
            f"WAIT is always valid · Aria never forces a trade</div>"
            f"</div></div></body></html>")
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  EXECUTE & CLOSE (manual override)
# ══════════════════════════════════════════════

@app.get("/execute")
async def execute_route(symbol: str = Query(...), side: str = Query(...)):
    symbol = symbol.upper(); side = side.upper()
    if symbol not in VALID_SYMBOLS or side not in ("BUY", "SELL"):
        return HTMLResponse(
            "<html><body style='background:#090909;color:#e74c3c;padding:30px'>"
            "<h2>❌ Invalid parameters</h2>"
            "<a href='/' style='color:#444'>Back</a></body></html>"
        )
    scan_data = scan(symbol)
    analysis  = analyze(scan_data)
    decision  = decide(analysis)
    result    = open_trade(symbol, side, analysis, decision)

    if result["success"]:
        pos   = result["position"]
        color = "#2ecc71" if side == "BUY" else "#e74c3c"
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#d0d0d0;font-family:Arial;padding:30px'>"
            f"<h2 style='color:{color}'>✅ Trade Executed - #{pos['trade_id']}</h2>"
            f"<p style='margin-top:10px'>{side} {pos['size']} {symbol} @ ${pos['entry_price']:,.2f}</p>"
            f"<p style='color:#444;margin-top:4px'>"
            f"SL ${pos['stop_loss']:,.2f} · TP ${pos['take_profit']:,.2f} · Risk ${pos['risk_amount']:.2f}</p>"
            f"<a href='/analyze?symbol={symbol}' style='color:#444;display:block;margin-top:20px'>← Back</a>"
            f"</body></html>"
        )
    else:
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#d0d0d0;font-family:Arial;padding:30px'>"
            f"<h2 style='color:#f39c12'>⚠️ Trade Rejected</h2>"
            f"<p style='margin-top:10px;color:#666'>{result['reason']}</p>"
            f"<a href='/analyze?symbol={symbol}' style='color:#444;display:block;margin-top:20px'>← Back</a>"
            f"</body></html>"
        )


@app.get("/close")
async def close_route(symbol: str = "BTCUSD", trade_id: str = ""):
    from database import get_open_positions
    positions = get_open_positions()
    if not positions:
        return HTMLResponse(
            "<html><body style='background:#090909;color:#d0d0d0;padding:30px'>"
            "<h2>No open position</h2>"
            "<a href='/' style='color:#444'>Back</a></body></html>"
        )
    # Close specific trade if trade_id given, else close first
    if trade_id:
        position = next((p for p in positions if p.get("trade_id","") == trade_id), positions[0])
    else:
        position = positions[0]
    price  = fetch_current_price(position["symbol"])
    result = close_trade(position, price, "Manual close")
    color  = "#2ecc71" if result["pl"] >= 0 else "#e74c3c"
    sym    = position["symbol"]
    return HTMLResponse(
        f"<html><body style='background:#090909;color:#d0d0d0;font-family:Arial;padding:30px'>"
        f"<h2>✅ Position Closed</h2>"
        f"<p style='margin-top:10px'>P/L: <b style='color:{color}'>${result['pl']:,.2f}</b></p>"
        f"<p style='color:#444'>New Balance: ${result['new_balance']:,.2f} · "
        f"Duration: {result['duration']}</p>"
        f"<a href='/analyze?symbol={sym}' style='color:#444;display:block;margin-top:20px'>← Back</a>"
        f"</body></html>"
    )


# ══════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════

@app.get("/api/status")
def api_status():
    return get_auto_status()

@app.get("/api/analyze")
async def api_analyze(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS: symbol = "BTCUSD"
    scan_data = scan(symbol)
    if not scan_data["candles"]: return {"error": "Market data unavailable"}
    analysis  = analyze(scan_data)
    decision  = decide(analysis)
    return {
        "symbol":     symbol,
        "price":      scan_data["price"],
        "decision":   decision["decision"],
        "confidence": decision["confidence"]["total"],
        "reasons":    decision["reasons"],
        "levels":     decision["levels"],
        "session":    scan_data["session"],
        "auto_status":get_auto_status(),
    }

@app.get("/api/equity")
async def api_equity():
    """Called every 5 seconds by frontend - sums ALL open positions for equity."""
    from database import recalc_equity
    account   = get_account()
    positions = get_open_positions()
    total_pl  = 0.0
    pos_data  = []
    prices    = {}
    for pos in positions:
        sym = pos["symbol"]
        if sym not in prices:
            prices[sym] = fetch_current_price(sym)
        p     = prices[sym]
        entry = pos["entry_price"]
        size  = pos["size"]
        side  = pos["side"]
        fl    = (p-entry)*size if side=="BUY" else (entry-p)*size
        fl    = round(fl, 2)
        total_pl += fl
        pos_data.append({
            "trade_id": pos.get("trade_id",""),
            "symbol":   sym,
            "side":     side,
            "pl":       fl,
            "price":    p,
        })
    # Recalculate and save equity in DB
    if positions:
        equity = recalc_equity(prices)
    else:
        equity = float(account["balance"])
    balance = float(account["balance"])
    return {
        "balance":     balance,
        "equity":      equity,
        "floating_pl": round(total_pl, 2),
        "positions":   pos_data,
        "has_position":len(positions) > 0,
    }

@app.get("/intelligence", response_class=HTMLResponse)
async def intelligence_page(symbol: str = "BTCUSD"):
    from intelligence import get_intelligence
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS:
        symbol = "BTCUSD"

    try:
        intel = get_intelligence(symbol)
    except Exception:
        intel = {}

    fg      = intel.get("fg",      {"value":50,"label":"Loading...","change":0,"history":[],"signal":"NEUTRAL","available":False})
    markets = intel.get("markets", {"btc_dominance":0,"eth_dominance":0,"total_mcap":0,"mcap_change":0,"dxy":0,"dxy_change":0,"dxy_available":False,"available":False})
    coin    = intel.get("coin",    {"price":0,"change_24h":0,"volume_24h":0,"high_24h":0,"low_24h":0,"available":False})
    funding = intel.get("funding", {"rate":0,"annualized":0,"signal":"NEUTRAL","available":False})
    oi      = intel.get("oi",      {"value_usd":0,"change_24h":0,"history":[],"signal":"STABLE","available":False})
    ls      = intel.get("ls",      {"longs":50,"shorts":50,"history":[],"signal":"BALANCED","available":False})
    liq     = intel.get("liq",     {"longs_usd":0,"shorts_usd":0,"signal":"NEUTRAL","available":False})
    news    = intel.get("news",    [])
    whales  = intel.get("whales",  [])
    events  = intel.get("events",  [])
    regime  = intel.get("regime",  {"score":50,"regime":"NEUTRAL","color":"#888","confidence":50,"primary":"Loading...","risk":"Loading...","bull_factors":[],"bear_factors":[]})
    ts      = intel.get("timestamp","")

    coin_name = "Bitcoin" if "BTC" in symbol else "Ethereum"
    tv_sym    = "BITSTAMP:BTCUSD" if "BTC" in symbol else "BITSTAMP:ETHUSD"
    other_sym = "ETHUSD" if "BTC" in symbol else "BTCUSD"
    other_name= "ETH" if "BTC" in symbol else "BTC"

    def sc(v):
        return "#2ecc71" if v > 0 else "#e74c3c" if v < 0 else "#888"
    def sig_c(s):
        g={"BULLISH","EXTREME_FEAR","OVERHEATED_SHORTS","CROWDED_SHORTS","FROM_EXCHANGE","STRONG_EXPANSION","EXPANDING"}
        b={"BEARISH","EXTREME_GREED","OVERHEATED_LONGS","CROWDED_LONGS","TO_EXCHANGE","STRONG_CONTRACTION","CONTRACTING"}
        return "#2ecc71" if s in g else "#e74c3c" if s in b else "#888"
    def na_str(v, fmt="", suffix=""):
        if v is None or v == 0: return "—"
        if fmt: return fmt.format(v) + suffix
        return str(v) + suffix
    def avail_c(available):
        return "" if available else " style='color:#444'"

    fgv = fg.get("value",50)
    fgc = ("#00bfff" if fgv<=25 else "#2ecc71" if fgv<=45
           else "#888" if fgv<=55 else "#f39c12" if fgv<=75 else "#e74c3c")

    # Sparkline helper
    def sparkline(hist, color="#2ecc71"):
        if len(hist) < 2: return ""
        mx = max(hist) or 1; mn = min(hist); rng = mx-mn or 1
        pts = " ".join(f"{i*10},{80-int((v-mn)/rng*70)}" for i,v in enumerate(hist))
        return f'<svg viewBox="0 0 {len(hist)*10} 80" style="width:100%;height:35px;margin-top:6px"><polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>'

    # News HTML
    news_html = ""
    for n in news[:20]:
        sent = n.get("sentiment","NEUTRAL")
        nc   = "#2ecc71" if sent=="BULLISH" else "#e74c3c" if sent=="BEARISH" else "#444"
        hot  = ' <span style="background:#2e1a00;color:#f39c12;font-size:9px;padding:2px 4px;border-radius:3px">HOT</span>' if n.get("important") else ""
        url  = n.get("url","")
        lnk  = f' <a href="{url}" target="_blank" style="color:#2ecc71;font-size:10px">Read →</a>' if url else ""
        news_html += (
            f'<div style="border-left:3px solid {nc};padding:10px 12px;margin-bottom:8px;background:#0d0d0d;border-radius:0 6px 6px 0">' +
            f'<div style="font-size:13px;color:#ccc;line-height:1.5">{n.get("title","")}{hot}{lnk}</div>' +
            f'<div style="margin-top:4px;font-size:10px;color:#333">{n.get("source","")} · {n.get("published","")} · <span style="color:{nc}">{sent}</span></div></div>'
        )
    if not news_html:
        news_html = '<div style="color:#444;padding:16px;font-size:13px">Loading news... Refreshes every 2 minutes.</div>'

    # Whale HTML
    whale_html = ""
    for w in whales[:10]:
        sig = w.get("signal","NEUTRAL")
        ico = "🟢" if sig=="BULLISH" else "🔴" if sig=="BEARISH" else "⚪"
        url = w.get("url","")
        lnk = f' <a href="{url}" target="_blank" style="color:#2ecc71;font-size:10px">→</a>' if url else ""
        whale_html += (
            f'<div style="padding:8px 12px;border-bottom:1px solid #141414;font-size:12px;color:#ccc;display:flex;justify-content:space-between">' +
            f'<span>{ico} {w.get("title","")[:75]}{lnk}</span>' +
            f'<span style="color:#333;font-size:10px;margin-left:8px;white-space:nowrap">{w.get("published","")}</span></div>'
        )
    if not whale_html:
        whale_html = '<div style="color:#444;padding:16px;font-size:12px">Loading whale data...</div>'

    # Events HTML
    evt_html = ""
    for e in events[:6]:
        url = e.get("url","")
        lnk = f' <a href="{url}" target="_blank" style="color:#f39c12;font-size:10px">→</a>' if url else ""
        evt_html += (
            f'<div style="background:#1a0f00;border:1px solid #3a2500;border-radius:6px;padding:10px;margin-bottom:8px">' +
            f'<div style="font-size:12px;color:#f39c12">{e.get("title","")}{lnk}</div>' +
            f'<div style="font-size:10px;color:#555;margin-top:3px">{e.get("source","")} · {e.get("published","")}</div></div>'
        )
    if not evt_html:
        evt_html = '<div style="color:#444;padding:10px;font-size:12px">No high-impact events detected.</div>'

    bull_html = "".join(f'<div style="padding:3px 0;font-size:12px;color:#2ecc71">✓ {b}</div>' for b in regime["bull_factors"])
    bear_html = "".join(f'<div style="padding:3px 0;font-size:12px;color:#e74c3c">✗ {b}</div>' for b in regime["bear_factors"])
    if not bull_html: bull_html = '<div style="color:#333;font-size:12px">No bullish factors detected</div>'
    if not bear_html: bear_html = '<div style="color:#333;font-size:12px">No bearish factors detected</div>'

    dxy_display  = f'{markets["dxy"]:.2f}' if markets.get("dxy_available") else "N/A"
    dxy_chg_disp = f'{markets["dxy_change"]:+.3f}' if markets.get("dxy_available") else ""

    # Metric cards data
    fund_rate  = f'{funding["rate"]:+.4f}%' if funding.get("available") else "Loading..."
    fund_ann   = f'Ann {funding["annualized"]:+.1f}%' if funding.get("available") else ""
    fund_sig   = funding["signal"].replace("_"," ") if funding.get("available") else "Connecting..."
    oi_val     = f'${oi["value_usd"]:.2f}B' if oi.get("available") and oi["value_usd"]>0 else "Loading..."
    oi_chg     = f'{oi["change_24h"]:+.2f}% 24h' if oi.get("available") else ""
    ls_long    = f'{ls["longs"]:.1f}%' if ls.get("available") else "Loading..."
    ls_short   = f'{ls["shorts"]:.1f}%' if ls.get("available") else ""
    liq_l      = f'${liq["longs_usd"]:.1f}M' if liq.get("available") else "Loading..."
    liq_s      = f'${liq["shorts_usd"]:.1f}M' if liq.get("available") else ""
    price_disp = f'${coin["price"]:,.2f}' if coin.get("available") and coin["price"]>0 else "Loading..."
    chg_disp   = f'{coin["change_24h"]:+.2f}%' if coin.get("available") else ""

    html = (
        '<!DOCTYPE html><html><head>' +
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
        '<meta http-equiv="refresh" content="120">' +
        f'<title>Aria Intel · {symbol}</title>' +
        base_css() +
        '<style>' +
        '.ig{display:grid;grid-template-columns:1fr 340px;gap:14px;padding:14px}' +
        '.mg{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;padding:14px}' +
        '.mc{background:#111;border:1px solid #1a1a1a;border-radius:10px;padding:14px}' +
        '.mv{font-size:18px;font-weight:bold;margin:4px 0}' +
        '.ml{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:1px}' +
        '.ms{font-size:11px;color:#555;margin-top:2px}' +
        '.sec{font-size:10px;color:#444;text-transform:uppercase;letter-spacing:2px;padding-bottom:8px;border-bottom:1px solid #111;margin-bottom:10px}' +
        '@media(max-width:760px){.ig{grid-template-columns:1fr}}' +
        '</style></head><body>' +
        _topbar("","intel") +
        # Symbol switcher
        f'<div style="padding:10px 14px;display:flex;gap:8px;align-items:center">' +
        f'<a href="/intelligence?symbol={symbol}" style="padding:6px 16px;background:#1a2e1a;border:1px solid #2ecc71;border-radius:20px;color:#2ecc71;font-size:12px;text-decoration:none">{"₿" if "BTC" in symbol else "Ξ"} {coin_name} Intel</a>' +
        f'<a href="/intelligence?symbol={other_sym}" style="padding:6px 16px;background:#111;border:1px solid #1a1a1a;border-radius:20px;color:#555;font-size:12px;text-decoration:none">{other_name}</a>' +
        f'<span style="margin-left:auto;font-size:11px;color:#333">Updated {ts} · Refreshes every 2 min</span></div>' +
        # Regime hero
        f'<div style="margin:0 14px 14px;background:#111;border:1px solid #1a1a1a;border-radius:12px;padding:20px;display:grid;grid-template-columns:auto 1fr;gap:20px;align-items:center">' +
        f'<div style="text-align:center"><div style="font-size:52px;font-weight:bold;color:{regime["color"]}">{regime["score"]}</div>' +
        f'<div style="font-size:10px;color:#444;letter-spacing:2px">SCORE / 100</div></div>' +
        f'<div><div style="font-size:22px;font-weight:bold;color:{regime["color"]};margin-bottom:8px">{regime["regime"]}</div>' +
        f'<div style="font-size:12px;color:#555;line-height:2">Confidence: <b style="color:#fff">{regime["confidence"]}%</b><br>' +
        f'Primary: <span style="color:#2ecc71">{regime["primary"]}</span><br>' +
        f'Risk: <span style="color:#e74c3c">{regime["risk"]}</span></div></div></div>' +
        # Metric cards
        '<div class="mg">' +
        # F&G
        f'<div class="mc"><div class="ml">Fear & Greed</div><div class="mv" style="color:{fgc}">{fgv}</div>' +
        f'<div class="ms">{fg.get("label","N/A")}</div>' +
        f'<div style="font-size:10px;color:#333">{("+" if fg.get("change",0)>=0 else "")}{fg.get("change",0)} vs yesterday</div>' +
        sparkline(fg.get("history",[]), fgc) + '</div>' +
        # BTC dominance
        f'<div class="mc"><div class="ml">BTC Dominance</div><div class="mv" style="color:#f7931a">{markets["btc_dominance"]}%</div>' +
        f'<div class="ms">ETH {markets["eth_dominance"]}%</div>' +
        f'<div style="font-size:10px;color:#333">${markets["total_mcap"]:.2f}T total</div></div>' +
        # Market cap
        f'<div class="mc"><div class="ml">Market Cap 24H</div>' +
        f'<div class="mv" style="color:{sc(markets["mcap_change"])}">{markets["mcap_change"]:+.2f}%</div>' +
        f'<div class="ms">${markets["total_mcap"]:.2f}T</div></div>' +
        # DXY - TradingView mini chart
        f'<div class="mc"><div class="ml">DXY Dollar Index</div>' +
        f'<div class="mv" style="color:{sc(-markets.get("dxy_change",0))}">{dxy_display}</div>' +
        f'<div class="ms">{dxy_chg_disp}</div>' +
        f'<div style="font-size:10px;color:#333">DXY↓ = crypto up</div></div>' +
        # Funding
        f'<div class="mc"><div class="ml">Funding Rate</div>' +
        f'<div class="mv" style="color:{sig_c(funding["signal"])}">{fund_rate}</div>' +
        f'<div class="ms">{fund_sig}</div><div style="font-size:10px;color:#333">{fund_ann}</div></div>' +
        # OI
        f'<div class="mc"><div class="ml">Open Interest</div>' +
        f'<div class="mv" style="color:#fff">{oi_val}</div>' +
        f'<div class="ms" style="color:{sc(oi.get("change_24h",0))}">{oi_chg}</div>' +
        sparkline(oi.get("history",[]), "#3498db") + '</div>' +
        # L/S
        f'<div class="mc"><div class="ml">Long / Short</div>' +
        f'<div class="mv" style="color:{sig_c(ls["signal"])}">{ls_long}</div>' +
        f'<div class="ms">Longs · {ls_short} Shorts</div>' +
        sparkline(ls.get("history",[]), "#9b59b6") + '</div>' +
        # Liquidations
        f'<div class="mc"><div class="ml">Liquidations 1H</div>' +
        f'<div class="mv" style="color:#e74c3c">{liq_l}</div>' +
        f'<div class="ms" style="color:#2ecc71">Shorts {liq_s}</div>' +
        f'<div style="font-size:10px;color:{sig_c(liq["signal"])}">{liq["signal"]}</div></div>' +
        # Price
        f'<div class="mc"><div class="ml">{coin_name} Price</div>' +
        f'<div class="mv" style="color:#fff">{price_disp}</div>' +
        f'<div class="ms" style="color:{sc(coin.get("change_24h",0))}">{chg_disp}</div>' +
        f'<div style="font-size:10px;color:#333">H {na_str(coin.get("high_24h",0),"${:,.0f}")} L {na_str(coin.get("low_24h",0),"${:,.0f}")}</div></div>' +
        '</div>' +
        # TradingView live chart
        f'<div style="margin:0 14px 14px;background:#111;border:1px solid #1a1a1a;border-radius:10px;overflow:hidden">' +
        f'<div style="padding:10px 14px;font-size:10px;color:#444;text-transform:uppercase;letter-spacing:2px">Live Chart — {coin_name}</div>' +
        f'<div class="tradingview-widget-container" style="height:300px">' +
        f'<div id="tv_chart"></div>' +
        f'<script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>' +
        f'{{"width":"100%","height":300,"symbol":"{tv_sym}","interval":"60","timezone":"Etc/UTC",' +
        f'"theme":"dark","style":"1","locale":"en","enable_publishing":false,' +
        f'"allow_symbol_change":false,"container_id":"tv_chart"}}' +
        '</script></div></div>' +
        # Main grid
        '<div class="ig">' +
        # Left: News + Whales
        f'<div><div class="sec">📰 Live News Feed — {coin_name}</div>' +
        news_html +
        '<div class="sec" style="margin-top:16px">🐋 Whale Transactions</div>' +
        '<div style="background:#111;border:1px solid #1a1a1a;border-radius:10px;overflow:hidden;margin-bottom:12px">' +
        '<div style="padding:8px 12px;font-size:11px;color:#333;border-bottom:1px solid #141414">' +
        '🟢 Leaving exchange = accumulation (bullish) · 🔴 Entering exchange = selling (bearish)</div>' +
        whale_html + '</div></div>' +
        # Right sidebar
        '<div>' +
        '<div class="sec">📅 High Impact Events</div>' +
        evt_html +
        '<div class="sec" style="margin-top:14px">📊 Regime Factors</div>' +
        '<div style="background:#0a1a0a;border:1px solid #1a2e1a;border-radius:8px;padding:12px;margin-bottom:10px">' +
        '<div style="font-size:11px;color:#2ecc71;font-weight:bold;margin-bottom:6px">BULLISH FACTORS</div>' +
        bull_html + '</div>' +
        '<div style="background:#1a0a0a;border:1px solid #2e1a1a;border-radius:8px;padding:12px;margin-bottom:10px">' +
        '<div style="font-size:11px;color:#e74c3c;font-weight:bold;margin-bottom:6px">BEARISH FACTORS</div>' +
        bear_html + '</div>' +
        '<div class="sec" style="margin-top:14px">📖 How to Read</div>' +
        '<div style="background:#0d0d0d;border-radius:8px;padding:12px;font-size:11px;color:#444;line-height:2">' +
        '<b style="color:#666">Score 72-100:</b> Strongly Bullish<br>' +
        '<b style="color:#666">Score 57-71:</b> Bullish<br>' +
        '<b style="color:#666">Score 44-56:</b> Neutral — wait<br>' +
        '<b style="color:#666">Score 29-43:</b> Bearish<br>' +
        '<b style="color:#666">Score 0-28:</b> Strongly Bearish<br>' +
        '<b style="color:#666">Funding +:</b> Longs crowded → flush risk<br>' +
        '<b style="color:#666">Funding -:</b> Shorts crowded → squeeze<br>' +
        '<b style="color:#666">OI Rising:</b> New money entering market<br>' +
        '<b style="color:#666">Whale from exchange:</b> Accumulation<br>' +
        '<b style="color:#666">DXY rising:</b> Bad for crypto</div>' +
        f'<div style="margin-top:10px;font-size:10px;color:#222;text-align:center;line-height:1.8">' +
        'Sources: Binance Futures · CoinGecko · Alternative.me<br>' +
        'CryptoPanic · Whale Alert · TradingEconomics · Kraken<br>' +
        'All free APIs · Auto-refreshes every 2 minutes</div>' +
        '</div></div></body></html>'
    )
    return HTMLResponse(html)



@app.get("/health")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine",
            "auto_trading": get_auto_status()["running"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
