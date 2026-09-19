from fastapi import FastAPI

from app.api.routes import clientes, pedidos, productos, whatsapp
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(whatsapp.router)
app.include_router(productos.router, prefix="/productos")
app.include_router(clientes.router, prefix="/clientes")
app.include_router(pedidos.router, prefix="/pedidos")


@app.get("/health")
async def health():
    return {"status": "ok"}
