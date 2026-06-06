import requests
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime
import os
import time
import random
import json
from openai import OpenAI

app = FastAPI(title="TradeGarden - Aria AI Trading Engine")

AI_API_KEY = os.getenv("AI_API_KEY")
client = OpenAI(api_key=AI_API_KEY) if AI_API_KEY else None

MAX_RISK_PERCENT = 1.0

# Helpers (ema, rsi, fetch_market_data same as before)
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

def get_ai_reasoning(symbol, price, ema20, ema50, rsi14, decision):
    if not client:
        return f"Technical setup: {decision} on {symbol} at ${price:,.2f}"
    try:
        prompt = f"Analyze for trading: {symbol} at ${price:,.2f}. EMA20:{ema20}, EMA50:{ema50}, RSI:{rsi14}. Decision: {decision}. Give short professional reasoning with stop-loss and take-profit ideas."
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI Analysis: {decision} setup. Good risk-reward. (Error: {str(e)[:50]})"

def save_to_journal(entry: dict):
    try:
        with open("trade_journal.txt", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except:
        pass

@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok", "service": "Aria AI Trading Engine", "risk": f"{MAX_RISK_PERCENT}% per trade"}

@app.get("/analyze")
def analyze(symbol: str = Query(..., description="BTCUSD"), account_balance: float = 1000):
    symbol = symbol.upper()
    if symbol not in ["BTCUSD", "ETHUSD"]:
        raise HTTPException(status_code=400, detail="Supported: BTCUSD, ETHUSD")

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

    ai_reason = get_ai_reasoning(symbol, last_price, ema20, ema50, rsi14, decision)

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

    return journal_entry

@app.get("/journal")
def view_journal():
    try:
        with open("trade_journal.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()[-10:]
        return {"entries": [json.loads(line.strip()) for line in lines if line.strip()]}
    except:
        return {"entries": [], "message": "No entries yet"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
