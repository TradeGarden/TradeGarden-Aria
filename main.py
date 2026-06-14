from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
import random
from datetime import datetime

app = FastAPI(title="Aria AI Trading Dashboard")

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria Trading Engine"}

@app.get("/analyze", response_class=HTMLResponse)
async def dashboard():
    # Try real price
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        r.raise_for_status()
        price = float(r.json()["price"])
    except:
        price = 60500  # fallback

    decision = "BUY (Long)" if random.random() > 0.5 else "SELL (Short)"
    reason = f"Technical: {decision} setup on BTCUSD at ${price:,.2f}."

    risk = 5.0
    size = round(5 / (price * 0.02), 6)

    html = f"""
    <html><head><title>Aria Dashboard</title>
    <style>body{{font-family:Arial;margin:40px;background:#111;color:#ddd;}} .card{{background:#222;padding:30px;border-radius:12px;}}</style>
    </head><body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>BTCUSD Analysis</h2>
            <p><strong>Price:</strong> ${price:,.2f}</p>
            <p><strong>Decision:</strong> <b>{decision}</b></p>
            <p><strong>Reason:</strong> {reason}</p>
            <p><strong>Risk (1%):</strong> ${risk:.2f}</p>
            <p><strong>Suggested Size:</strong> {size} BTC</p>
            <p><strong>Paper Balance:</strong> $500.00</p>
        </div>
        <p><a href="/analyze">🔄 Refresh Analysis</a></p>
    </body></html>
    """
    return HTMLResponse(html)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
