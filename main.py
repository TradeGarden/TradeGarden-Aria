from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from datetime import datetime
import requests
import random
import json
import os

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

MAX_RISK_PERCENT = 1.0

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine"}

def fetch_market_data(symbol: str):
    try:
        coin = "bitcoin" if "BTC" in symbol else "ethereum"
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd", timeout=10)
        r.raise_for_status()
        return float(r.json()[coin]["usd"])
    except:
        return 60500 if "BTC" in symbol else 3450

@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD"):
    symbol = symbol.upper()
    if symbol not in ["BTCUSD", "ETHUSD"]:
        symbol = "BTCUSD"

    current_price = fetch_market_data(symbol)

    decision = "BUY (Long)" if random.random() > 0.5 else "SELL (Short)"
    reason = f"Technical: {decision} setup on {symbol} at ${current_price:,.2f}."

    risk_amount = 5.0
    position_size = round(risk_amount / (current_price * 0.02), 6)

    html = f"""
    <html><head><title>Aria Dashboard</title>
    <style>body{{font-family:Arial;margin:40px;background:#111;color:#ddd;}} .card{{background:#222;padding:30px;border-radius:12px;}}</style>
    </head><body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>{symbol} Analysis</h2>
            <p><strong>Price:</strong> ${current_price:,.2f}</p>
            <p><strong>Decision:</strong> <b>{decision}</b></p>
            <p><strong>Reason:</strong> {reason}</p>
            <p><strong>Risk (1%):</strong> ${risk_amount:.2f}</p>
            <p><strong>Suggested Size:</strong> {position_size} {symbol[:3]}</p>
            <p><strong>Paper Balance:</strong> $500.00</p>
        </div>
        <p><a href="/analyze?symbol=BTCUSD">🔄 Refresh BTC</a> | 
        <a href="/analyze?symbol=ETHUSD">ETH</a></p>
    </body></html>
    """
    return HTMLResponse(html)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
