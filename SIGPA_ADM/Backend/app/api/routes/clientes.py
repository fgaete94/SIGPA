from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.cliente import Cliente
from app.schemas.cliente import ClienteCreate, ClienteOut, ClienteUpdate

router = APIRouter(tags=["clientes"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[ClienteOut])
async def listar_clientes():
    async with SessionLocal() as session:
        result = await session.execute(select(Cliente))
        return result.scalars().all()


@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
async def crear_cliente(datos: ClienteCreate):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Cliente).where(Cliente.telefono == datos.telefono)
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un cliente con ese teléfono",
            )

        cliente = Cliente(**datos.model_dump())
        session.add(cliente)
        await session.commit()
        await session.refresh(cliente)
        return cliente


@router.get("/{id}", response_model=ClienteOut)
async def obtener_cliente(id: int):
    async with SessionLocal() as session:
        cliente = await session.get(Cliente, id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        return cliente


@router.patch("/{id}", response_model=ClienteOut)
async def actualizar_cliente(id: int, datos: ClienteUpdate):
    async with SessionLocal() as session:
        cliente = await session.get(Cliente, id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(cliente, campo, valor)

        await session.commit()
        await session.refresh(cliente)
        return cliente
