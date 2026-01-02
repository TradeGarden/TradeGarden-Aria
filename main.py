from fastapi import FastAPI, HTTPException
import requests
from datetime import datetime

app = FastAPI(title="Aria Crypto Engine", version="1.0")

# ======================
# HEALTH CHECK
# ======================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-phase-1",
        "time": datetime.utcnow().isoformat()
    }

# ======================
# CRYPTO ANALYSIS
# ======================
@app.post("/analyze")
def analyze_crypto(payload: dict):
    symbol = payload.get("symbol", "").upper()

    symbol_map = {
        "BTCUSD": "bitcoin",
        "ETHUSD": "ethereum"
    }

    if symbol not in symbol_map:
        raise HTTPException(
            status_code=400,
            detail="Supported symbols: BTCUSD, ETHUSD"
        )

    coin_id = symbol_map[symbol]

    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={coin_id}&vs_currencies=usd"
            "&include_24hr_change=true"
        )
        response = requests.get(url, timeout=10)
        data = response.json()

        price = data[coin_id]["usd"]
        change_24h = data[coin_id]["usd_24h_change"]

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch market data"
        )

    # ======================
    # SIMPLE LOGIC
    # ======================
    if change_24h > 1:
        bias = "bullish"
        action = "look for long entries"
    elif change_24h < -1:
        bias = "bearish"
        action = "look for short or wait"
    else:
        bias = "neutral"
        action = "no trade zone"

    return {
        "symbol": symbol,
        "price_usd": round(price, 2),
        "change_24h_percent": round(change_24h, 2),
        "bias": bias,
        "suggested_action": action,
        "analysis_time_utc": datetime.utcnow().isoformat()
    }
