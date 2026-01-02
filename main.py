import os
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# --------------------
# App Init
# --------------------
app = FastAPI()

# --------------------
# Environment Variables
# --------------------
PHONE_TOKEN = os.getenv("PHONE_TOKEN")

# --------------------
# Models
# --------------------
class AssistRequest(BaseModel):
    prompt: str

# --------------------
# Health Check
# --------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "aria"
    }

# --------------------
# Root
# --------------------
@app.get("/")
def root():
    return {"message": "Aria is alive"}

# --------------------
# ASSIST ENDPOINT (THIS IS THE ONE YOU USE)
# --------------------
@app.post("/assist")
def assist(
    data: AssistRequest,
    authorization: str = Header(None)
):
    # Optional simple auth check
    if PHONE_TOKEN:
        if not authorization or authorization != f"Bearer {PHONE_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    # Simple response (no OpenAI, no Alpaca yet)
    return {
        "action": "analysis",
        "text": f"Aria received your message: {data.prompt}"
    }
