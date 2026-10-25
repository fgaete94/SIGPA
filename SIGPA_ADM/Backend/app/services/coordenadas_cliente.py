"""
Historia #159: las coordenadas de un pedido se guardan también en su
cliente, para que el siguiente pedido ya las traiga y la dirección no se
vuelva a geocodificar ni a corregir a mano.

Solo aplica cuando el pedido va a la dirección registrada del cliente. Un
pedido con otra dirección de despacho (por ejemplo, una oficina) no le
cambia la ubicación al cliente.
"""
from app.models.pedido import Pedido


def _normalizar(direccion: str | None) -> str:
    # Ignora mayúsculas y espacios repetidos al comparar direcciones.
    return " ".join((direccion or "").split()).casefold()


def usa_direccion_del_cliente(pedido: Pedido) -> bool:
    """
    True si el pedido va a la dirección registrada del cliente: no tiene
    dirección de despacho propia (pedidos del panel) o es la misma que la
    del cliente (el bot copia la del cliente al crear el pedido).
    """
    if pedido.cliente is None:
        return False

    if not pedido.direccion_despacho:
        return True

    return _normalizar(pedido.direccion_despacho) == _normalizar(pedido.cliente.direccion)


def copiar_coordenadas_al_cliente(pedido: Pedido, sobrescribir: bool) -> bool:
    """
    Copia la latitud y longitud del pedido a su cliente. Devuelve True si
    el cliente cambió, para que quien llama registre la auditoría.

    sobrescribir=True: corrección manual de la ejecutiva, reemplaza lo que
    tenga el cliente.
    sobrescribir=False: coordenadas geocodificadas por n8n, solo completan
    un cliente sin coordenadas (no reemplazan una corrección manual).
    """
    if pedido.latitud is None or pedido.longitud is None:
        return False

    if not usa_direccion_del_cliente(pedido):
        return False

    cliente = pedido.cliente
    cliente_tiene_coordenadas = cliente.latitud is not None and cliente.longitud is not None

    if cliente_tiene_coordenadas and not sobrescribir:
        return False

    if cliente_tiene_coordenadas and (
        float(cliente.latitud) == float(pedido.latitud)
        and float(cliente.longitud) == float(pedido.longitud)
    ):
        return False

    cliente.latitud = pedido.latitud
    cliente.longitud = pedido.longitud
    return True