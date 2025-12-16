import os, json, time, logging, requests
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Aria – TradeGarden Crypto Core")

# === ENV ===
OPENAI_KEY = os.getenv("OPENAI_KEY")
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL")
PHONE_TOKEN = os.getenv("PHONE_TOKEN")

MAX_RISK = float(os.getenv("MAX_RISK", "0.02"))
ALLOWED_SYMBOLS = ["BTCUSD", "ETHUSD"]
MEMORY_FILE = os.getenv("MEMORY_FILE", "memory.json")

CRYPTO_PRICE_URL = f"{BASE_URL}/v2/assets"

# === MEMORY ===
def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {"trades": [], "analysis": None}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def save_memory(mem):
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2)

memory = load_memory()

# === MARKET DATA ===
def get_crypto_price(symbol):
    url = f"{BASE_URL}/v1beta3/crypto/us/latest/trades"
    headers = {
        "APCA-API-KEY-ID": API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET
    }
    r = requests.get(url, headers=headers, params={"symbols": symbol})
    r.raise_for_status()
    return r.json()["trades"][symbol]["p"]

# === OPENAI ===
def ask_aria(prompt, price):
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json"
    }

    system = f"""
You are Aria, a professional crypto trader.
You ONLY trade BTCUSD or ETHUSD.
You MUST follow this order:
1. Analyze market using live price: {price}
2. Decide trade or no trade
3. Respect max risk: {MAX_RISK*100}%
4. Output JSON ONLY

If no trade:
{{"action":"analysis","text":"..."}}

If trade:
{{"action":"order","side":"buy|sell","symbol":"BTCUSD","qty":1,"reason":"..."}}
"""

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
    }

    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# === ORDER ===
def place_crypto_order(order):
    url = f"{BASE_URL}/v2/orders"
    headers = {
        "APCA-API-KEY-ID": API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET,
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, json=order)
    r.raise_for_status()
    return r.json()

# === API ===
@app.post("/assistant")
async def assistant(req: Request, bg: BackgroundTasks):
    if PHONE_TOKEN not in req.headers.get("Authorization", ""):
        raise HTTPException(401, "Unauthorized")

    body = await req.json()
    prompt = body.get("prompt", "")
    symbol = body.get("symbol", "BTCUSD")

    if symbol not in ALLOWED_SYMBOLS:
        raise HTTPException(400, "Symbol not allowed")

    price = get_crypto_price(symbol)
    response = ask_aria(prompt, price)

    data = json.loads(response)
    memory["analysis"] = data
    save_memory(memory)

    if data["action"] == "order":
        order = {
            "symbol": symbol,
            "qty": data["qty"],
            "side": data["side"],
            "type": "market",
            "time_in_force": "gtc"
        }

        def execute():
            result = place_crypto_order(order)
            memory["trades"].append({
                "time": time.time(),
                "order": result,
                "reason": data["reason"]
            })
            save_memory(memory)

        bg.add_task(execute)
        return {"status": "ORDER_PLACED", "details": data, "price": price}

    return {"status": "ANALYSIS_ONLY", "analysis": data, "price": price}

@app.get("/health")
def health():
    return {"status": "Aria is online", "mode": "crypto-only"}
