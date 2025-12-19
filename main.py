import os
import json
import logging
import requests
from fastapi import FastAPI, Request, HTTPException
import uvicorn
import re

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria – TradeGarden Crypto Core")

# ========= ENV =========
ALPACA_BASE_URL = os.getenv("APCA_API_BASE_URL")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
AUTH_TOKEN = os.getenv("PHONE_TOKEN")

CRYPTO_SYMBOLS = os.getenv("CRYPTO_SYMBOLS", "BTC/USD").split(",")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))

# ========= VALIDATION =========
if not all([ALPACA_BASE_URL, ALPACA_KEY, ALPACA_SECRET, OPENAI_KEY, AUTH_TOKEN]):
    raise RuntimeError("Missing required environment variables")

# ========= HELPERS =========
def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }

def extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("No JSON found in AI response")
    return json.loads(match.group())

# ========= ROUTES =========
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK
    }

@app.post("/assistant")
async def assistant(req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "")

    # ===== OpenAI Request =====
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Aria, a professional crypto trader.\n"
                    "You ONLY trade crypto.\n"
                    "Return JSON ONLY.\n\n"
                    "If trading:\n"
                    "{"
                    "\"analysis\": \"...\","
                    "\"decision\": \"buy or sell\","
                    "\"symbol\": \"BTC/USD\","
                    "\"qty\": 0.001,"
                    "\"explanation\": \"...\""
                    "}\n\n"
                    "If no trade:\n"
                    "{"
                    "\"analysis\": \"...\","
                    "\"decision\": \"no_trade\","
                    "\"explanation\": \"...\""
                    "}"
                )
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }

    ai = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )
    ai.raise_for_status()

    raw = ai.json()["choices"][0]["message"]["content"]

    try:
        data = extract_json(raw)
    except Exception as e:
        logging.error(raw)
        return {"error": "AI response parse failed", "raw": raw}

    # ===== No Trade =====
    if data.get("decision") == "no_trade":
        return data

    # ===== Trade =====
    symbol = data.get("symbol")
    qty = float(data.get("qty", 0))
    side = data.get("decision")

    if symbol not in CRYPTO_SYMBOLS:
        return {"error": "Symbol not allowed", "symbol": symbol}

    order = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }

    resp = requests.post(
        f"{ALPACA_BASE_URL}/v2/orders",
        headers=alpaca_headers(),
        json=order,
        timeout=30
    )
    resp.raise_for_status()

    return {
        "analysis": data["analysis"],
        "decision": side,
        "order": resp.json(),
        "explanation": data["explanation"]
    }

# ========= START =========
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
