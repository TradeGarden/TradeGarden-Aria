import os
import logging
from typing import Dict, Any, List
from fastapi import FastAPI, HTTPException, Request
import requests
import uvicorn

# ------------------ BASIC SETUP ------------------

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Aria Crypto Core")

OPENAI_KEY = os.getenv("OPENAI_KEY")
PHONE_TOKEN = os.getenv("PHONE_TOKEN", "")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))

CRYPTO_SYMBOLS: List[str] = [
    s.strip() for s in os.getenv("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD").split(",")
]

if not OPENAI_KEY:
    raise RuntimeError("OPENAI_KEY is missing")

# ------------------ OPENAI CALL ------------------

def call_openai(prompt: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = f"""
You are Aria, a professional crypto trading AI.

RULES (MANDATORY):
- Crypto ONLY
- Allowed symbols: {CRYPTO_SYMBOLS}
- NO stocks
- NO forex
- NO execution
- NO prices unless stated as ESTIMATE
- Always respond in JSON
- Follow this flow strictly:
  1. analysis
  2. decision (buy / sell / hold)
  3. explanation

Response format:
{{
  "action": "analysis",
  "symbol": "BTC/USD",
  "decision": "buy|sell|hold",
  "confidence": 0.0,
  "reasoning": "text explanation"
}}
"""

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=30
    )
    r.raise_for_status()
    return r.json()

# ------------------ ROUTES ------------------

@app.post("/assistant")
async def assistant(req: Request):
    # Auth check
    auth = req.headers.get("Authorization", "")
    if PHONE_TOKEN not in auth:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "").strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt required")

    ai = call_openai(prompt)
    content = ai["choices"][0]["message"]["content"]

    return {
        "layer": 2,
        "mode": "crypto-analysis-only",
        "response": content
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-crypto",
        "symbols": CRYPTO_SYMBOLS,
        "risk": MAX_RISK
    }

# ------------------ RUN ------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
