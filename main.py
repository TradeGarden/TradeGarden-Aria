import os
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

AUTH_TOKEN = os.getenv("PHONE_TOKEN")

@app.get("/health")
def health():
    return {"status": "ok", "service": "aria-crypto"}

@app.post("/assistant")
async def assistant(req: Request):
    auth = req.headers.get("Authorization", "")
    if auth != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await req.json()
    return {
        "message": "Aria is alive",
        "received": body
    }
