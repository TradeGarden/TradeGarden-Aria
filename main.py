from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse
import os

app = FastAPI()

PHONE_TOKEN = os.getenv("PHONE_TOKEN", "test-token")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria-core"
    }

@app.post("/assistant")
async def assistant(
    request: Request,
    authorization: str = Header(None)
):
    # --- Auth check ---
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization format")

    token = authorization.replace("Bearer ", "").strip()
    if token != PHONE_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    # --- Parse JSON safely ---
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    prompt = body.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing 'prompt'")

    # --- Guaranteed response ---
    return JSONResponse({
        "action": "analysis",
        "text": f"Aria received your message: {prompt}"
    })
