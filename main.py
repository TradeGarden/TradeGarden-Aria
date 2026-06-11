from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Aria Trading Dashboard")

@app.get("/health")
@app.get("/")
def health():
    return {"status": "ok", "service": "Aria Trading Engine"}

@app.get("/analyze", response_class=HTMLResponse)
async def dashboard():
    html = """
    <html>
    <head><title>Aria Dashboard</title>
    <style>body{font-family:Arial;margin:40px;background:#111;color:#ddd;} .card{background:#222;padding:30px;border-radius:12px;}</style>
    </head>
    <body>
        <h1>Aria AI Trading Dashboard</h1>
        <div class="card">
            <h2>BTCUSD Analysis</h2>
            <p>Price: Loading...</p>
            <p>Decision: BUY / SELL</p>
            <p>Risk (1%): Active</p>
        </div>
        <p><a href="/analyze">Refresh</a></p>
    </body>
    </html>
    """
    return HTMLResponse(html)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
