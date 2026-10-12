import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, clientes, internal, pedidos, productos, rutas, sectores, tipos_cliente, whatsapp
from app.core.config import settings

app = FastAPI(title=settings.PROJECT_NAME)

# Permite que el panel web (en otro dominio) llame a la API desde el navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # No se usan cookies: el token viaja en el header Authorization
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router, prefix="/auth")
app.include_router(whatsapp.router)
app.include_router(productos.router, prefix="/productos")
app.include_router(clientes.router, prefix="/clientes")
app.include_router(tipos_cliente.router, prefix="/tipos-cliente")
app.include_router(sectores.router, prefix="/sectores")
app.include_router(pedidos.router, prefix="/pedidos")
app.include_router(internal.router)
app.include_router(rutas.router, prefix="/rutas")


@app.get("/health")
async def health():
    # RENDER_GIT_COMMIT lo define Render en cada deploy: sirve para saber
    # qué commit está corriendo. Es la única variable de entorno expuesta.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT") or "desconocido"}
