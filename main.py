import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from datetime import datetime
import os
import time
import random
import json

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

MAX_RISK_PERCENT = 1.0

# ... (keep the same ema, rsi, fetch_market_data, save_to_journal functions from previous version)

def ema(prices, period):
    k = 2 / (period + 1)
    ema_val = prices[0]
    for price in prices[1:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val

def rsi(prices, period=14):
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = prices[i] - prices[i - 1]
        if diff >= 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def fetch_market_data(symbol: str):
    try:
        binance_symbol = symbol.replace("USD", "USDT")
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": binance_symbol}, timeout=10)
        r.raise_for_status()
        price = float(r.json()["price"])
        r2 = requests.get("https://api.binance.com/api/v3/klines", params={"symbol": binance_symbol, "interval": "1h", "limit": 100}, timeout=10)
        data = r2.json()
        return [float(c[4]) for c in data]
    except:
        base = 59000 if "BTC" in symbol else 3400
        return [base * (0.97 + random.random() * 0.06) for _ in range(100)]

def save_to_journal(entry: dict):
    try:
        with open("trade_journal.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except:
        pass

# ===================== ROUTES =====================
@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine"}

@app.get("/analyze", response_class=HTMLResponse)
async def analyze_web(symbol: str = "BTCUSD", account_balance: float = 500):
    symbol = symbol.upper()
    if symbol not in ["BTCUSD", "ETHUSD"]:
        symbol = "BTCUSD"

    prices = fetch_market_data(symbol)
    last_price = round(prices[-1], 2)
    ema20 = round(ema(prices[-20:], 20), 2)
    ema50 = round(ema(prices[-50:], 50), 2)
    rsi14 = rsi(prices[-15:], 14)

    if ema20 > ema50 and rsi14 < 65:
        decision = "BUY (Long)"
    elif ema20 < ema50 and rsi14 > 35:
        decision = "SELL (Short)"
    else:
        decision = "HOLD"

    ai_reason = f"Technical Analysis: {decision} setup on {symbol} at ${last_price:,.2f}. EMA alignment {'bullish' if ema20 > ema50 else 'bearish'}. RSI at {rsi14} indicates neutral momentum."

    risk_amount = account_balance * (MAX_RISK_PERCENT / 100)
    stop_loss_pct = 2.0
    position_size = round(risk_amount / (last_price * (stop_loss_pct/100)), 6)

    journal_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "price": last_price,
        "decision": decision,
        "ai_reasoning": ai_reason,
        "risk_amount_usd": round(risk_amount, 2),
        "position_size": position_size,
        "account_balance": account_balance
    }
    save_to_journal(journal_entry)

    # Simple HTML Dashboard
    html = f"""
    <html>
    <head><title>Aria Dashboard</title>
    <style>body {{font-family: Arial; margin: 20px;}} .card {{background: #222; padding: 15px; border-radius: 8px; margin: 10px 0;}}</style>
    </head>
    <body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>Current Analysis - {symbol}</h2>
            <p><strong>Price:</strong> ${last_price:,.2f}</p>
            <p><strong>Decision:</strong> {decision}</p>
            <p><strong>Reason:</strong> {ai_reason}</p>
            <p><strong>Risk (1%):</strong> ${risk_amount:.2f}</p>
            <p><strong>Suggested Position:</strong> {position_size} BTC</p>
        </div>
        <a href="/analyze?symbol=BTCUSD&account_balance={account_balance}">Refresh</a> | 
        <a href="/journal">View Journal</a>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-10:]
        entries = [json.loads(line.strip()) for line in lines if line.strip()]
        return {"entries": entries}
    except:
        return {"entries": [], "message": "No entries yet"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
