from pydantic import BaseModel, ConfigDict


class ProductoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    precio_unitario: float
    stock: int
    capacidad_litros: float | None = None


class ProductoCreate(BaseModel):
    nombre: str
    precio_unitario: float
    stock: int = 0
    capacidad_litros: float | None = None


class ProductoUpdate(BaseModel):
    nombre: str | None = None
    precio_unitario: float | None = None
    stock: int | None = None
    capacidad_litros: float | None = None
