import os
import json
import requests
from fastapi import FastAPI, Header, HTTPException
from dotenv import load_dotenv

# Load environment variables (Render + local)
load_dotenv()

# =========================
# ENVIRONMENT VARIABLES
# =========================
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not ALPACA_API_KEY or not ALPACA_SECRET or not OPENAI_API_KEY:
    raise RuntimeError("Missing required environment variables")

# =========================
# APP SETUP
# =========================
app = FastAPI()

AUTH_TOKEN = "aria-phone-2025"

# =========================
# HEALTH CHECK
# =========================
@app.get("/health")
def health():
    return {"status": "ok"}

# =========================
# HELPER FUNCTIONS
# =========================
def get_btc_price():
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/bars?symbols=BTC/USD"
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()["bars"]["BTC/USD"]["c"]

def place_crypto_order(side: str, notional: float):
    url = "https://paper-api.alpaca.markets/v2/orders"
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }
    payload = {
        "symbol": "BTC/USD",
        "notional": notional,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }
    r = requests.post(url, headers=headers, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def call_openai(prompt: str, price: float):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = (
        "You are Aria, a crypto trading AI. "
        "Respond ONLY with valid JSON. "
        "Schema:\n"
        "{\n"
        '  "decision": "buy | sell | hold",\n'
        '  "notional": number,\n'
        '  "explanation": string\n'
        "}\n"
        "Do NOT include markdown or text outside JSON."
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{prompt}\nBTC price: {price}"}
        ],
        "temperature": 0.2
    }

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# =========================
# MAIN ASSISTANT ENDPOINT
# =========================
@app.post("/assistant")
def assistant(
    body: dict,
    authorization: str = Header(None)
):
    # Auth check
    if authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    prompt = body.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    # Get BTC price
    price = get_btc_price()

    # Ask AI
    ai_raw = call_openai(prompt, price)

    # SAFE JSON PARSE (NO eval)
    try:
        decision = json.loads(ai_raw)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=f"AI returned invalid JSON: {ai_raw}"
        )

    action = decision.get("decision")
    notional = float(decision.get("notional", 0))
    explanation = decision.get("explanation", "")

    response = {
        "price": price,
        "decision": action,
        "explanation": explanation
    }

    # Execute trade only if valid
    if action in ["buy", "sell"] and notional > 0:
        order = place_crypto_order(action, notional)
        response["order"] = order

    return response
