from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.comuna import Comuna
from app.models.sector import Sector
from app.schemas.cliente import SectorOut

router = APIRouter(tags=["sectores"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[SectorOut])
async def listar_sectores():
    # Lo usa el panel para asignar el sector (y con él la comuna) al crear o editar un cliente
    async with SessionLocal() as session:
        result = await session.execute(
            select(Sector.id, Sector.nombre, Sector.comuna_id, Comuna.nombre.label("comuna_nombre"))
            .join(Comuna, Sector.comuna_id == Comuna.id)
            .order_by(Comuna.nombre, Sector.nombre)
        )
        return [SectorOut(id=fila.id, nombre=fila.nombre, comuna_id=fila.comuna_id, comuna_nombre=fila.comuna_nombre) for fila in result]
