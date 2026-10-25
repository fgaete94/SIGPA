from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.security import get_current_user
from app.models.cliente import Cliente
from app.models.comuna import Comuna
from app.models.sector import Sector
from app.schemas.cliente import ClienteCreate, ClienteDetalleOut, ClienteOut, ClienteUpdate, SugerenciaDiaOut
from app.services.auditoria_service import construir_snapshot, registrar_auditoria

router = APIRouter(tags=["clientes"], dependencies=[Depends(get_current_user)])

def _cliente_a_out(cliente: Cliente) -> ClienteOut:
    return ClienteOut(
        id=cliente.id,
        nombre=cliente.nombre,
        apellido_paterno=cliente.apellido_paterno,
        apellido_materno=cliente.apellido_materno,
        telefono=cliente.telefono,
        direccion=cliente.direccion,
        sector_id=cliente.sector_id,
        comuna_id=cliente.comuna_id,
        comuna_nombre=cliente.comuna.nombre if cliente.comuna else None,
        tipo_cliente_id=cliente.tipo_cliente_id,
        tipo_cliente_nombre=cliente.tipo_cliente.nombre if cliente.tipo_cliente else None,
        dia_reparto=cliente.dia_reparto,
        activo=cliente.activo,
        opt_out_whatsapp=cliente.opt_out_whatsapp,
        latitud=cliente.latitud,
        longitud=cliente.longitud,
    )



async def _validar_comuna_y_sector(session, comuna_id: int | None, sector_id: int | None) -> int | None:
    """Historia #136: devuelve la comuna que debe quedar en el cliente.

    El sector es opcional, pero si viene debe pertenecer a la comuna. Si llega un
    sector sin comuna, la comuna se toma del sector.
    """
    if sector_id is not None:
        sector = await session.get(Sector, sector_id)
        if sector is None:
            raise HTTPException(status_code=422, detail="El sector indicado no existe")
        if comuna_id is None:
            return sector.comuna_id
        if comuna_id != sector.comuna_id:
            raise HTTPException(status_code=422, detail="El sector no pertenece a la comuna indicada")
        return comuna_id

    if comuna_id is not None and await session.get(Comuna, comuna_id) is None:
        raise HTTPException(status_code=422, detail="La comuna indicada no existe")
    return comuna_id

@router.get("", response_model=list[ClienteOut])
async def listar_clientes(
    nombre: str | None = Query(None),
    telefono: str | None = Query(None),
):
    async with SessionLocal() as session:
        query = select(Cliente).options(selectinload(Cliente.tipo_cliente), selectinload(Cliente.comuna))

        if nombre is not None:
            query = query.where(
                or_(
                    Cliente.nombre.ilike(f"%{nombre}%"),
                    Cliente.apellido_paterno.ilike(f"%{nombre}%"),
                    Cliente.apellido_materno.ilike(f"%{nombre}%"),
                )
            )
        if telefono is not None:
            query = query.where(Cliente.telefono.ilike(f"%{telefono}%"))

        result = await session.execute(query)
        return [_cliente_a_out(cliente) for cliente in result.scalars().all()]

# Historia #93: por comuna solo se sugiere el día si es claramente dominante
# (en Viña del Mar, por ejemplo, cada sector tiene su propio día).
PORCENTAJE_MINIMO_COMUNA = 0.8


# Va antes de GET /{id}: si no, FastAPI intentaría leer "sugerencia-dia" como id.
@router.get("/sugerencia-dia", response_model=SugerenciaDiaOut)
async def sugerir_dia_reparto(
    sector_id: int | None = Query(None),
    comuna_id: int | None = Query(None),
    excluir_cliente_id: int | None = Query(None),
):
    """
    Historia #93: sugiere el día de reparto de un cliente, como el día más
    común entre los clientes activos que ya tienen día:
    - Con sector_id, entre los clientes de ese sector.
    - Sin sector_id, entre los clientes de la comuna, y solo si ese día
      reúne al menos el PORCENTAJE_MINIMO_COMUNA de ellos.
    Si nadie tiene día, si hay empate entre los días más comunes o si en
    la comuna ningún día es dominante, no hay sugerencia (dia_reparto null)
    y el administrador lo asigna a mano.

    excluir_cliente_id deja fuera al cliente que se está editando, para que
    su propio día no cuente en la sugerencia.
    """
    if sector_id is None and comuna_id is None:
        raise HTTPException(status_code=422, detail="Indica sector_id o comuna_id")

    async with SessionLocal() as session:
        if sector_id is not None:
            sector = await session.get(Sector, sector_id)
            if sector is None:
                raise HTTPException(status_code=404, detail="Sector no encontrado")
            base, base_id, base_nombre = "sector", sector.id, sector.nombre
            filtro = Cliente.sector_id == sector_id
        else:
            comuna = await session.get(Comuna, comuna_id)
            if comuna is None:
                raise HTTPException(status_code=404, detail="Comuna no encontrada")
            base, base_id, base_nombre = "comuna", comuna.id, comuna.nombre
            filtro = Cliente.comuna_id == comuna_id

        query = (
            select(Cliente.dia_reparto, func.count())
            .where(
                filtro,
                Cliente.activo.is_(True),
                Cliente.dia_reparto.is_not(None),
            )
            .group_by(Cliente.dia_reparto)
            .order_by(func.count().desc())
        )
        if excluir_cliente_id is not None:
            query = query.where(Cliente.id != excluir_cliente_id)

        conteos = (await session.execute(query)).all()

    total = sum(cantidad for _, cantidad in conteos)
    dia, clientes_con_ese_dia = None, 0

    if conteos and (len(conteos) == 1 or conteos[0][1] > conteos[1][1]):
        dia, clientes_con_ese_dia = conteos[0]

        if base == "comuna" and clientes_con_ese_dia < PORCENTAJE_MINIMO_COMUNA * total:
            dia, clientes_con_ese_dia = None, 0

    return SugerenciaDiaOut(
        base=base,
        base_id=base_id,
        base_nombre=base_nombre,
        dia_reparto=dia,
        clientes_con_ese_dia=clientes_con_ese_dia,
        clientes_con_dia=total,
    )

