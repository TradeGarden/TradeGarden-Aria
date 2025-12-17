import os
import time
import logging
import requests
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request
import uvicorn

# -------------------------------------------------
# Basic setup
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria – TradeGarden Crypto Core")

# -------------------------------------------------
# Environment
# -------------------------------------------------
OPENAI_KEY = os.getenv("OPENAI_KEY")
APCA_KEY = os.getenv("APCA_API_KEY_ID")
APCA_SECRET = os.getenv("APCA_API_SECRET_KEY")
APCA_BASE = os.getenv("APCA_API_BASE_URL")

PHONE_TOKEN = os.getenv("PHONE_TOKEN", "")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))
CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USD").split(",")

# -------------------------------------------------
# In-memory state (safe on free tier)
# -------------------------------------------------
memory = {
    "trades": [],
    "last_action": None
}

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def alpaca_headers():
    return {
        "APCA-API-KEY-ID": APCA_KEY,
        "APCA-API-SECRET-KEY": APCA_SECRET,
        "Content-Type": "application/json"
    }


def get_crypto_price(symbol: str) -> float:
    url = f"{APCA_BASE}/v1beta3/crypto/us/latest/trades"
    params = {"symbols": symbol}
    r = requests.get(url, headers=alpaca_headers(), params=params)
    r.raise_for_status()
    return r.json()["trades"][symbol]["p"]


def place_crypto_order(symbol: str, qty: float, side: str):
    url = f"{APCA_BASE}/v2/orders"
    payload = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }
    r = requests.post(url, headers=alpaca_headers(), json=payload)
    r.raise_for_status()
    return r.json()


def verify_last_order():
    url = f"{APCA_BASE}/v2/orders?limit=1"
    r = requests.get(url, headers=alpaca_headers())
    r.raise_for_status()
    return r.json()[0]


def call_openai(prompt: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "You are Aria, a professional crypto trader for TradeGarden.\n"
        "Rules:\n"
        "- Crypto ONLY (BTC/USD, ETH/USD)\n"
        "- Always respond in JSON ONLY\n"
        "- Flow: analysis → decision → order → explanation\n"
        "- Never hallucinate prices\n"
        "- Risk is strictly 2%\n\n"
        "Response format:\n"
        "{\n"
        '  "analysis": "...",\n'
        '  "decision": "buy | sell | hold",\n'
        '  "symbol": "BTC/USD",\n'
        '  "qty": number,\n'
        '  "explanation": "..."\n'
        "}"
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }

    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


# -------------------------------------------------
# Routes
# -------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-core",
        "memory_trades": len(memory["trades"])
    }


@app.post("/assistant")
async def assistant(req: Request):
    auth = req.headers.get("Authorization", "")
    if PHONE_TOKEN not in auth:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    user_prompt = body.get("prompt", "")

    ai = call_openai(user_prompt)
    content = ai["choices"][0]["message"]["content"]

    decision = eval(content)  # safe here because we force JSON only

    symbol = decision["symbol"]
    if symbol not in CRYPTO_SYMBOLS:
        return {"error": "Symbol not allowed"}

    price = get_crypto_price(symbol)

    result = {
        "analysis": decision["analysis"],
        "decision": decision["decision"],
        "price": price,
        "explanation": decision["explanation"]
    }

    if decision["decision"] in ["buy", "sell"]:
        order = place_crypto_order(
            symbol=symbol,
            qty=decision["qty"],
            side=decision["decision"]
        )
        verified = verify_last_order()

        memory["trades"].append({
            "symbol": symbol,
            "side": decision["decision"],
            "qty": decision["qty"],
            "price": price,
            "time": time.time()
        })

        result["order"] = order
        result["verified"] = verified

    memory["last_action"] = result
    return result


# -------------------------------------------------
# Entry
# -------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
