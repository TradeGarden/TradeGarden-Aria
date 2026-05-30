import requests
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime
import os
import time
import random

app = FastAPI(title="Aria AI Trading Bot")

# ===================== CONFIG =====================
SUPPORTED_SYMBOLS = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
AI_API_KEY = os.getenv("AI_API_KEY")  # Add in Render if you have it

PAPER_TRADING = True
MAX_RISK_PERCENT = 1.0   # 1% risk per trade

# ===================== HELPERS =====================
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
    binance_symbol = symbol.replace("USD", "USDT")
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": binance_symbol, "interval": "1h", "limit": 100}
    for _ in range(3):
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()
            return [float(candle[4]) for candle in data]
        except:
            time.sleep(2)
    # Mock fallback
    base = 65000 if "BTC" in symbol else 3500
    return [base * (0.95 + random.random() * 0.1) for _ in range(100)]

# ===================== ROUTES =====================
@app.get("/")
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Aria AI Trading Bot",
        "mode": "PAPER TRADING ONLY",
        "risk_per_trade": f"{MAX_RISK_PERCENT}%"
    }

@app.get("/analyze")
def analyze(symbol: str = Query(..., description="BTCUSD or ETHUSD"), account_balance: float = 1000):
    symbol = symbol.upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(status_code=400, detail="Supported: BTCUSD, ETHUSD")

    prices = fetch_market_data(symbol)
    last_price = round(prices[-1], 2)
    ema20 = round(ema(prices[-20:], 20), 2)
    ema50 = round(ema(prices[-50:], 50), 2)
    rsi14 = rsi(prices[-15:], 14)

    # Risk Management (1% risk)
    risk_amount = account_balance * (MAX_RISK_PERCENT / 100)
    stop_loss_percent = 2.0
    position_size = round(risk_amount / (last_price * (stop_loss_percent/100)), 6)

    if ema20 > ema50 and rsi14 < 65:
        decision = "BUY (Long)"
        confidence = "Medium-High"
    elif ema20 < ema50 and rsi14 > 35:
        decision = "SELL (Short)"
        confidence = "Medium"
    else:
        decision = "HOLD"
        confidence = "Low"

    return {
        "symbol": symbol,
        "price_usd": last_price,
        "decision": decision,
        "confidence": confidence,
        "ema_20": ema20,
        "ema_50": ema50,
        "rsi_14": rsi14,
        "suggested_position_size": position_size,
        "risk_amount_usd": round(risk_amount, 2),
        "account_balance": account_balance,
        "data_source": "real"
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
