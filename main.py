from fastapi import FastAPI, HTTPException
from datetime import datetime, timezone
import requests
import os

app = FastAPI(title="Aria Crypto – Layer 2A")

SUPPORTED_SYMBOLS = ["BTCUSD", "ETHUSD"]

COINGECKO_IDS = {
    "BTCUSD": "bitcoin",
    "ETHUSD": "ethereum"
}

# ---------- UTILITIES ----------

def fetch_market_data(symbol: str):
    coin_id = COINGECKO_IDS[symbol]
    url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{coin_id}/market_chart?vs_currency=usd&days=2"
    )

    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        raise Exception("Failed to fetch market data")

    prices = r.json()["prices"]
    closes = [p[1] for p in prices]

    return closes[-1], closes


def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff >= 0:
            gains.append(diff)
        else:
            losses.append(abs(diff))

    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_ema(closes, period=20):
    if len(closes) < period:
        return None

    k = 2 / (period + 1)
    ema = closes[0]

    for price in closes:
        ema = price * k + ema * (1 - k)

    return round(ema, 2)


def build_confidence(rsi, price, ema, change_24h):
    score = 0

    if rsi and 55 <= rsi <= 70:
        score += 30
    elif rsi and rsi > 70:
        score += 20

    if ema and price > ema:
        score += 30

    if change_24h > 0:
        score += 20

    return min(score, 100)


# ---------- ROUTES ----------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto-layer2A",
        "symbols": SUPPORTED_SYMBOLS
    }


@app.post("/analyze")
def analyze(symbol: str):
    symbol = symbol.upper()

    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail="Supported symbols: BTCUSD, ETHUSD"
        )

    try:
        price, closes = fetch_market_data(symbol)

        price_24h_ago = closes[0]
        change_24h = round(
            ((price - price_24h_ago) / price_24h_ago) * 100, 2
        )

        rsi = calculate_rsi(closes)
        ema = calculate_ema(closes)

        bias = "bullish" if change_24h > 0 else "bearish"
        ema_trend = "up" if ema and price > ema else "down"

        confidence = build_confidence(
            rsi=rsi,
            price=price,
            ema=ema,
            change_24h=change_24h
        )

        confirmed_signal = (
            "LONG_BIAS"
            if bias == "bullish" and confidence >= 60
            else "NO_TRADE"
        )

        return {
            "symbol": symbol,
            "price_usd": round(price, 2),
            "change_24h_percent": change_24h,
            "rsi": rsi,
            "ema_trend": ema_trend,
            "bias": bias,
            "confidence": confidence,
            "confirmed_signal": confirmed_signal,
            "analysis_time_utc": datetime.now(timezone.utc).isoformat()
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )
