"""Orquestación del flujo de pedidos por WhatsApp.

Conecta interpret_message (interpretación del LLM), construir_resumen_pedido
(precios reales desde la BD) y draft_store (borrador de pedido en curso por
cliente) para decidir qué responder en cada mensaje entrante.
"""

import json
import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Cliente, DetallePedido, Pedido, Producto
from app.models.enums import EstadoPedido
from app.services.agent_service import construir_resumen_pedido, interpret_message
from app.services.draft_store import clear_draft, get_draft, save_draft

logger = logging.getLogger(__name__)

CONFIRMACIONES = {"si", "sí", "confirmo", "dale", "ok"}

MENSAJE_PEDIR_UBICACION = (
    "Para continuar necesito que compartas tu ubicación de WhatsApp: usa el clip 📎 "
    "y selecciona 'Ubicación'."
)

PREGUNTA_DIRECCION_HABITUAL = (
    "¿Confirmas tu dirección habitual o prefieres indicar una distinta para este pedido?"
)

MENSAJE_PEDIR_NOMBRE = "¿A nombre de quién registramos tu pedido?"

MENSAJE_ERROR_PEDIDO = (
    "Hubo un problema al registrar tu pedido, por favor intenta de nuevo o contacta a un ejecutivo."
)


def _contexto_desde_draft(draft: dict | None) -> dict | None:
    if draft is None:
        return None
    return {
        "intencion": draft.get("intencion"),
        "productos": draft.get("productos", []),
        "aclaracion_pendiente": draft.get("aclaracion_pendiente"),
        "usa_direccion_habitual": draft.get("usa_direccion_habitual"),
        "direccion_texto": draft.get("direccion_texto"),
        "notas": draft.get("notas"),
        "ubicacion_recibida": draft.get("ubicacion") is not None,
        "nombre_cliente": draft.get("nombre_cliente"),
    }


async def _interpretar_con_debug(
    phone: str, message: str, es_cliente_nuevo: bool, context: dict | None
) -> dict:
    # Logging de debug para diagnosticar problemas de interpretación. Usa
    # logger.debug a propósito: no aparece con el nivel INFO por defecto, así
    # que no hace falta quitarlo; si se necesita volver a diagnosticar algo,
    # basta con subir temporalmente el nivel de logging a DEBUG.
    logger.debug("[DEBUG contexto] %s", json.dumps(context, ensure_ascii=False))
    resultado = await interpret_message(
        phone=phone,
        message=message,
        es_cliente_nuevo=es_cliente_nuevo,
        context=context,
    )
    logger.debug("[DEBUG resultado_llm] %s", json.dumps(resultado, ensure_ascii=False))
    return resultado


async def _aplicar_resultado_llm(
    phone: str, resultado: dict, draft_previo: dict | None, es_cliente_nuevo: bool
) -> str:
    direccion_preguntada = bool((draft_previo or {}).get("direccion_preguntada"))

    if resultado.get("usa_direccion_habitual"):
        # Salvaguarda: la dirección habitual nunca requiere ubicación, sin
        # importar qué haya devuelto el LLM en "esperando_ubicacion" ni si el
        # cliente es nuevo o existente. pedido_completo se recalcula sin
        # exigir ubicación (solo productos y, si aplica, nombre_cliente).
        nombre_resuelto = (not es_cliente_nuevo) or bool(resultado.get("nombre_cliente"))
        resultado = {
            **resultado,
            "esperando_ubicacion": False,
            "pedido_completo": bool(resultado.get("productos")) and nombre_resuelto,
        }
        direccion_preguntada = True

    if not es_cliente_nuevo and not direccion_preguntada and resultado.get("esperando_ubicacion"):
        # Salvaguarda: a un cliente existente nunca se le debe pedir ubicación
        # sin antes haberle preguntado explícitamente por su dirección habitual,
        # aunque el LLM se haya saltado ese paso.
        resultado = {
            **resultado,
            "esperando_ubicacion": False,
            "usa_direccion_habitual": False,
            "pedido_completo": False,
            "respuesta_sugerida": PREGUNTA_DIRECCION_HABITUAL,
        }
        direccion_preguntada = True

    nuevo_draft = {
        "intencion": resultado.get("intencion"),
        "productos": resultado.get("productos", []),
        "aclaracion_pendiente": resultado.get("aclaracion_pendiente"),
        "usa_direccion_habitual": resultado.get("usa_direccion_habitual"),
        "direccion_texto": resultado.get("direccion_texto"),
        "notas": resultado.get("notas"),
        "ubicacion": (draft_previo or {}).get("ubicacion"),
        "direccion_preguntada": direccion_preguntada,
        "nombre_cliente": resultado.get("nombre_cliente"),
    }

    if resultado.get("esperando_ubicacion"):
        nuevo_draft["estado"] = "esperando_ubicacion"
        save_draft(phone, nuevo_draft)
        return resultado.get("respuesta_sugerida", MENSAJE_PEDIR_UBICACION)

    if resultado.get("pedido_completo"):
        resumen = await construir_resumen_pedido(resultado.get("productos", []))
        nuevo_draft["estado"] = "esperando_confirmacion"
        nuevo_draft["resumen"] = resumen
        save_draft(phone, nuevo_draft)
        return resumen["texto_resumen"]

    nuevo_draft["estado"] = "armando"
    save_draft(phone, nuevo_draft)
    return resultado.get("respuesta_sugerida", "")


