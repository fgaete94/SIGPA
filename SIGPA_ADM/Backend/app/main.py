from fastapi import FastAPI

from app.api.routes import whatsapp
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(whatsapp.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
