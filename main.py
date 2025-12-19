import os
from fastapi import FastAPI, Request, HTTPException
import requests

app = FastAPI()

AUTH_TOKEN = os.getenv("PHONE_TOKEN")

# Only crypto symbols allowed
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS
    }

@app.post("/assistant")
async def assistant(req: Request):
    auth = req.headers.get("Authorization")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "").lower()

    # HARD BLOCK stocks
    banned = ["aapl", "tsla", "stock", "shares", "equity"]
    if any(word in prompt for word in banned):
        return {
            "error": "Aria is crypto-only. Stocks are disabled."
        }

    return {
        "stage": "analysis",
        "message": "Crypto-only mode confirmed. Ready for BTC analysis.",
        "received": body
    }
