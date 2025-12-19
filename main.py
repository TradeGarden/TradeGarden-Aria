import os
import time
import logging
import requests
from fastapi import FastAPI, HTTPException, Request
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Aria – TradeGarden Crypto Core")

# ===== ENV =====
APCA_API_BASE_URL = os.getenv("APCA_API_BASE_URL")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
PHONE_TOKEN = os.getenv("PHONE_TOKEN")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))
CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD").split(",")

# Alpaca crypto data endpoint (correct)
CRYPTO_DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us/latest/bars"

HEADERS_ALPACA = {
    "APCA-API-KEY-ID": APCA_API_KEY_ID,
    "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
}

# ===== MEMORY (IN-MEMORY FOR NOW) =====
memory = {
    "trades": []
}

# ===== HEALTH =====
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK
    }

# ===== PRICE FETCH =====
def get_crypto_price(symbol: str) -> float:
    r = requests.get(
        CRYPTO_DATA_URL,
        headers=HEADERS_ALPACA,
        params={"symbols": symbol},
        timeout=10
    )
    r.raise_for_status()
    data = r.json()
    return data["bars"][symbol]["c"]

# ===== AI =====
def call_openai(prompt: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Aria, a professional crypto trader.\n"
                    "Crypto ONLY (BTC/USD, ETH/USD).\n"
                    "You must ALWAYS return JSON.\n"
                    "Flow: analysis -> decision -> explanation.\n"
                    "If no trade, return action=analysis.\n"
                    "If trade, return action=order with symbol, side, qty, explanation."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=20
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# ===== ORDER =====
def place_crypto_order(symbol: str, side: str, qty: float):
    url = f"{APCA_API_BASE_URL}/v2/orders"
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "qty": qty,
        "time_in_force": "gtc"
    }

    r = requests.post(url, headers=HEADERS_ALPACA, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

# ===== ASSISTANT =====
@app.post("/assistant")
async def assistant(req: Request):
    if req.headers.get("Authorization") != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "")

    ai_raw = call_openai(prompt)

    try:
        import json
        ai = json.loads(ai_raw)
    except Exception:
        raise HTTPException(status_code=500, detail="AI returned invalid JSON")

    if ai["action"] == "analysis":
        return ai

    if ai["action"] == "order":
        symbol = ai["symbol"]
        side = ai["side"]
        qty = float(ai["qty"])

        if symbol not in CRYPTO_SYMBOLS:
            raise HTTPException(status_code=400, detail="Symbol not allowed")

        price = get_crypto_price(symbol)
        order = place_crypto_order(symbol, side, qty)

        memory["trades"].append({
            "time": time.time(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "reason": ai.get("explanation", "")
        })

        return {
            "action": "placed",
            "symbol": symbol,
            "price": price,
            "order": order,
            "explanation": ai.get("explanation")
        }

    return ai
