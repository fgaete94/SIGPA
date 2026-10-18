from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.comuna import Comuna
from app.schemas.cliente import ComunaOut

router = APIRouter(tags=["comunas"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[ComunaOut])
async def listar_comunas():
    # Historia #136: el panel elige primero la comuna del cliente y luego, de forma opcional, un sector de ella
    async with SessionLocal() as session:
        result = await session.execute(select(Comuna).order_by(Comuna.nombre))
        return result.scalars().all()