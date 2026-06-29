from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import requests
import random
import json
import os
from openai import OpenAI

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

# CORS added here
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows localhost:3000 and your React app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AI_API_KEY = os.getenv("AI_API_KEY")
client = OpenAI(api_key=AI_API_KEY) if AI_API_KEY and AI_API_KEY.startswith("sk-") else None

MAX_RISK_PERCENT = 1.0

def load_balance():
    try:
        if os.path.exists("paper_balance.txt"):
            with open("paper_balance.txt", "r") as f:
                return float(f.read().strip())
    except:
        pass
    return 500.0

def save_balance(balance):
    try:
        with open("paper_balance.txt", "w") as f:
            f.write(str(round(balance, 2)))
    except:
        pass

def load_position():
    try:
        if os.path.exists("paper_position.json"):
            with open("paper_position.json", "r") as f:
                return json.load(f)
    except:
        pass
    return None

def save_position(position):
    try:
        with open("paper_position.json", "w") as f:
            json.dump(position, f)
    except:
        pass

def fetch_market_data(symbol: str):
    try:
        coin = "bitcoin" if "BTC" in symbol else "ethereum"
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd", timeout=12)
        r.raise_for_status()
        return float(r.json()[coin]["usd"])
    except:
        return 64500 if "BTC" in symbol else 3450

def get_ai_reasoning(symbol, price, decision):
    if not client:
        return f"Technical: {decision} setup on {symbol} at ${price:,.2f}."
    try:
        prompt = f"Short professional trading reasoning for {symbol} at ${price:,.2f}. Decision: {decision}."
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except:
        return f"Technical: {decision} setup on {symbol} at ${price:,.2f}."

def save_to_journal(entry: dict):
    try:
        with open("trade_journal.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except:
        pass

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine"}

@app.get("/analyze", response_class=HTMLResponse)
async def dashboard(symbol: str = "BTCUSD"):
    balance = load_balance()
    position = load_position()
    symbol = symbol.upper()
    if symbol not in ["BTCUSD", "ETHUSD"]:
        symbol = "BTCUSD"

    current_price = fetch_market_data(symbol)

    pl = 0
    pl_percent = 0
    if position and position["symbol"] == symbol:
        entry_price = position["entry_price"]
        size = position["size"]
        pl = (current_price - entry_price) * size if position["side"] == "BUY" else (entry_price - current_price) * size
        pl_percent = (pl / position["risk_amount"]) * 100 if position["risk_amount"] > 0 else 0

    decision = "BUY (Long)" if random.random() > 0.5 else "SELL (Short)"
    ai_reason = get_ai_reasoning(symbol, current_price, decision)

    risk_amount = balance * (MAX_RISK_PERCENT / 100)
    position_size = round(risk_amount / (current_price * 0.02), 6)

    html = f"""
    <html><head><title>Aria Dashboard</title>
    <style>body{{font-family:Arial;margin:30px;background:#111;color:#ddd;}} .card{{background:#222;padding:25px;border-radius:12px;margin:15px 0;}}</style>
    </head><body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>{symbol} Analysis</h2>
            <p><strong>Price:</strong> ${current_price:,.2f}</p>
            <p><strong>Decision:</strong> <b>{decision}</b></p>
            <p><strong>AI Reason:</strong> {ai_reason}</p>
            <p><strong>Risk (1%):</strong> ${risk_amount:.2f}</p>
            <p><strong>Suggested Size:</strong> {position_size} {symbol[:3]}</p>
            <p><strong>Paper Balance:</strong> ${balance:,.2f}</p>
        </div>
        {f'<div class="card"><h3>🟢 Open Position</h3><p>Side: {position["side"]}</p><p>Entry: ${position["entry_price"]}</p><p>P/L: ${pl:.2f} ({pl_percent:.1f}%)</p><a href="/close">Close Position</a></div>' if position else '<div class="card"><p>No open position</p></div>'}
        <p>
            <a href="/execute?symbol={symbol}&side=BUY">🟢 Execute BUY</a> | 
            <a href="/execute?symbol={symbol}&side=SELL">🔴 Execute SELL</a><br>
            <a href="/analyze?symbol=BTCUSD">🔄 Refresh BTC</a> | 
            <a href="/analyze?symbol=ETHUSD">ETH</a> | 
            <a href="/journal">📖 View Journal</a>
        </p>
    </body></html>
    """
    return HTMLResponse(html)

@app.get("/execute")
async def execute_trade(symbol: str = Query(...), side: str = Query(...)):
    balance = load_balance()
    current_price = fetch_market_data(symbol)
    risk_amount = balance * (MAX_RISK_PERCENT / 100)
    size = round(risk_amount / (current_price * 0.02), 6)

    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": current_price,
        "size": size,
        "risk_amount": risk_amount,
        "timestamp": datetime.utcnow().isoformat()
    }
    save_position(position)
    save_to_journal({"action": "EXECUTE_TRADE", "symbol": symbol, "side": side, "price": current_price, "size": size, "timestamp": datetime.utcnow().isoformat()})

    return HTMLResponse(f"<h2>✅ Paper Trade Executed: {side} {size} {symbol} at ${current_price:,.2f}</h2><p><a href='/analyze'>← Back to Dashboard</a></p>")

@app.get("/close")
async def close_position():
    position = load_position()
    if position:
        save_position(None)
        save_to_journal({"action": "CLOSE_POSITION", "symbol": position["symbol"], "timestamp": datetime.utcnow().isoformat()})
        return HTMLResponse(f"<h2>✅ Position Closed</h2><p><a href='/analyze'>← Back to Dashboard</a></p>")
    return HTMLResponse("<h2>No position to close</h2><p><a href='/analyze'>Back</a></p>")

@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-30:]
        entries = [json.loads(line.strip()) for line in lines if line.strip()]
        return {"entries": entries}
    except:
        return {"entries": [], "message": "No entries yet"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
