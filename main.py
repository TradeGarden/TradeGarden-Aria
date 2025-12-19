import os
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

# ========================
# ENV VARIABLES
# ========================
ALPACA_BASE_URL = os.getenv("APCA_API_BASE_URL")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
AUTH_TOKEN = os.getenv("PHONE_TOKEN")

CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD").split(",")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))

# ========================
# HEALTH CHECK
# ========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK
    }

# ========================
# HELPER: GET CRYPTO PRICE
# ========================
def get_crypto_price(symbol: str):
    url = f"{ALPACA_BASE_URL}/v2/crypto/latest/trades"
    r = requests.get(
        url,
        headers={
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET
        },
        params={"symbols": symbol},
        timeout=10
    )

    data = r.json()
    return data["trades"][symbol]["p"]

# ========================
# HELPER: PLACE ORDER
# ========================
def place_crypto_order(symbol, qty, side):
    url = f"{ALPACA_BASE_URL}/v2/orders"

    payload = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }

    r = requests.post(
        url,
        json=payload,
        headers={
            "APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET
        },
        timeout=10
    )

    return r.json()

# ========================
# ASSISTANT ENDPOINT
# ========================
@app.post("/assistant")
async def assistant(req: Request):
    try:
        # ---- AUTH ----
        auth = req.headers.get("Authorization", "")
        if auth != f"Bearer {AUTH_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")

        body = await req.json()
        prompt = body.get("prompt", "").strip()

        if not prompt:
            raise HTTPException(status_code=400, detail="Empty prompt")

        # ---- GET BTC PRICE ----
        price = get_crypto_price("BTC/USD")

        # ---- AI ANALYSIS ----
        ai_payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Aria, a crypto-only trading assistant. "
                        "You must respond in this order:\n"
                        "1. Market analysis\n"
                        "2. Trade decision (buy/sell/hold)\n"
                        "3. Risk explanation\n"
                        "DO NOT claim a trade is placed unless instructed."
                    )
                },
                {"role": "user", "content": f"{prompt}\nBTC price: {price}"}
            ]
        }

        ai = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json"
            },
            json=ai_payload,
            timeout=30
        )

        ai_json = ai.json()
        response_text = ai_json["choices"][0]["message"]["content"]

        # ---- OPTIONAL TRADE ----
        trade_result = None
        if "buy" in prompt.lower():
            trade_result = place_crypto_order("BTC/USD", 0.001, "buy")

        return {
            "price": price,
            "analysis": response_text,
            "trade": trade_result
        }

    except Exception as e:
        return {
            "error": str(e),
            "type": str(type(e))
        }
        
