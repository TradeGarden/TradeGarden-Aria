import os
import logging
from fastapi import FastAPI, Request, HTTPException
import requests

# --------------------------------------------------
# Setup
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria – TradeGarden Crypto Core")

# --------------------------------------------------
# ENV VARS
# --------------------------------------------------
OPENAI_KEY = os.getenv("OPENAI_KEY")
PHONE_TOKEN = os.getenv("PHONE_TOKEN", "aria-phone-2026")

CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))

# --------------------------------------------------
# OpenAI
# --------------------------------------------------
def call_openai(prompt: str):
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Aria, a professional crypto trader.\n"
                    "Only BTC/USD or ETH/USD.\n"
                    "No fake prices.\n"
                    "Return JSON ONLY:\n"
                    "{symbol, analysis, decision, risk, reason}"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

# --------------------------------------------------
# Routes
# --------------------------------------------------
@app.get("/")
def root():
    return {"message": "Aria is alive"}

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
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt")

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt required")

    ai = call_openai(prompt)
    content = ai["choices"][0]["message"]["content"]

    return {"action": "analysis", "response": content}
