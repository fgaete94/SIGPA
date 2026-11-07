from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import get_current_user, require_admin
from app.models.tipo_cliente import TipoCliente
from app.schemas.cliente import TipoClienteOut

router = APIRouter(tags=["tipos-cliente"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[TipoClienteOut])
async def listar_tipos_cliente():
    # Lo usa el panel para ofrecer las opciones B2C/B2B al editar un cliente
    async with SessionLocal() as session:
        result = await session.execute(select(TipoCliente).order_by(TipoCliente.nombre))
        return result.scalars().all()
