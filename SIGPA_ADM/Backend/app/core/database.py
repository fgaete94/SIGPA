from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

# Historia #78: el pooler de Supabase en modo sesión admite 15 conexiones en
# total, compartidas entre el backend, n8n y cualquier otro cliente (scripts,
# pruebas locales). Con el pool por defecto de SQLAlchemy (5 + 10 de
# overflow) el backend podía abrir 15 por sí solo y, con varias
# conversaciones simultáneas, el pooler rechazaba conexiones
# (EMAXCONNSESSION) y se perdían mensajes. Con un tope fijo, las consultas
# que no alcanzan conexión esperan su turno (hasta DB_POOL_TIMEOUT) en vez
# de fallar. pool_pre_ping descarta conexiones que el pooler cerró mientras
# estaban inactivas (por ejemplo, cuando Render duerme el servicio).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def get_async_db():
    async with SessionLocal() as session:
        yield session
