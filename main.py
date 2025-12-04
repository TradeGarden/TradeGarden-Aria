import os
import time
import logging
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import requests
import uvicorn

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria - TradeGarden Core")

OPENAI_KEY = os.getenv("OPENAI_KEY")
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")

PHONE_TOKEN = os.getenv("PHONE_TOKEN", "phone-secret")
MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))   # 2% risk
MAX_WIN_RATE = 0.10 # training threshold for learning log

ALLOWED_SYMBOLS = os.getenv("ALLOWED_SYMBOLS", "AAPL,MSFT,SPY,QQQ,GOOG").split(",")
PAPER_BASE = "https://paper-api.alpaca.markets"

state = {
    "daily_loss": 0.0,
    "orders_today": 0,
    "last_reset": int(time.time())
}


def reset_daily_if_needed():
    now = int(time.time())
    if now - state["last_reset"] > 24*3600:
        state["daily_loss"] = 0.0
        state["orders_today"] = 0
        state["last_reset"] = now


def call_openai(prompt: str) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content":
                "You are Aria, a highly skilled trader."
                "All outputs MUST be JSON only. No extra text."
                "Risk must be 2% of capital."
                "Always explain why you take a trade."
                "If user wants analysis, return: "
                '{"action":"analysis","text":"..."}'
                "If trade, return:"
                '{"action":"order","side":"buy|sell","symbol":"...","qty":number,"reason":"..."}'
            },
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    r.raise_for_status()
    return r.json()


def safe_parse_json(text: str):
    import json, re
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise ValueError("No JSON found.")
    return json.loads(m.group(0))


def place_order(order: Dict[str, Any]):
    url = f"{PAPER_BASE}/v2/orders"
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }
    resp = requests.post(url, headers=headers, json=order)
    resp.raise_for_status()
    return resp.json()


@app.post("/assistant")
async def assistant(req: Request, background: BackgroundTasks):
    reset_daily_if_needed()

    if PHONE_TOKEN not in req.headers.get("Authorization", ""):
        raise HTTPException(status_code=401, detail="Bad token.")

    body = await req.json()
    prompt = body.get("prompt", "")

    ai = call_openai(prompt)
    content = ai["choices"][0]["message"]["content"]

    try:
        parsed = safe_parse_json(content)
    except Exception:
        return {"action": "error", "raw": content}

    if parsed.get("action") == "analysis":
        return parsed

    if parsed.get("action") == "order":
        side = parsed["side"]
        symbol = parsed["symbol"]
        qty = int(parsed["qty"])

        if symbol not in ALLOWED_SYMBOLS:
            return {"action": "rejected", "reason": "symbol not allowed"}

        order_payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": "market",
            "time_in_force": "day"
        }

        def execute():
            try:
                result = place_order(order_payload)
                logging.info(result)
            except Exception as e:
                logging.error(f"Order error: {e}")

        background.add_task(execute)

        return {"action": "placed", "order": parsed}

    return {"action": "unknown", "raw": parsed}


@app.get("/health")
def health():
    return {"status": "ok", "service": "aria-core"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
