import test from 'node:test';
import assert from 'node:assert/strict';
import { cambiada, datosConfirmar, enlacePunto, estadoVistaPrevia, mover, puedeConfirmar, reponer, restablecer, sacar } from './vistaPrevia.js';

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
  let e = mover(estadoVistaPrevia(respuesta), 2, -1);
  assert.deepEqual(ids(e), [10, 30, 20]);
  assert.equal(cambiada(e), true);
  e = sacar(e, 30);
  assert.deepEqual(ids(e), [10, 20]);
  assert.deepEqual(e.fuera.map(p => p.pedido_id), [30]);
  e = reponer(e, 30);
  assert.deepEqual(ids(e), [10, 20, 30], 'vuelve como última parada');
  assert.equal(cambiada(e), false, 'mismo orden que la propuesta');
  e = restablecer(sacar(mover(e, 0, 1), 10));
  assert.deepEqual(ids(e), [10, 20, 30]);
  assert.deepEqual(e.fuera, []);
  // Movimientos fuera de rango y pedidos que no están no cambian nada.
  assert.deepEqual(ids(mover(e, 0, -1)), [10, 20, 30]);
  assert.equal(sacar(e, 99), e);
  assert.equal(reponer(e, 10), e);
});

test('al confirmar se envía el orden final con coordenadas y versión; los sacados no van', () => {
  const e = sacar(mover(estadoVistaPrevia(respuesta), 1, 1), 10);
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
