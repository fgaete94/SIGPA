import test from 'node:test';
import assert from 'node:assert/strict';
import { cambiada, cambiarViaje, datosConfirmar, enlacePunto, estadoVistaPrevia, moverParada, puedeConfirmar, reponer, restablecer, sacar, viajesVista } from './vistaPrevia.js';

const parada = (pedido_id, orden_entrega) => ({ pedido_id, orden_entrega, cliente_nombre: `C${pedido_id}`, direccion_texto: `Calle ${pedido_id}`, latitud: -33 - pedido_id / 100, longitud: -71.5, version: '2026-10-18T12:00:00.123456' });
const respuesta = {
  vista_previa: true,
  ruta: [parada(20, 2), parada(10, 1), parada(30, 3)],
  sin_resolver: [{ pedido_id: 40, motivo: 'dirección no encontrada', cliente_nombre: 'C40', direccion_texto: 'Sin número', version: '2026-10-18T11:00:00' }],
};
const ids = estado => estado.paradas.map(p => p.pedido_id);

test('parte con el orden que propone n8n y sin cambios', () => {
  const e = estadoVistaPrevia(respuesta);
  assert.deepEqual(ids(e), [10, 20, 30]);
  assert.equal(cambiada(e), false);
  assert.equal(puedeConfirmar(e), true);
});

test('mover, sacar y reponer paradas cambia la ruta; restablecer vuelve a la propuesta', () => {
  let e = moverParada(estadoVistaPrevia(respuesta), null, 2, 1);
  assert.deepEqual(ids(e), [10, 30, 20]);
  assert.equal(cambiada(e), true);
  e = sacar(e, 30);
  assert.deepEqual(ids(e), [10, 20]);
  assert.deepEqual(e.fuera.map(p => p.pedido_id), [30]);
  e = reponer(e, 30);
  assert.deepEqual(ids(e), [10, 20, 30], 'vuelve como última parada');
  assert.equal(cambiada(e), false, 'mismo orden que la propuesta');
  e = restablecer(sacar(moverParada(e, null, 0, 1), 10));
  assert.deepEqual(ids(e), [10, 20, 30]);
  assert.deepEqual(e.fuera, []);
  // Movimientos fuera de rango y pedidos que no están no cambian nada.
  assert.deepEqual(ids(moverParada(e, null, 0, 9)), [10, 20, 30]);
  assert.equal(sacar(e, 99), e);
  assert.equal(reponer(e, 10), e);
});

test('al confirmar se envía el orden final con coordenadas y versión; los sacados no van', () => {
  const e = sacar(moverParada(estadoVistaPrevia(respuesta), null, 1, 2), 10);
  assert.deepEqual(datosConfirmar(e), {
    paradas: [
      { pedido_id: 30, latitud: -33.3, longitud: -71.5, version: '2026-10-18T12:00:00.123456' },
      { pedido_id: 20, latitud: -33.2, longitud: -71.5, version: '2026-10-18T12:00:00.123456' },
    ],
    sin_resolver: [{ pedido_id: 40, motivo: 'dirección no encontrada', version: '2026-10-18T11:00:00' }],
  });
});

test('sin direcciones ubicadas se puede confirmar para marcarlas; sin nada, no', () => {
  const soloSinResolver = estadoVistaPrevia({ ruta: [], sin_resolver: respuesta.sin_resolver });
  assert.equal(puedeConfirmar(soloSinResolver), true);
  assert.equal(puedeConfirmar(sacar(estadoVistaPrevia({ ruta: [parada(1, 1)], sin_resolver: [] }), 1)), false);
});

test('enlace al punto que ubicó n8n', () => {
  assert.equal(enlacePunto({ latitud: -33.05, longitud: -71.6 }), 'https://www.google.com/maps/search/?api=1&query=-33.05,-71.6');
});

test('arrastrar una parada a otra posición cambia la ruta propuesta', () => {
  const e = moverParada(estadoVistaPrevia(respuesta), null, 2, 0);
  assert.deepEqual(ids(e), [30, 10, 20]);
  assert.equal(cambiada(e), true);
  assert.equal(cambiada(moverParada(e, null, 0, 2)), false);
});