@router.post("", response_model=ClienteOut, status_code=status.HTTP_201_CREATED)
async def crear_cliente(datos: ClienteCreate, current_user: dict = Depends(get_current_user)):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Cliente).where(Cliente.telefono == datos.telefono)
        )
        if result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe un cliente con ese teléfono",
            )

        comuna_id = await _validar_comuna_y_sector(session, datos.comuna_id, datos.sector_id)
        if comuna_id is None:
            raise HTTPException(status_code=422, detail="La comuna es obligatoria")

        cliente = Cliente(**{**datos.model_dump(), "comuna_id": comuna_id})
        session.add(cliente)
        await session.commit()
        await session.refresh(cliente)

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="cliente",
            entidad_id=cliente.id,
            accion="crear",
            antes=None,
            despues=construir_snapshot(cliente),
        )

        result = await session.execute(
            select(Cliente)
            .where(Cliente.id == cliente.id)
            .options(selectinload(Cliente.tipo_cliente), selectinload(Cliente.comuna))
        )
        cliente_completo = result.scalar_one()

        return _cliente_a_out(cliente_completo)


@router.get("/{id}", response_model=ClienteDetalleOut)
async def obtener_cliente(id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            select(Cliente)
            .where(Cliente.id == id)
            .options(
                selectinload(Cliente.pedidos),
                selectinload(Cliente.tipo_cliente),
                selectinload(Cliente.comuna),
            )
        )
        cliente = result.scalar_one_or_none()
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        cliente.pedidos.sort(key=lambda pedido: pedido.creado_en, reverse=True)

        return ClienteDetalleOut(
            **_cliente_a_out(cliente).model_dump(),
            pedidos=cliente.pedidos,
        )


@router.patch("/{id}", response_model=ClienteOut)
async def actualizar_cliente(
    id: int, datos: ClienteUpdate, current_user: dict = Depends(get_current_user)
):
    async with SessionLocal() as session:
        cliente = await session.get(Cliente, id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")

        cambios = datos.model_dump(exclude_unset=True)

        # El bot identifica al cliente por su teléfono: no puede quedar repetido
        if cambios.get("telefono") and cambios["telefono"] != cliente.telefono:
            result = await session.execute(
                select(Cliente).where(Cliente.telefono == cambios["telefono"], Cliente.id != id)
            )
            if result.scalars().first() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ya existe un cliente con ese teléfono",
                )

        # Si cambia la comuna o el sector, se valida el par resultante. Sin comuna en el
        # cambio, un sector nuevo define la comuna; sin sector, se mantiene el actual.
        if "comuna_id" in cambios or "sector_id" in cambios:
            sector_id = cambios.get("sector_id", cliente.sector_id)
            if "comuna_id" in cambios:
                if cambios["comuna_id"] is None and sector_id is not None:
                    raise HTTPException(
                        status_code=422,
                        detail="Para quitar la comuna también hay que quitar el sector",
                    )
                comuna_id = cambios["comuna_id"]
            else:
                comuna_id = None if sector_id is not None else cliente.comuna_id
            cambios["comuna_id"] = await _validar_comuna_y_sector(session, comuna_id, sector_id)

        # Historia #159: si cambia la dirección y no vienen coordenadas nuevas,
        # se borran las anteriores para que la dirección nueva se geocodifique.
        cambia_direccion = (
            "direccion" in cambios
            and (cambios["direccion"] or "").strip() != (cliente.direccion or "").strip()
        )
        if cambia_direccion and "latitud" not in cambios and "longitud" not in cambios:
            cambios["latitud"] = None
            cambios["longitud"] = None

        snapshot_antes = construir_snapshot(cliente)

        for campo, valor in cambios.items():
            setattr(cliente, campo, valor)

        await session.commit()
        await session.refresh(cliente)

        await registrar_auditoria(
            usuario=current_user.get("email"),
            entidad="cliente",
            entidad_id=cliente.id,
            accion="actualizar",
            antes=snapshot_antes,
            despues=construir_snapshot(cliente),
        )

        result = await session.execute(
            select(Cliente)
            .where(Cliente.id == cliente.id)
            .options(selectinload(Cliente.tipo_cliente), selectinload(Cliente.comuna))
        )
        cliente_completo = result.scalar_one()

        return _cliente_a_out(cliente_completo)
