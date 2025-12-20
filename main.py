import os
import time
import logging
from typing import Dict, Any
from fastapi import FastAPI, Request, HTTPException
import requests

# --------------------------------------------------
# App setup
# --------------------------------------------------
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria – TradeGarden Crypto Core")

# --------------------------------------------------
# Environment variables (DO NOT hardcode secrets)
# --------------------------------------------------
OPENAI_KEY = os.getenv("OPENAI_KEY")
PHONE_TOKEN = os.getenv("PHONE_TOKEN", "aria-phone-2026")

APCA_API_BASE_URL = os.getenv(
    "APCA_API_BASE_URL",
    "https://paper-api.alpaca.markets"
)

APCA_API_KEY_ID = os.getenv("APCA_API_KEY_ID")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")

MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))

# Crypto-only symbols (LOCKED)
CRYPTO_SYMBOLS = ["BTC/USD", "ETH/USD"]

# --------------------------------------------------
# Simple in-memory memory (Layer 2)
# --------------------------------------------------
memory = {
    "conversations": [],
    "decisions": [],
    "started_at": int(time.time())
}

# --------------------------------------------------
# OpenAI call (analysis only, no fake prices)
# --------------------------------------------------
def call_openai(prompt: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are Aria, a professional crypto trader.\n"
        "You ONLY trade crypto.\n"
        "Allowed symbols: BTC/USD, ETH/USD.\n"
        "You NEVER invent prices.\n"
        "You ALWAYS return JSON only.\n\n"
        "You must respond in this format:\n"
        "{\n"
        '  "symbol": "BTC/USD",\n'
        '  "analysis": "...",\n'
        '  "decision": "buy | sell | hold",\n'
        '  "risk": "2%",\n'
        '  "reason": "..."\n'
        "}\n"
    )

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

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
    # Auth check
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {PHONE_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt")

    if not prompt:
        raise HTTPException(status_code=400, detail="Missing prompt")

    # Store conversation
    memory["conversations"].append(
        {"time": int(time.time()), "prompt": prompt}
    )

    ai_response = call_openai(prompt)
    content = ai_response["choices"][0]["message"]["content"]

    # Save decision
    memory["decisions"].append(
        {"time": int(time.time()), "response": content}
    )

    return {
        "action": "analysis",
        "response": content,
        "memory_size": len(memory["decisions"]),
    }