async def _confirmar_pedido(phone: str, draft: dict) -> str:
    async with SessionLocal() as session:
        try:
            result = await session.execute(select(Cliente).where(Cliente.telefono == phone))
            cliente = result.scalar_one_or_none()

            if cliente is None:
                nombre_cliente = draft.get("nombre_cliente")
                if not nombre_cliente:
                    draft["esperando_nombre"] = True
                    save_draft(phone, draft)
                    return MENSAJE_PEDIR_NOMBRE

                ubicacion_cliente = draft.get("ubicacion") or {}
                cliente = Cliente(
                    telefono=phone,
                    nombre=nombre_cliente,
                    latitud=ubicacion_cliente.get("latitud"),
                    longitud=ubicacion_cliente.get("longitud"),
                    activo=True,
                )
                session.add(cliente)
                await session.flush()

            resumen = draft.get("resumen") or {}
            lineas = resumen.get("lineas", [])

            nombres_productos = [linea["nombre"] for linea in lineas]
            result = await session.execute(
                select(Producto).where(Producto.nombre.in_(nombres_productos))
            )
            productos_bd = {p.nombre: p for p in result.scalars().all()}

            usa_direccion_habitual = bool(draft.get("usa_direccion_habitual"))
            ubicacion_pedido = draft.get("ubicacion") or {}

            pedido = Pedido(
                cliente_id=cliente.id,
                estado=EstadoPedido.PENDIENTE,
                direccion_despacho=None if usa_direccion_habitual else draft.get("direccion_texto"),
                latitud=None if usa_direccion_habitual else ubicacion_pedido.get("latitud"),
                longitud=None if usa_direccion_habitual else ubicacion_pedido.get("longitud"),
                total=resumen.get("total", 0),
            )
            session.add(pedido)
            await session.flush()

            for linea in lineas:
                producto = productos_bd[linea["nombre"]]
                session.add(
                    DetallePedido(
                        pedido_id=pedido.id,
                        producto_id=producto.id,
                        cantidad=linea["cantidad"],
                        precio_unitario=linea["precio_unitario"],
                    )
                )

            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("[order_flow] Falló la creación del pedido para phone=%s", phone)
            return MENSAJE_ERROR_PEDIDO

    clear_draft(phone)
    return (
        f"¡Pedido #{pedido.id} confirmado! Quedó pendiente de revisión, "
        "te contactaremos para coordinar la entrega."
    )


async def _es_cliente_nuevo(phone: str) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(select(Cliente).where(Cliente.telefono == phone))
        cliente = result.scalar_one_or_none()
    return cliente is None


async def procesar_mensaje(
    phone: str,
    message_type: str,
    message_text: str | None,
    location: dict | None,
) -> str:
    es_cliente_nuevo = await _es_cliente_nuevo(phone)
    draft = get_draft(phone)
    estado = draft.get("estado") if draft else None

    if draft is not None and estado == "esperando_confirmacion":
        if draft.get("esperando_nombre"):
            nombre_cliente = (message_text or "").strip()
            draft["nombre_cliente"] = nombre_cliente or draft.get("nombre_cliente")
            draft["esperando_nombre"] = False
            save_draft(phone, draft)
            return await _confirmar_pedido(phone, draft)

        texto_normalizado = (message_text or "").strip().lower()

        if texto_normalizado in CONFIRMACIONES:
            return await _confirmar_pedido(phone, draft)

        resultado = await _interpretar_con_debug(
            phone, message_text or "", es_cliente_nuevo, _contexto_desde_draft(draft)
        )
        return await _aplicar_resultado_llm(phone, resultado, draft, es_cliente_nuevo)

    if draft is not None and estado == "esperando_ubicacion":
        if message_type == "location":
            ubicacion = location or {}
            draft["ubicacion"] = {
                "latitud": ubicacion.get("latitude"),
                "longitud": ubicacion.get("longitude"),
            }
            draft["estado"] = "armando"

            direccion_resuelta = draft.get("usa_direccion_habitual") or (
                draft.get("direccion_texto") is not None
            )
            nombre_resuelto = (not es_cliente_nuevo) or bool(draft.get("nombre_cliente"))
            pedido_completo = bool(draft.get("productos")) and direccion_resuelta and nombre_resuelto

            if pedido_completo:
                resumen = await construir_resumen_pedido(draft.get("productos", []))
                draft["estado"] = "esperando_confirmacion"
                draft["resumen"] = resumen
                save_draft(phone, draft)
                return resumen["texto_resumen"]

            save_draft(phone, draft)
            if draft.get("direccion_texto") is None and not draft.get("usa_direccion_habitual"):
                return (
                    "¡Gracias, ya registré tu ubicación! ¿Puedes indicarme también la "
                    "dirección (calle y número) para este pedido?"
                )
            if not nombre_resuelto:
                return f"¡Gracias, ya registré tu ubicación! {MENSAJE_PEDIR_NOMBRE}"
            return "¡Gracias, ya registré tu ubicación! ¿Hay algo más que quieras agregar a tu pedido?"

        save_draft(phone, draft)
        return MENSAJE_PEDIR_UBICACION

    resultado = await _interpretar_con_debug(
        phone, message_text or "", es_cliente_nuevo, _contexto_desde_draft(draft)
    )
    return await _aplicar_resultado_llm(phone, resultado, draft, es_cliente_nuevo)
