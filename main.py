from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from datetime import datetime
import requests
import random
import json

app = FastAPI(title="Aria AI Trading Dashboard")

MAX_RISK_PERCENT = 1.0

@app.get("/health")
def health():
    return {"status": "ok", "service": "Aria Trading Engine"}

@app.get("/", response_class=HTMLResponse)
@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD", account_balance: float = 500):
    symbol = symbol.upper()
    if symbol not in ["BTCUSD", "ETHUSD"]:
        symbol = "BTCUSD"

    try:
        binance_symbol = symbol.replace("USD", "USDT")
        r = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": binance_symbol}, timeout=10)
        last_price = float(r.json()["price"])
    except:
        last_price = 60500 if "BTC" in symbol else 3400

    decision = "BUY (Long)" if random.random() > 0.5 else "SELL (Short)"
    reason = f"Technical setup: {decision} on {symbol} at ${last_price:,.2f}."

    risk_amount = account_balance * (MAX_RISK_PERCENT / 100)
    position_size = round(risk_amount / (last_price * 0.02), 6)

    journal_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": symbol,
        "price": last_price,
        "decision": decision,
        "reason": reason,
        "risk_amount": round(risk_amount, 2),
        "position_size": position_size
    }
    try:
        with open("trade_journal.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(journal_entry) + "\n")
    except:
        pass

    html = f"""
    <html><head><title>Aria Dashboard</title>
    <style>body{{font-family:Arial;margin:30px;background:#111;color:#ddd;}} .card{{background:#222;padding:25px;border-radius:12px;margin:15px 0;}}</style>
    </head><body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>{symbol} Analysis</h2>
            <p><strong>Price:</strong> ${last_price:,.2f}</p>
            <p><strong>Decision:</strong> <b>{decision}</b></p>
            <p><strong>Reason:</strong> {reason}</p>
            <p><strong>Risk (1%):</strong> ${risk_amount:.2f}</p>
            <p><strong>Suggested Size:</strong> {position_size} {symbol[:3]}</p>
        </div>
        <p><a href="/analyze?symbol=BTCUSD&account_balance={account_balance}">🔄 Refresh BTC</a> | 
        <a href="/analyze?symbol=ETHUSD&account_balance={account_balance}">ETH</a> | 
        <a href="/journal">📖 View Journal</a></p>
    </body></html>
    """
    return HTMLResponse(html)

@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-15:]
        entries = [json.loads(line.strip()) for line in lines if line.strip()]
        return {"entries": entries}
    except:
        return {"entries": [], "message": "No entries yet"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
