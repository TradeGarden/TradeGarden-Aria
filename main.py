import requests
from fastapi import FastAPI, HTTPException, Query
from datetime import datetime

app = FastAPI(title="Aria Crypto – Layer 2A")

# =====================
# CONFIG
# =====================
COINGECKO_API = "https://api.coingecko.com/api/v3"
SUPPORTED_SYMBOLS = {
    "BTCUSD": "bitcoin",
    "ETHUSD": "ethereum"
}

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14


# =====================
# HELPERS
# =====================
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


def fetch_market_data(coin_id):
    url = f"{COINGECKO_API}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": 2, "interval": "hourly"}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return [p[1] for p in r.json()["prices"]]


# =====================
# ROUTES
# =====================
@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto-layer-2A",
        "supported_symbols": list(SUPPORTED_SYMBOLS.keys())
    }


@app.get("/analyze")
def analyze(symbol: str = Query(..., description="BTCUSD or ETHUSD")):
    symbol = symbol.upper()

    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail="Supported symbols: BTCUSD, ETHUSD"
        )

    try:
        prices = fetch_market_data(SUPPORTED_SYMBOLS[symbol])
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch market data")

    if len(prices) < 60:
        raise HTTPException(status_code=500, detail="Insufficient price data")

    ema20 = round(ema(prices[-EMA_FAST:], EMA_FAST), 2)
    ema50 = round(ema(prices[-EMA_SLOW:], EMA_SLOW), 2)
    rsi14 = rsi(prices[-(RSI_PERIOD + 1):], RSI_PERIOD)
    last_price = round(prices[-1], 2)

    # =====================
    # DECISION ENGINE
    # =====================
    if ema20 > ema50 and 45 <= rsi14 <= 65:
        bias = "bullish"
        decision = "look for long entries"
        reason = "Uptrend confirmed by EMA alignment and healthy RSI momentum"
    elif ema20 < ema50 and 35 <= rsi14 <= 55:
        bias = "bearish"
        decision = "look for short entries"
        reason = "Downtrend confirmed by EMA alignment and weak momentum"
    else:
        bias = "neutral"
        decision = "wait"
        reason = "Market conditions are unclear or overextended"

    return {
        "symbol": symbol,
        "price_usd": last_price,
        "ema_20": ema20,
        "ema_50": ema50,
        "rsi_14": rsi14,
        "bias": bias,
        "decision": decision,
        "reason": reason,
        "analysis_time_utc": datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
