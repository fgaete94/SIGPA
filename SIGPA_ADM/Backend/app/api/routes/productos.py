from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.producto import Producto
from app.schemas.producto import ProductoCreate, ProductoOut, ProductoUpdate

router = APIRouter(tags=["productos"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[ProductoOut])
async def listar_productos():
    async with SessionLocal() as session:
        result = await session.execute(select(Producto))
        return result.scalars().all()


@router.post("", response_model=ProductoOut, status_code=status.HTTP_201_CREATED)
async def crear_producto(datos: ProductoCreate):
    async with SessionLocal() as session:
        producto = Producto(**datos.model_dump())
        session.add(producto)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un producto con ese nombre",
            )
        await session.refresh(producto)
        return producto


@router.get("/{id}", response_model=ProductoOut)
async def obtener_producto(id: int):
    async with SessionLocal() as session:
        producto = await session.get(Producto, id)
        if producto is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        return producto


@router.patch("/{id}", response_model=ProductoOut)
async def actualizar_producto(id: int, datos: ProductoUpdate):
    async with SessionLocal() as session:
        producto = await session.get(Producto, id)
        if producto is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(producto, campo, valor)

        await session.commit()
        await session.refresh(producto)
        return producto
