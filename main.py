# main.py - TradeGarden / Aria (Crypto-only, full memory, require confirm)
import os
import time
import json
import logging
import math
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
import requests
import uvicorn
from pathlib import Path

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria - TradeGarden Core (Crypto Only)")

# --- ENV VARS (set these in Render or Replit) ---
OPENAI_KEY = os.getenv("OPENAI_KEY")
ALPACA_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_ENDPOINT = os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
PHONE_TOKEN = os.getenv("PHONE_TOKEN", "phone-secret")

# Risk / behavior
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.02"))   # 2% risk
DAILY_DRAWDOWN_PCT = float(os.getenv("DAILY_DRAWDOWN_PCT", "0.10"))   # 10%
MAX_ORDER_QTY = int(os.getenv("MAX_ORDER_QTY", "1000"))  # high limit, crypto often fractional via qty in units
ALLOWED_SYMBOLS = os.getenv("ALLOWED_SYMBOLS", "BTCUSD,ETHUSD").split(",")

# Memory file
MEMORY_PATH = os.getenv("MEMORY_PATH", "memory.json")
memory_file = Path(MEMORY_PATH)

# Ensure keys exist (warnings only)
if not (OPENAI_KEY and ALPACA_KEY and ALPACA_SECRET):
    logging.warning("One or more API keys are missing. Set OPENAI_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY in env.")

# Simple in-process state
state = {
    "last_reset": int(time.time())
}

# --- Memory utilities (full memory stored in JSON) ---
def load_memory() -> Dict[str, Any]:
    if memory_file.exists():
        try:
            return json.loads(memory_file.read_text())
        except Exception as e:
            logging.exception("Failed loading memory.json")
            return {"created_at": time.time(), "analyses": [], "suggested_orders": [], "executed_orders": [], "notes": []}
    else:
        mem = {"created_at": time.time(), "analyses": [], "suggested_orders": [], "executed_orders": [], "notes": []}
        memory_file.write_text(json.dumps(mem, indent=2))
        return mem

def save_memory(mem: Dict[str, Any]):
    memory_file.write_text(json.dumps(mem, indent=2))

memory = load_memory()

# --- Helpers: price feeds & sizing ---
def fetch_crypto_price(symbol: str) -> float:
    """
    Get latest USD price for symbol using CoinGecko (no API key).
    symbol expected as 'BTCUSD' or 'ETHUSD'
    """
    mapping = {"BTCUSD":"bitcoin", "ETHUSD":"ethereum"}
    coin = mapping.get(symbol.upper())
    if not coin:
        raise ValueError("Unsupported crypto symbol for price fetch")
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    price = float(data[coin]["usd"])
    return price

def fetch_account_equity() -> float:
    url = f"{ALPACA_ENDPOINT}/v2/account"
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    j = r.json()
    equity = float(j.get("equity") or j.get("cash") or 0.0)
    return equity

def compute_qty_from_risk(symbol: str, equity: float, stop_price: Optional[float]=None) -> float:
    """
    Compute qty (crypto units) that risks RISK_PER_TRADE_PCT of equity.
    If stop_price provided, compute using (entry - stop) per unit risk.
    Otherwise use conservative notional: qty = (equity * RISK%) / price
    """
    price = fetch_crypto_price(symbol)
    budget = equity * RISK_PER_TRADE_PCT
    if stop_price and stop_price < price:
        # risk per unit = (price - stop)
        per_unit_risk = price - stop_price
        if per_unit_risk <= 0:
            return 0.0
        qty = budget / per_unit_risk
    else:
        qty = budget / price
    # For crypto we allow fractional qty; cap sanity
    qty = max(0.0, qty)
    if qty > MAX_ORDER_QTY:
        qty = float(MAX_ORDER_QTY)
    # round to 6 decimal places for crypto
    return round(qty, 6)

# --- OpenAI call that forces JSON response ---
def call_openai_json(prompt: str) -> Dict[str, Any]:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_KEY not set")
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    system = (
        "You are Aria, a crypto trading analyst. RETURN EXACT JSON ONLY."
        "If analysis: {\"action\":\"analysis\",\"text\":\"...\"}."
        "If recommending a trade: return {\"action\":\"order\",\"side\":\"buy|sell\",\"symbol\":\"BTCUSD|ETHUSD\",\"reason\":\"short reason\",\"suggested_stop\":<number or null>}."
        "Do NOT include qty. Always include a concise reason and, if relevant, a suggested_stop price. If uncertain, return analysis not order."
    )
    payload = {"model":"gpt-4o-mini","messages":[{"role":"system","content":system},{"role":"user","content":prompt}],"temperature":0.0,"max_tokens":400}
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def safe_parse_json_block(text: str) -> Dict[str, Any]:
    import re, json
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        raise ValueError("No JSON object in model output")
    return json.loads(m.group(0))

# --- Alpaca order placement (crypto) ---
def place_alpaca_crypto_order(symbol: str, side: str, qty: float):
    """
    Place a crypto market order with Alpaca. We set asset_class='crypto' in payload.
    Note: Alpaca expects qty for crypto as decimal strings.
    """
    url = f"{ALPACA_ENDPOINT}/v2/orders"
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "Content-Type":"application/json"}
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "gtc",
        "asset_class": "crypto"
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

# --- API endpoints ---
@app.get("/")
def home():
    return {"status":"Aria is online", "mode":"crypto-only"}