// EP-04: orden_entrega de 1 a N dentro de cada viaje. La ruta no trae el peso de cada parada: sale de los pendientes.
const enViaje = (pedido_id, viaje, orden_entrega) => ({ ...parada(pedido_id, orden_entrega), viaje });
const conViajes = {
  ruta: [enViaje(21, 2, 1), enViaje(12, 1, 2), enViaje(11, 1, 1), enViaje(22, 2, 2), enViaje(13, 1, 3)],
  viajes: [{ viaje: 1, peso_kg: 1000, termina_en: 'planta', total_paradas: 3 }, { viaje: 2, peso_kg: 450, termina_en: 'casa', total_paradas: 2 }],
  sin_resolver: [],
};
const pesos = { 11: 500, 12: 300, 13: 200, 21: 250, 22: 200 };
const resumen = e => viajesVista(e).grupos.map(v => [v.numero, v.peso_kg, v.termina_en, v.paradas.map(p => p.pedido_id)]);

test('con viajes, las paradas quedan agrupadas por viaje y en su orden', () => {
  const e = estadoVistaPrevia(conViajes, pesos);
  assert.deepEqual(ids(e), [11, 12, 13, 21, 22]);
  assert.deepEqual(resumen(e), [[1, 1000, 'planta', [11, 12, 13]], [2, 450, 'casa', [21, 22]]]);
  assert.deepEqual(viajesVista(e).problemas, []);
});

test('arrastrar mueve solo dentro del viaje; reponer devuelve la parada al final de su viaje', () => {
  let e = moverParada(estadoVistaPrevia(conViajes, pesos), 2, 1, 0);
  assert.deepEqual(ids(e), [11, 12, 13, 22, 21]);
  e = moverParada(e, 1, 0, 2);
  assert.deepEqual(ids(e), [12, 13, 11, 22, 21]);
  e = reponer(sacar(e, 13), 13);
  assert.deepEqual(ids(e), [12, 11, 13, 22, 21], 'vuelve al viaje 1, no al final de la ruta');
});

test('pasar una parada a otro viaje recalcula pesos y destino, y avisa lo que el backend rechazaría', () => {
  let e = cambiarViaje(estadoVistaPrevia(conViajes, pesos), 13, 2);
  assert.equal(cambiada(e), true);
  // El viaje 2 queda con 650 kg y sigue siendo el más liviano: termina en casa.
  assert.deepEqual(resumen(e), [[1, 800, 'planta', [11, 12]], [2, 650, 'casa', [21, 22, 13]]]);
  assert.equal(puedeConfirmar(e), true);
  e = cambiarViaje(e, 12, 2);
  assert.deepEqual(resumen(e), [[1, 500, 'planta', [11]], [2, 950, 'planta', [21, 22, 13, 12]]]);
  assert.match(viajesVista(e).problemas[0], /más liviano/);
  assert.equal(puedeConfirmar(e), false);
  // Todo en el viaje 1: 1.450 kg, más de lo que carga el camión.
  e = cambiarViaje(cambiarViaje(estadoVistaPrevia(conViajes, pesos), 21, 1), 22, 1);
  assert.deepEqual(resumen(e), [[1, 1450, 'planta', [11, 12, 13, 21, 22]]]);
  assert.match(viajesVista(e).problemas[0], /pesa 1\.450 kg/);
});

test('si un viaje queda vacío los siguientes se renumeran; al confirmar va el viaje de cada parada', () => {
  let e = estadoVistaPrevia(conViajes, pesos);
  for (const id of [11, 12, 13]) e = sacar(e, id);
  assert.deepEqual(resumen(e), [[1, 450, 'planta', [21, 22]]], 'un solo viaje: termina en planta');
  assert.deepEqual(datosConfirmar(e).paradas.map(p => [p.pedido_id, p.viaje]), [[21, 1], [22, 1]]);
  e = moverParada(estadoVistaPrevia(conViajes, pesos), 1, 2, 0);
  assert.deepEqual(datosConfirmar(e).paradas.map(p => [p.pedido_id, p.viaje]), [[13, 1], [11, 1], [12, 1], [21, 2], [22, 2]]);
});

test('sin el peso de las paradas se muestra lo que informó el backend mientras no se cambie la ruta', () => {
  const e = estadoVistaPrevia(conViajes);
  assert.deepEqual(resumen(e)[1].slice(0, 3), [2, 450, 'casa']);
  assert.deepEqual(resumen(sacar(e, 21))[1].slice(0, 3), [2, null, null]);
  assert.equal(puedeConfirmar(sacar(e, 21)), true, 'sin pesos no se valida aquí: lo valida el backend');
});
