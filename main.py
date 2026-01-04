from fastapi import FastAPI, Query
import requests
from datetime import datetime

app = FastAPI(title="Aria Crypto – Layer 2A")

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

@app.get("/health")
def health():
    return {"status": "ok", "service": "aria-crypto-layer-2a"}

@app.get("/analyze")
def analyze(symbol: str = Query(..., description="BTCUSD or ETHUSD")):
    symbol = symbol.upper()

    if symbol not in ["BTCUSD", "ETHUSD"]:
        return {
            "detail": "Supported symbols: BTCUSD, ETHUSD"
        }

    coin_id = "bitcoin" if symbol == "BTCUSD" else "ethereum"

    r = requests.get(
        COINGECKO_URL,
        params={
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true"
        },
        timeout=10
    )

    if r.status_code != 200:
        return {"detail": "Failed to fetch market data"}

    data = r.json()[coin_id]

    return {
        "symbol": symbol,
        "price_usd": round(data["usd"], 2),
        "change_24h_percent": round(data["usd_24h_change"], 2),
        "bias": "bullish" if data["usd_24h_change"] > 0 else "bearish",
        "analysis_time_utc": datetime.utcnow().isoformat()
    }