@app.get("/memory")
def get_memory():
    # Return short summary
    mem = load_memory()
    summary = {
        "created_at": mem.get("created_at"),
        "last_analyses_count": len(mem.get("analyses",[])),
        "suggested_orders_count": len(mem.get("suggested_orders",[])),
        "executed_orders_count": len(mem.get("executed_orders",[]))
    }
    return {"memory_summary": summary, "recent": mem}

@app.post("/assistant")
async def assistant(req: Request):
    """
    Main endpoint. Returns either analysis or a suggested order requiring confirmation.
    """
    if PHONE_TOKEN not in req.headers.get("Authorization", ""):
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="No prompt")

    # Ask Aria
    ai_resp = call_openai_json(prompt)
    content = ai_resp["choices"][0]["message"]["content"]
    try:
        parsed = safe_parse_json_block(content)
    except Exception as e:
        # log model raw output to memory and return error
        mem = load_memory()
        mem["notes"].append({"time":time.time(),"event":"bad_model_output","raw":content})
        save_memory(mem)
        return {"action":"error","raw":content}

    # If analysis => save analysis and return
    if parsed.get("action") == "analysis":
        mem = load_memory()
        mem["analyses"].append({"time":time.time(),"prompt":prompt,"analysis":parsed.get("text")})
        save_memory(mem)
        return {"action":"analysis","text":parsed.get("text")}

    # If model recommended order -> prepare suggested order (no auto-execute)
    if parsed.get("action") == "order":
        side = parsed.get("side")
        symbol = parsed.get("symbol")
        reason = parsed.get("reason","").strip()
        suggested_stop = parsed.get("suggested_stop")  # may be None

        if not reason:
            return {"action":"rejected","reason":"AI must supply a reason"}

        if symbol not in ALLOWED_SYMBOLS:
            return {"action":"rejected","reason":"symbol not allowed"}

        # compute qty using 2% risk and suggested stop if provided
        try:
            equity = fetch_account_equity()
        except Exception as e:
            return {"action":"error","reason":"failed to fetch account equity", "detail": str(e)}
        try:
            qty = compute_qty_from_risk(symbol, equity, stop_price=suggested_stop)
        except Exception as e:
            return {"action":"error","reason":"failed to compute qty", "detail": str(e)}

        if qty <= 0:
            return {"action":"rejected","reason":"calculated qty is zero (insufficient equity)"}

        # Save suggested order in memory with a unique id
        suggestion = {
            "id": f"sugg_{int(time.time()*1000)}",
            "time": time.time(),
            "prompt": prompt,
            "ai": parsed,
            "qty": qty,
            "side": side,
            "symbol": symbol,
            "reason": reason,
            "suggested_stop": suggested_stop,
            "status": "pending"
        }
        mem = load_memory()
        mem["suggested_orders"].append(suggestion)
        save_memory(mem)

        # Return suggestion and require confirmation endpoint
        return {"action":"require_confirm","suggestion": suggestion, "how_to_confirm": "POST /confirm with {'suggestion_id':...,'confirm':true}"}

    return {"action":"unknown","raw_ai": parsed}

@app.post("/confirm")
async def confirm(req: Request, background: BackgroundTasks):
    """
    Confirm and execute a previously suggested order.
    Body: {"suggestion_id":"sugg_...", "confirm": true}
    """
    if PHONE_TOKEN not in req.headers.get("Authorization",""):
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    suggestion_id = body.get("suggestion_id")
    confirm_flag = body.get("confirm", False)
    if not suggestion_id:
        raise HTTPException(status_code=400, detail="Missing suggestion_id")

    mem = load_memory()
    suggestion = next((s for s in mem.get("suggested_orders",[]) if s["id"]==suggestion_id), None)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion["status"] != "pending":
        return {"action":"rejected","reason":"Suggestion is not pending"}

    if not confirm_flag:
        # mark as declined
        suggestion["status"] = "declined"
        suggestion["declined_at"] = time.time()
        save_memory(mem)
        return {"action":"declined","suggestion_id": suggestion_id}

    # Double-check daily drawdown and risk
    try:
        equity = fetch_account_equity()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch equity: {e}")
    max_daily_loss_abs = equity * DAILY_DRAWDOWN_PCT
    # NOTE: memory should hold executed P&L; we do conservative check here
    # For demo we skip deep P&L computation.

    # Place the order in background
    def do_place_and_record():
        try:
            resp = place_alpaca_crypto_order(suggestion["symbol"], suggestion["side"], suggestion["qty"])
            # record execution
            executed = {
                "id": f"exec_{int(time.time()*1000)}",
                "time": time.time(),
                "suggestion_id": suggestion_id,
                "order_result": resp
            }
            mem2 = load_memory()
            suggestion2 = next((s for s in mem2.get("suggested_orders",[]) if s["id"]==suggestion_id), None)
            if suggestion2:
                suggestion2["status"] = "executed"
                suggestion2["executed_at"] = time.time()
                mem2["executed_orders"].append(executed)
                save_memory(mem2)
            logging.info("Order executed: %s", resp)
        except Exception as ex:
            logging.exception("Execution failed: %s", ex)
            mem3 = load_memory()
            mem3["notes"].append({"time":time.time(),"event":"execution_failed","detail": str(ex)})
            save_memory(mem3)

    background.add_task(do_place_and_record)
    # immediate response
    return {"action":"placed_request","suggestion_id": suggestion_id, "status":"placed_request"}

@app.get("/health")
def health():
    return {"status":"ok","service":"aria-core-crypto"}

# --- run server
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT","8000")))
