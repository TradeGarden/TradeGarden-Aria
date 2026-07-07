"""
main.py — Aria AI Trading Engine
==================================
Starts the FastAPI server and connects all modules.
Contains ZERO trading logic — that lives in the modules below.

On startup:
  - Auto-trading loop begins immediately (executor.start_auto_trading)
  - Scans BTC and ETH every 60 seconds
  - Opens trades automatically when ALL conditions are met
  - trade_manager.py runs as a separate Render background worker

Module map:
  scanner.py          Stage 1 — Fetch market data
  analyzer.py         Stage 2 — Calculate all indicators
  decision_engine.py  Stage 3 — Make one decision: BUY / SELL / WAIT
  executor.py         Stage 4 — Auto-trade with full rule enforcement
  trade_manager.py    Stage 5 — Background position monitoring
  journal.py          Stage 6 — Permanent trade storage
  reports.py          Stage 7 — Daily/weekly/monthly performance reports
  recommendations.py  Stage 8 — Self-analysis and improvement suggestions
  config.py           All system settings
"""

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import os

# ── Module imports ──
from scanner         import scan, fetch_current_price
from analyzer        import analyze
from decision_engine import decide
from executor        import (
    open_trade, close_trade,
    load_position, load_balance,
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
#  STARTUP — begin auto-trading when server launches
# ══════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_auto_trading()   # ← Aria starts trading itself here
    yield
    stop_auto_trading()


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
        return "<span style='color:#333;font-size:13px'>No significant pattern detected</span>"
    out = ""
    for p in patterns:
        c = "#2ecc71" if p["direction"]=="Bullish" else ("#e74c3c" if p["direction"]=="Bearish" else "#555")
        out += (f"<div style='padding:6px 0;border-bottom:1px solid #141414'>"
                f"<span style='color:{c};font-weight:bold'>{p['name']}</span>"
                f"<span style='color:#333;font-size:11px;margin-left:8px'>"
                f"{p['direction']} · {p['strength']} · Reliability: {p['reliability']}</span></div>")
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
    out = (f"<div style='padding:5px 0;font-size:13px'>"
           f"<span style='color:#2ecc71'>BSL</span>"
           f"<span style='color:#444;font-size:12px;margin-left:8px'>${liq['buy_side_level']:,.2f}</span></div>"
           f"<div style='padding:5px 0;font-size:13px;border-bottom:1px solid #141414'>"
           f"<span style='color:#e74c3c'>SSL</span>"
           f"<span style='color:#444;font-size:12px;margin-left:8px'>${liq['sell_side_level']:,.2f}</span></div>")
    if liq["equal_highs"]:
        out += (f"<div style='padding:5px 0;color:#f39c12;font-size:12px'>"
                f"Equal Highs @ ${liq['equal_highs_level']:,.2f} — BSL above</div>")
    if liq["equal_lows"]:
        out += (f"<div style='padding:5px 0;color:#f39c12;font-size:12px'>"
                f"Equal Lows @ ${liq['equal_lows_level']:,.2f} — SSL below</div>")
    if liq["sweep"]:
        sw = liq["sweep"]
        out += (f"<div style='padding:8px;margin-top:6px;background:#1a1200;"
                f"border-radius:6px;border:1px solid #f39c12;font-size:12px'>"
                f"<b style='color:#f39c12'>⚠️ Sweep</b> — {sw['direction']} · {sw['signal']}</div>")
    return out

def _levels_html(levels):
    def bar(s):
        w = {"High":100,"Moderate":60,"Low":30}.get(s,40)
        c = {"High":"#2ecc71","Moderate":"#f39c12","Low":"#e74c3c"}.get(s,"#555")
        return (f"<div class='lv-bar-bg'>"
                f"<div style='width:{w}%;height:100%;background:{c};border-radius:3px'></div></div>")
    out = ""
    for lbl, lvl, touches, strength, color in [
        ("Support",    levels["support"],    levels["support_touches"],    levels["support_strength"],    "#2ecc71"),
        ("Resistance", levels["resistance"], levels["resistance_touches"], levels["resistance_strength"], "#e74c3c"),
    ]:
        out += (f"<div style='padding:7px 0;border-bottom:1px solid #141414'>"
                f"<div style='display:flex;justify-content:space-between'>"
                f"<span style='color:{color};font-size:13px;font-weight:bold'>{lbl}</span>"
                f"<span style='color:#fff;font-size:13px'>${lvl:,.2f}</span></div>"
                f"<div style='color:#333;font-size:11px;margin-top:2px'>"
                f"Tested {touches}× · {strength}</div>"
                f"{bar(strength)}</div>")
    return out

def _conf_breakdown(conf):
    maxes = {"Market Structure":20,"EMA Alignment":15,"RSI":10,
             "MACD":15,"Candlestick":20,"Volume":20}
    out = ""
    for k, v in conf["breakdown"].items():
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
        return (f"<div style='color:#333;padding:20px;text-align:center'>"
                f"{r.get('message','No data')}</div>")
    wr_c = "#2ecc71" if r["win_rate"]>=55 else ("#f39c12" if r["win_rate"]>=40 else "#e74c3c")
    pl_c = "#2ecc71" if r["total_pl"]>=0 else "#e74c3c"
    return f"""
<div class="wk-grid">
  <div class="wk-stat"><div class="v">{r['trades']}</div><div class="l">Trades</div></div>
  <div class="wk-stat"><div class="v" style="color:#2ecc71">{r['wins']}</div><div class="l">Wins</div></div>
  <div class="wk-stat"><div class="v" style="color:#e74c3c">{r['losses']}</div><div class="l">Losses</div></div>
  <div class="wk-stat"><div class="v" style="color:{wr_c}">{r['win_rate']}%</div><div class="l">Win Rate</div></div>
  <div class="wk-stat"><div class="v" style="color:#2ecc71">${r['avg_win']}</div><div class="l">Avg Win</div></div>
  <div class="wk-stat"><div class="v" style="color:#e74c3c">${r['avg_loss']}</div><div class="l">Avg Loss</div></div>
  <div class="wk-stat"><div class="v" style="color:{pl_c}">${r['total_pl']}</div><div class="l">Total P/L</div></div>
  <div class="wk-stat"><div class="v" style="color:#2ecc71">${r['best_trade']['pl']}</div><div class="l">Best</div></div>
  <div class="wk-stat"><div class="v" style="color:#e74c3c">${r['worst_trade']['pl']}</div><div class="l">Worst</div></div>
</div>"""

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

    scan_data = scan(symbol)
    if not scan_data["candles"]:
        return HTMLResponse(
            f"<html><body style='background:#090909;color:#e74c3c;"
            f"font-family:Arial;padding:30px'>"
            f"<h2>⚠️ Market data unavailable</h2>"
            f"<a href='/' style='color:#444;display:block;margin-top:16px'>↻ Retry</a>"
            f"</body></html>"
        )

    analysis = analyze(scan_data)
    decision = decide(analysis)
    balance  = load_balance()
    position = load_position()
    price    = scan_data["price"]
    dec      = decision["decision"]
    conf     = decision["confidence"]["total"]
    levels   = decision["levels"]
    tc_color = _tc(decision["trend"])
    cc_color = _cc(conf)

    today_trades = todays_trade_count()
    today_loss   = todays_loss_pct(balance)
    risk_usd     = round(balance * RISK_PER_TRADE_PCT / 100, 2)

    # ── Open position block ──
    pos_block = ""
    if position and position.get("symbol") == symbol:
        entry = position["entry_price"]
        size  = position["size"]
        side  = position["side"]
        pl    = (price-entry)*size if side=="BUY" else (entry-price)*size
        pl    = round(pl, 2)
        pl_c  = "#2ecc71" if pl >= 0 else "#e74c3c"
        pos_block = (
            f"<div class='pos-card pos-{side.lower()}'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<span style='font-weight:bold;color:#fff'>{side} {size} {symbol[:3]}</span>"
            f"<span style='color:#444;font-size:11px'>#{position.get('trade_id','')}</span></div>"
            f"<div style='margin-top:5px;font-size:12px;color:#555'>"
            f"Entry ${entry:,.2f} · SL ${position['stop_loss']:,.2f} · TP ${position['take_profit']:,.2f}</div>"
            f"<div style='color:{pl_c};font-size:18px;font-weight:bold;margin-top:6px'>"
            f"P/L: ${pl:,.2f}</div>"
            f"<a href='/close?symbol={symbol}' class='btn btn-close' "
            f"style='display:inline-block;margin-top:8px'>Close Position</a></div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{DASHBOARD_REFRESH_SECONDS}">
<title>Aria — {symbol}</title>
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
      {"<div style='margin-top:8px;padding:7px;background:#0d2e1a;border-radius:6px;color:#2ecc71;font-size:12px'>✅ BOS — Break of Structure confirmed</div>" if analysis['ms']['bos'] else ""}
      {"<div style='margin-top:8px;padding:7px;background:#2e1a0d;border-radius:6px;color:#f39c12;font-size:12px'>⚠️ CHoCH — Change of Character detected</div>" if analysis['ms']['choch'] else ""}
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
      <div class="explain">Minimum {MIN_CONFIDENCE}% required before Aria trades.</div>
      {_conf_breakdown(decision['confidence'])}
    </div>

    <div class="adv-card" style="grid-column:1/-1">
      <h4>AI Reasoning</h4>
      <div class="explain">Aria's full narrative analysis of the current setup.</div>
      <div style="font-size:13px;line-height:1.9;color:#bbb;white-space:pre-wrap;margin-top:8px">
        {decision['narrative']}
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
    </div>
  </div>

  {pos_block}

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
</body></html>"""
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  REPORTS
# ══════════════════════════════════════════════

@app.get("/weekly", response_class=HTMLResponse)
async def reports_page():
    d = daily_report(); w = weekly_report(); m = monthly_report()
    html = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            f"<title>Aria · Reports</title>{base_css()}</head><body>"
            f"{_topbar('', 'weekly')}"
            f"<div style='max-width:960px;margin:24px auto;padding:0 20px'>"
            f"<div class='card'><div class='card-title'>Daily — Last 24 Hours</div>{_report_block(d)}</div>"
            f"<div class='card'><div class='card-title'>Weekly — Last 7 Days</div>{_report_block(w)}</div>"
            f"<div class='card'><div class='card-title'>Monthly — Last 30 Days</div>{_report_block(m)}</div>"
            f"</div></body></html>")
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  RECOMMENDATIONS
# ══════════════════════════════════════════════

@app.get("/recommendations", response_class=HTMLResponse)
async def recommendations_page():
    recs  = get_recommendations()
    pcol  = {"high":"#e74c3c","medium":"#f39c12","info":"#2ecc71"}
    items = ""
    for i, rec in enumerate(recs):
        pc = pcol.get(rec["priority"], "#888")
        items += (
            f"<div class='rec-item'>"
            f"<div class='rec-header' onclick='t({i})'>"
            f"<span><span style='display:inline-block;width:8px;height:8px;"
            f"border-radius:50%;background:{pc};margin-right:8px'></span>"
            f"{rec['title']}</span>"
            f"<span style='color:#333' id='ra{i}'>▸</span></div>"
            f"<div class='rec-body' id='rb{i}'>{rec['detail']}</div></div>"
        )
    html = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            f"<title>Aria · Recommendations</title>{base_css()}</head><body>"
            f"{_topbar('', 'recs')}"
            f"<div style='max-width:800px;margin:24px auto;padding:0 20px'>"
            f"<div class='card'>"
            f"<div class='card-title'>Improvement Recommendations</div>"
            f"<div style='font-size:12px;color:#333;margin-bottom:12px'>"
            f"Tap each item to read. You decide whether to apply it. "
            f"Aria never changes the strategy automatically.</div>"
            f"{items}</div></div>"
            f"<script>function t(i){{"
            f"var b=document.getElementById('rb'+i),"
            f"a=document.getElementById('ra'+i);"
            f"b.classList.toggle('open');"
            f"a.textContent=b.classList.contains('open')?'▾':'▸';}}"
            f"</script></body></html>")
    return HTMLResponse(html)


# ══════════════════════════════════════════════
#  JOURNAL
# ══════════════════════════════════════════════

@app.get("/journal", response_class=HTMLResponse)
async def journal_page():
    trades = load_recent(50)
    rows   = ""
    for t in trades:
        action    = t.get("action", "")
        pl        = t.get("pl", 0)
        pl_c      = "#2ecc71" if pl > 0 else ("#e74c3c" if pl < 0 else "#888")
        date      = t.get("closed_at", t.get("opened_at", t.get("timestamp","")))[:16].replace("T", " ")
        entry_str = f" · Entry ${t['entry']:,.2f}"  if "entry" in t else ""
        exit_str  = f" → Exit ${t['exit']:,.2f}"   if "exit"  in t else ""
        pl_div    = f"<div style='color:{pl_c};font-weight:bold'>P/L: ${pl}</div>" if action=="CLOSE" else ""
        reason    = t.get("exit_reason", t.get("reason", ""))
        rsn_div   = f"<div style='color:#333;font-size:11px'>{reason}</div>" if reason else ""
        sym       = t.get("symbol",""); side2 = t.get("side",""); tid = t.get("trade_id","")[:6]
        action_color = ("#2ecc71" if action=="OPEN" else
                        "#e74c3c" if action=="CLOSE" else
                        "#f39c12" if action=="WAIT" else "#555")
        rows += (f"<div class='trade-row'>"
                 f"<div style='display:flex;justify-content:space-between;margin-bottom:4px'>"
                 f"<span style='font-weight:bold;color:#fff'>{sym} {side2} "
                 f"<span style='color:{action_color};font-size:11px'>[{action}]</span> "
                 f"<span style='color:#333;font-size:11px'>#{tid}</span></span>"
                 f"<span style='color:#333'>{date}</span></div>"
                 f"<div style='color:#555;margin-bottom:3px'>{entry_str}{exit_str}</div>"
                 f"{pl_div}{rsn_div}</div>")
    if not rows:
        rows = "<div style='color:#333;padding:30px;text-align:center'>No trades recorded yet. Aria is scanning...</div>"
    html = (f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
            f"<title>Aria · Journal</title>{base_css()}</head><body>"
            f"{_topbar('', 'journal')}"
            f"<div style='max-width:900px;margin:24px auto;padding:0 20px'>{rows}</div>"
            f"</body></html>")
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
            f"<div class='card-title'>Current Trading Rules — config.py</div>"
            f"<div style='font-size:12px;color:#333;margin-bottom:12px'>"
            f"These rules are enforced on every trade. Aria never breaks them. "
            f"Change them in config.py — Aria applies them automatically.</div>"
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
            f"<div class='rule-box'><h5>BUY — All required</h5>"
            f"<div class='rule-row'><span>Structure</span><span style='color:#2ecc71'>HH / HL</span></div>"
            f"<div class='rule-row'><span>EMA</span><span style='color:#2ecc71'>EMA20 above EMA50</span></div>"
            f"<div class='rule-row'><span>Volume</span><span style='color:#2ecc71'>Buyers confirmed</span></div>"
            f"<div class='rule-row'><span>Candle</span><span style='color:#2ecc71'>Bullish pattern</span></div>"
            f"<div class='rule-row'><span>RSI</span><span style='color:#2ecc71'>Below 75</span></div>"
            f"<div class='rule-row'><span>Timeframes</span><span style='color:#2ecc71'>2+ aligned</span></div></div>"
            f"<div class='rule-box'><h5>SELL — All required</h5>"
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
            f"<h2 style='color:{color}'>✅ Trade Executed — #{pos['trade_id']}</h2>"
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
async def close_route(symbol: str = "BTCUSD"):
    position = load_position()
    if not position:
        return HTMLResponse(
            "<html><body style='background:#090909;color:#d0d0d0;padding:30px'>"
            "<h2>No open position</h2>"
            "<a href='/' style='color:#444'>Back</a></body></html>"
        )
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

@app.get("/health")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine",
            "auto_trading": get_auto_status()["running"]}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
