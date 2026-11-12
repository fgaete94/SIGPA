import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, clientes, comunas, internal, pedidos, productos, repartidor, reportes, rutas, sectores, tipos_cliente, whatsapp
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    # Sin ENABLE_DOCS=true no se publican /docs, /redoc ni /openapi.json.
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)

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
app.include_router(comunas.router, prefix="/comunas")
app.include_router(sectores.router, prefix="/sectores")
app.include_router(pedidos.router, prefix="/pedidos")
app.include_router(internal.router)
app.include_router(rutas.router, prefix="/rutas")
app.include_router(reportes.router, prefix="/reportes")
app.include_router(repartidor.router_admin)
app.include_router(repartidor.router_repartidor)


@app.get("/health")
async def health():
    # RENDER_GIT_COMMIT lo define Render en cada deploy: sirve para saber
    # qué commit está corriendo. Es la única variable de entorno expuesta.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT") or "desconocido"}
