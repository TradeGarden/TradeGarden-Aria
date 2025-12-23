from fastapi import FastAPI, Request, Header, HTTPException
import os
import requests
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# ENV
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
ALPACA_BASE = os.getenv("APCA_API_BASE_URL")

PHONE_TOKEN = os.getenv("PHONE_TOKEN")

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type": "application/json"
}

CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD"]
MAX_RISK = 0.02


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK
    }


@app.post("/trade")
async def trade(request: Request, authorization: str = Header(None)):
    if authorization != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    symbol = body.get("symbol", "BTCUSD")
    side = body.get("side", "buy")
    notional = body.get("notional", 10)

    if symbol not in CRYPTO_SYMBOLS:
        raise HTTPException(status_code=400, detail="Invalid crypto symbol")

    order_payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "notional": notional,
        "time_in_force": "gtc"
    }

    try:
        response = requests.post(
            f"{ALPACA_BASE}/v2/orders",
            json=order_payload,
            headers=HEADERS,
            timeout=10
        )

        if response.status_code >= 400:
            return {
                "error": "Alpaca rejected order",
                "alpaca_status": response.status_code,
                "alpaca_response": response.text
            }

        return {
            "status": "order_submitted",
            "order": response.json()
        }

    except Exception as e:
        return {
            "error": "Internal exception",
            "detail": str(e)
        }
