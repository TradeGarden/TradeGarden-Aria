import os
import requests
from fastapi import FastAPI, Request, HTTPException

# =========================
# ENV VARIABLES
# =========================
ALPACA_BASE_URL = os.getenv("APCA_API_BASE_URL")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")

OPENAI_KEY = os.getenv("OPENAI_KEY")
AUTH_TOKEN = os.getenv("PHONE_TOKEN")

if not all([ALPACA_BASE_URL, ALPACA_KEY, ALPACA_SECRET, OPENAI_KEY, AUTH_TOKEN]):
    raise RuntimeError("Missing environment variables")

# =========================
# APP INIT
# =========================
app = FastAPI(title="Aria Crypto Trader")

CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]
RISK = 0.02

# =========================
# HEALTH CHECK
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": RISK
    }

# =========================
# ALPACA HELPERS
# =========================
def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }

def get_crypto_price(symbol: str):
    url = f"{ALPACA_BASE_URL}/v1beta3/crypto/us/latest/trades"
    r = requests.get(url, headers=alpaca_headers(), params={"symbols": symbol}, timeout=15)
    r.raise_for_status()
    return r.json()["trades"][symbol]["p"]

def place_crypto_order(symbol: str, qty: float, side: str):
    url = f"{ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "gtc"
    }
    r = requests.post(url, headers=alpaca_headers(), json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

# =========================
# ASSISTANT (DEBUG SAFE)
# =========================
@app.post("/assistant")
async def assistant(req: Request):
    try:
        # ---- AUTH ----
        auth = req.headers.get("Authorization", "")
        if auth != f"Bearer {AUTH_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")

        body = await req.json()
        prompt = body.get("prompt", "").lower()

        # ---- FORCE CRYPTO ONLY ----
        symbol = "BTC/USD" if "btc" in prompt else "ETH/USD"

        price = get_crypto_price(symbol)

        analysis = f"{symbol} live price is ${price:.2f}. Market is volatile."

        decision = "hold"
        if "buy" in prompt:
            decision = "buy"
        elif "sell" in prompt:
            decision = "sell"

        order_result = None
        if decision in ["buy", "sell"]:
            qty = round((100 * RISK) / price, 6)
            order_result = place_crypto_order(symbol, qty, decision)

        return {
            "analysis": analysis,
            "decision": decision,
            "order": order_result,
            "explanation": "Crypto-only execution with live Alpaca verification."
        }

    except Exception as e:
        return {
            "error": str(e),
            "type": str(type(e))
        }
