from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import requests
import json
import os
from openai import OpenAI

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AI_API_KEY = os.getenv("AI_API_KEY")
client = OpenAI(api_key=AI_API_KEY) if AI_API_KEY and AI_API_KEY.startswith("sk-") else None

MAX_RISK_PERCENT = 1.0
VALID_SYMBOLS = ["BTCUSD", "ETHUSD"]
BINANCE_MAP   = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}

# ─────────────────────────────────────────────
# STATE PERSISTENCE
# ─────────────────────────────────────────────

def load_balance() -> float:
    try:
        if os.path.exists("paper_balance.txt"):
            with open("paper_balance.txt", "r") as f:
                return float(f.read().strip())
    except Exception:
        pass
    return 500.0

def save_balance(balance: float):
    with open("paper_balance.txt", "w") as f:
        f.write(str(round(balance, 2)))

def load_position():
    try:
        if os.path.exists("paper_position.json"):
            with open("paper_position.json", "r") as f:
                data = json.load(f)
                return data if data else None
    except Exception:
        pass
    return None

def save_position(position):
    with open("paper_position.json", "w") as f:
        json.dump(position, f)

def save_to_journal(entry: dict):
    with open("trade_journal.txt", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ─────────────────────────────────────────────
# REAL MARKET DATA — Binance public API (no key needed)
# Returns list of dicts: {open, high, low, close, volume}
# ─────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str = "1d", limit: int = 100):
    """
    Fetch real OHLCV candles from Binance public API.
    No API key required. Returns list of candle dicts.
    """
    pair = BINANCE_MAP.get(symbol, "BTCUSDT")
    url  = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        raw = r.json()
        candles = []
        for c in raw:
            candles.append({
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        return candles
    except Exception as e:
        return []

def fetch_current_price(symbol: str) -> float:
    pair = BINANCE_MAP.get(symbol, "BTCUSDT")
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={pair}",
            timeout=10
        )
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return 62000.0 if "BTC" in symbol else 3400.0

# ─────────────────────────────────────────────
# TECHNICAL INDICATORS
# ─────────────────────────────────────────────

def ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return round(sum(closes) / len(closes), 2)
    subset = closes[-period:]
    k = 2 / (period + 1)
    val = subset[0]
    for p in subset[1:]:
        val = p * k + val * (1 - k)
    return round(val, 2)

def rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    relevant = closes[-(period + 1):]
    gains, losses = [], []
    for i in range(1, len(relevant)):
        diff = relevant[i] - relevant[i - 1]
        if diff >= 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 1e-9
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def detect_market_structure(candles: list) -> dict:
    """
    Detect HH/HL (uptrend) or LH/LL (downtrend) using swing highs and lows.
    Looks at the last 20 candles.
    """
    recent = candles[-20:]
    highs  = [c["high"]  for c in recent]
    lows   = [c["low"]   for c in recent]

    # Split into two halves and compare
    mid = len(recent) // 2
    first_half_high  = max(highs[:mid])
    second_half_high = max(highs[mid:])
    first_half_low   = min(lows[:mid])
    second_half_low  = min(lows[mid:])

    higher_high = second_half_high > first_half_high
    higher_low  = second_half_low  > first_half_low
    lower_high  = second_half_high < first_half_high
    lower_low   = second_half_low  < first_half_low

    if higher_high and higher_low:
        structure = "HH / HL — Uptrend"
        trend = "Bullish"
    elif lower_high and lower_low:
        structure = "LH / LL — Downtrend"
        trend = "Bearish"
    elif higher_high and lower_low:
        structure = "HH / LL — Expansion / Volatile"
        trend = "Neutral"
    else:
        structure = "LH / HL — Consolidation / Range"
        trend = "Neutral"

    return {
        "structure": structure,
        "trend": trend,
        "swing_high": round(second_half_high, 2),
        "swing_low":  round(second_half_low,  2),
    }

def atr(candles: list, period: int = 14) -> float:
    """Average True Range — used for dynamic S/R and SL/TP."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]
        high = candles[i]["high"]
        low  = candles[i]["low"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 2)

def compute_indicators(candles: list, current_price: float) -> dict:
    closes = [c["close"] for c in candles]
    closes.append(current_price)  # include live price

    ema20_val = ema(closes, 20)
    ema50_val = ema(closes, 50)
    rsi14_val = rsi(closes, 14)
    atr14_val = atr(candles, 14)
    ms        = detect_market_structure(candles)

    # Support = lowest low of last 20 candles
    # Resistance = highest high of last 20 candles
    support    = round(min(c["low"]  for c in candles[-20:]), 2)
    resistance = round(max(c["high"] for c in candles[-20:]), 2)

    # Signal: combine EMA cross + RSI + market structure
    bullish = ema20_val > ema50_val and rsi14_val < 70 and ms["trend"] == "Bullish"
    bearish = ema20_val < ema50_val and rsi14_val > 30 and ms["trend"] == "Bearish"

    if bullish:
        decision    = "BUY (Long)"
        stop_loss   = round(current_price - (atr14_val * 1.5), 2)
        take_profit = round(current_price + (atr14_val * 3.0), 2)
    elif bearish:
        decision    = "SELL (Short)"
        stop_loss   = round(current_price + (atr14_val * 1.5), 2)
        take_profit = round(current_price - (atr14_val * 3.0), 2)
    else:
        decision    = "HOLD"
        stop_loss   = round(current_price - (atr14_val * 1.5), 2)
        take_profit = round(current_price + (atr14_val * 1.5), 2)

    rr_ratio = round(abs(take_profit - current_price) / abs(stop_loss - current_price), 2) if stop_loss != current_price else 0

    return {
        "ema20":      ema20_val,
        "ema50":      ema50_val,
        "rsi14":      rsi14_val,
        "atr14":      atr14_val,
        "support":    support,
        "resistance": resistance,
        "decision":   decision,
        "stop_loss":  stop_loss,
        "take_profit":take_profit,
        "rr_ratio":   rr_ratio,
        "structure":  ms["structure"],
        "trend":      ms["trend"],
        "swing_high": ms["swing_high"],
        "swing_low":  ms["swing_low"],
    }

# ─────────────────────────────────────────────
# AI REASONING
# ─────────────────────────────────────────────

def get_ai_reasoning(symbol: str, price: float, ind: dict) -> str:
    fallback = (
        f"Signal: {ind['decision']}\n"
        f"Structure: {ind['structure']}\n"
        f"Trend: {ind['trend']}\n"
        f"EMA20 ({ind['ema20']:,.2f}) {'above' if ind['ema20'] > ind['ema50'] else 'below'} EMA50 ({ind['ema50']:,.2f})\n"
        f"RSI14: {ind['rsi14']} | ATR14: {ind['atr14']:,.2f}\n"
        f"Support: ${ind['support']:,.2f} | Resistance: ${ind['resistance']:,.2f}\n"
        f"Stop Loss: ${ind['stop_loss']:,.2f} | Take Profit: ${ind['take_profit']:,.2f}\n"
        f"Risk/Reward: 1:{ind['rr_ratio']}"
    )
    if not client:
        return fallback
    try:
        prompt = f"""You are a professional crypto trader. Give a concise structured analysis:

Symbol: {symbol} | Price: ${price:,.2f}
Signal: {ind['decision']}
Market Structure: {ind['structure']}
Trend: {ind['trend']}
EMA20: {ind['ema20']:,.2f} | EMA50: {ind['ema50']:,.2f}
RSI14: {ind['rsi14']} | ATR14: {ind['atr14']:,.2f}
Support: ${ind['support']:,.2f} | Resistance: ${ind['resistance']:,.2f}
Stop Loss: ${ind['stop_loss']:,.2f} | Take Profit: ${ind['take_profit']:,.2f}
Risk/Reward: 1:{ind['rr_ratio']}

Respond with:
1. Market Structure confirmation
2. Trend strength
3. Key reasons for the signal
4. Risk/Reward assessment
5. Confidence score (0-100%)
Max 150 words."""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.5,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return fallback

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def calc_pl(position: dict, current_price: float):
    entry = position["entry_price"]
    size  = position["size"]
    risk  = position["risk_amount"]
    pl    = (current_price - entry) * size if position["side"] == "BUY" else (entry - current_price) * size
    pl_pct = (pl / risk * 100) if risk > 0 else 0
    return round(pl, 2), round(pl_pct, 2)

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine"}


@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS:
        symbol = "BTCUSD"

    balance       = load_balance()
    position      = load_position()
    candles       = fetch_candles(symbol, interval="1d", limit=100)
    current_price = fetch_current_price(symbol)

    # If Binance failed, show error card
    if not candles:
        return HTMLResponse("""
        <html><body style='background:#111;color:#e74c3c;font-family:Arial;padding:30px'>
        <h2>⚠️ Market data unavailable</h2>
        <p>Could not fetch candles from Binance. Check network or try again.</p>
        <a href='/analyze' style='color:#aaa'>Retry</a>
        </body></html>""")

    ind       = compute_indicators(candles, current_price)
    ai_reason = get_ai_reasoning(symbol, current_price, ind)

    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    position_size = round(risk_amount / (current_price * 0.02), 6)

    # P/L block
    pl_block = ""
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)
        color = "#2ecc71" if pl >= 0 else "#e74c3c"
        pl_block = f"""
        <div class="card">
            <h3>📊 Open Position</h3>
            <p>Side: <b>{position['side']}</b> | Entry: <b>${position['entry_price']:,.2f}</b> | Size: {position['size']} {symbol[:3]}</p>
            <p style="color:{color};font-size:18px;font-weight:bold">P/L: ${pl:,.2f} ({pl_pct:.1f}%)</p>
            <a href="/close" class="btn btn-sell">❌ Close Position</a>
        </div>"""
    else:
        pl_block = '<div class="card"><p style="color:#666">No open position</p></div>'

    sig_color = "#2ecc71" if "BUY" in ind["decision"] else ("#e74c3c" if "SELL" in ind["decision"] else "#f39c12")
    trend_color = "#2ecc71" if ind["trend"] == "Bullish" else ("#e74c3c" if ind["trend"] == "Bearish" else "#f39c12")

    html = f"""<!DOCTYPE html>
<html><head><title>Aria Dashboard</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',Arial,sans-serif; background:#0d0d0d; color:#ddd; padding:24px; }}
  h1 {{ color:#fff; margin-bottom:4px; }}
  .sub {{ color:#666; font-size:12px; margin-bottom:20px; }}
  .card {{ background:#1a1a1a; border:1px solid #2a2a2a; padding:20px; border-radius:12px; margin:12px 0; }}
  .card h2 {{ color:#fff; margin-bottom:14px; }}
  .card h3 {{ color:#aaa; margin-bottom:10px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin:12px 0; }}
  .metric {{ background:#111; border-radius:8px; padding:12px; text-align:center; }}
  .metric .val {{ font-size:16px; font-weight:bold; color:#fff; }}
  .metric .lbl {{ font-size:11px; color:#666; margin-top:4px; }}
  .badge {{ display:inline-block; padding:5px 14px; border-radius:20px; font-weight:bold; font-size:14px; color:#fff; background:{sig_color}; }}
  .struct {{ display:inline-block; padding:4px 12px; border-radius:20px; font-size:13px; color:#fff; background:{trend_color}33; border:1px solid {trend_color}; color:{trend_color}; }}
  .reason {{ background:#111; border-radius:8px; padding:14px; font-size:13px; line-height:1.7; color:#ccc; white-space:pre-wrap; margin-top:10px; }}
  .nav {{ margin-top:16px; display:flex; gap:10px; flex-wrap:wrap; }}
  .btn {{ display:inline-block; padding:9px 18px; border-radius:8px; background:#2a2a2a; color:#ddd; text-decoration:none; font-size:13px; }}
  .btn-buy {{ background:#1a5c2e; color:#2ecc71; }}
  .btn-sell {{ background:#5c1a1a; color:#e74c3c; }}
  .divider {{ border:none; border-top:1px solid #222; margin:12px 0; }}
</style>
</head><body>
  <h1>🌱 Aria AI Trading Dashboard</h1>
  <p class="sub">Paper Trading · {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · Data: Binance</p>

  <div class="card">
    <h2>{symbol} · Real-Time Analysis</h2>

    <p style="margin-bottom:10px">
      Signal: <span class="badge">{ind['decision']}</span>
      &nbsp;&nbsp;
      Structure: <span class="struct">{ind['structure']}</span>
    </p>

    <div class="grid">
      <div class="metric"><div class="val">${current_price:,.2f}</div><div class="lbl">Price</div></div>
      <div class="metric"><div class="val">${ind['ema20']:,.2f}</div><div class="lbl">EMA 20</div></div>
      <div class="metric"><div class="val">${ind['ema50']:,.2f}</div><div class="lbl">EMA 50</div></div>
      <div class="metric"><div class="val">{ind['rsi14']}</div><div class="lbl">RSI 14</div></div>
      <div class="metric"><div class="val">${ind['atr14']:,.2f}</div><div class="lbl">ATR 14</div></div>
      <div class="metric"><div class="val">${ind['swing_high']:,.2f}</div><div class="lbl">Swing High</div></div>
      <div class="metric"><div class="val">${ind['swing_low']:,.2f}</div><div class="lbl">Swing Low</div></div>
      <div class="metric"><div class="val">${ind['support']:,.2f}</div><div class="lbl">Support</div></div>
      <div class="metric"><div class="val">${ind['resistance']:,.2f}</div><div class="lbl">Resistance</div></div>
      <div class="metric"><div class="val">${ind['stop_loss']:,.2f}</div><div class="lbl">Stop Loss</div></div>
      <div class="metric"><div class="val">${ind['take_profit']:,.2f}</div><div class="lbl">Take Profit</div></div>
      <div class="metric"><div class="val">1:{ind['rr_ratio']}</div><div class="lbl">Risk/Reward</div></div>
      <div class="metric"><div class="val">${risk_amount:.2f}</div><div class="lbl">Risk (1%)</div></div>
      <div class="metric"><div class="val">{position_size}</div><div class="lbl">Size ({symbol[:3]})</div></div>
      <div class="metric"><div class="val">${balance:,.2f}</div><div class="lbl">Balance</div></div>
    </div>

    <hr class="divider">
    <p style="color:#666;font-size:12px;margin-bottom:6px">AI Reasoning:</p>
    <div class="reason">{ai_reason}</div>
  </div>

  {pl_block}

  <div class="nav">
    <a href="/execute?symbol={symbol}&side=BUY" class="btn btn-buy">🟢 Execute BUY</a>
    <a href="/execute?symbol={symbol}&side=SELL" class="btn btn-sell">🔴 Execute SELL</a>
    <a href="/analyze?symbol=BTCUSD" class="btn">₿ BTC</a>
    <a href="/analyze?symbol=ETHUSD" class="btn">Ξ ETH</a>
    <a href="/journal" class="btn">📖 Journal</a>
  </div>
</body></html>"""
    return HTMLResponse(html)


@app.get("/api/analyze")
async def api_analyze(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in VALID_SYMBOLS:
        symbol = "BTCUSD"

    balance       = load_balance()
    position      = load_position()
    candles       = fetch_candles(symbol)
    current_price = fetch_current_price(symbol)

    if not candles:
        return {"error": "Could not fetch market data from Binance"}

    ind           = compute_indicators(candles, current_price)
    ai_reason     = get_ai_reasoning(symbol, current_price, ind)
    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    position_size = round(risk_amount / (current_price * 0.02), 6)

    pl, pl_pct = 0.0, 0.0
    if position and position["symbol"] == symbol:
        pl, pl_pct = calc_pl(position, current_price)

    return {
        "symbol": symbol,
        "price": current_price,
        "indicators": ind,
        "decision": ind["decision"],
        "ai_reasoning": ai_reason,
        "risk_amount_usd": risk_amount,
        "position_size": position_size,
        "account_balance": balance,
        "open_position": position,
        "unrealized_pl": pl,
        "unrealized_pl_pct": pl_pct,
    }


@app.get("/execute")
async def execute_trade(symbol: str = Query(...), side: str = Query(...)):
    symbol = symbol.upper()
    side   = side.upper()
    if symbol not in VALID_SYMBOLS:
        return HTMLResponse("<h2>❌ Invalid symbol</h2><a href='/analyze'>Back</a>")
    if side not in ("BUY", "SELL"):
        return HTMLResponse("<h2>❌ Invalid side (BUY or SELL only)</h2><a href='/analyze'>Back</a>")

    balance       = load_balance()
    current_price = fetch_current_price(symbol)
    risk_amount   = round(balance * MAX_RISK_PERCENT / 100, 2)
    size          = round(risk_amount / (current_price * 0.02), 6)

    position = {
        "symbol":      symbol,
        "side":        side,
        "entry_price": current_price,
        "size":        size,
        "risk_amount": risk_amount,
        "timestamp":   datetime.utcnow().isoformat(),
    }
    save_position(position)
    save_to_journal({"action": "EXECUTE_TRADE", "symbol": symbol, "side": side,
                     "price": current_price, "size": size,
                     "timestamp": datetime.utcnow().isoformat()})

    return HTMLResponse(
        f"<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
        f"<h2 style='color:#2ecc71'>✅ Paper Trade Executed</h2>"
        f"<p>{side} {size} {symbol} at ${current_price:,.2f}</p>"
        f"<p>Risk: ${risk_amount:.2f}</p>"
        f"<a href='/analyze?symbol={symbol}' style='color:#aaa'>← Back to Dashboard</a>"
        f"</body></html>"
    )


@app.get("/close")
async def close_position():
    position = load_position()
    if not position:
        return HTMLResponse(
            "<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
            "<h2>No open position to close</h2>"
            "<a href='/analyze' style='color:#aaa'>Back</a></body></html>"
        )

    balance       = load_balance()
    current_price = fetch_current_price(position["symbol"])
    pl, _         = calc_pl(position, current_price)
    new_balance   = round(balance + pl, 2)

    save_balance(new_balance)
    save_position(None)
    save_to_journal({
        "action":      "CLOSE_POSITION",
        "symbol":      position["symbol"],
        "entry_price": position["entry_price"],
        "exit_price":  current_price,
        "pl":          pl,
        "new_balance": new_balance,
        "timestamp":   datetime.utcnow().isoformat(),
    })

    color = "#2ecc71" if pl >= 0 else "#e74c3c"
    closed_symbol = position["symbol"]
    return HTMLResponse(
        f"<html><body style='background:#111;color:#ddd;font-family:Arial;padding:30px'>"
        f"<h2>✅ Position Closed</h2>"
        f"<p>P/L: <b style='color:{color}'>${pl:,.2f}</b></p>"
        f"<p>New Balance: <b>${new_balance:,.2f}</b></p>"
        f"<a href='/analyze?symbol={closed_symbol}' style='color:#aaa'>← Back to Dashboard</a>"
        f"</body></html>"
    )


@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-50:]
        entries = [json.loads(line.strip()) for line in lines if line.strip()]
        return {"count": len(entries), "entries": entries}
    except Exception:
        return {"count": 0, "entries": [], "message": "No entries yet"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
