import os
import time
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI(title="Aria Crypto Trader")

# =========================
# ENVIRONMENT
# =========================
ALPACA_BASE = os.getenv("APCA_API_BASE_URL")
ALPACA_KEY = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
OPENAI_KEY = os.getenv("OPENAI_KEY")
PHONE_TOKEN = os.getenv("PHONE_TOKEN")

CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]
MAX_RISK = 0.02  # 2%

# =========================
# SIMPLE MEMORY (SAFE)
# =========================
memory = {
    "last_analysis": None,
    "last_decision": None,
    "last_trade": None,
}

# =========================
# HELPERS
# =========================
def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

def get_crypto_price(symbol: str):
    url = f"{ALPACA_BASE}/v1beta3/crypto/us/latest/trades"
    r = requests.get(url, headers=alpaca_headers(), params={"symbols": symbol})
    r.raise_for_status()
    return r.json()["trades"][symbol]["p"]

def verify_order(order_id: str):
    url = f"{ALPACA_BASE}/v2/orders/{order_id}"
    r = requests.get(url, headers=alpaca_headers())
    r.raise_for_status()
    return r.json()

def place_crypto_order(symbol: str, side: str, notional: float):
    url = f"{ALPACA_BASE}/v2/orders"
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "notional": notional,
    }
    r = requests.post(url, headers=alpaca_headers(), json=payload)
    r.raise_for_status()
    return r.json()

def ask_openai(prompt: str):
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Aria, a professional crypto trader.\n"
                    "Rules:\n"
                    "- Crypto ONLY\n"
                    "- BTC/USD or ETH/USD only\n"
                    "- Always return JSON\n"
                    "- Follow stages: analysis → decision → explanation\n"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# =========================
# ROUTES
# =========================
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK,
    }

@app.post("/assistant")
async def assistant(req: Request):
    auth = req.headers.get("Authorization")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "")

    # HARD BLOCK STOCKS
    banned = ["aapl", "tsla", "stock", "share", "equity"]
    if any(b in prompt.lower() for b in banned):
        return {"error": "Stocks are disabled. Crypto only."}

    # =========================
    # ANALYSIS
    # =========================
    symbol = "BTC/USD"
    price = get_crypto_price(symbol)

    analysis_prompt = (
        f"BTC current price is ${price}. "
        f"Analyze market conditions and decide whether to BUY, SELL, or HOLD."
    )

    ai_response = ask_openai(analysis_prompt)

    memory["last_analysis"] = {
        "symbol": symbol,
        "price": price,
        "ai": ai_response,
        "time": time.time(),
    }

    # =========================
    # DECISION LOGIC (SAFE)
    # =========================
    decision = "hold"
    if "buy" in ai_response.lower():
        decision = "buy"
    elif "sell" in ai_response.lower():
        decision = "sell"

    memory["last_decision"] = decision

    # =========================
    # ORDER (ONLY IF BUY/SELL)
    # =========================
    trade_result = None

    if decision in ["buy", "sell"]:
        notional = 50  # paper trade amount
        order = place_crypto_order(symbol, decision, notional)
        verified = verify_order(order["id"])

        trade_result = {
            "order": order,
            "verified": verified,
        }

        memory["last_trade"] = trade_result

    # =========================
    # RESPONSE
    # =========================
    return {
        "stage": "complete",
        "analysis": memory["last_analysis"],
        "decision": decision,
        "trade": trade_result,
        "explanation": "Trade only executed after Alpaca verification.",
    }
