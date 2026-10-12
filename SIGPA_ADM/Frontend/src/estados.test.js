import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCION_DESPACHO, despachables, textoAccion, textoCambio } from './estados.js';
import { fechaHora } from './fechas.js';

test('despacho: solo confirmado ↔ en despacho, como el backend', () => {
  assert.deepEqual(ACCION_DESPACHO.confirmado, { destino: 'en_despacho', texto: 'Salió a reparto' });
  assert.deepEqual(ACCION_DESPACHO.en_despacho, { destino: 'confirmado', texto: 'Volver a confirmado' });
  for (const estado of ['pendiente', 'entregado', 'cancelado']) assert.equal(ACCION_DESPACHO[estado], undefined);
  assert.deepEqual(despachables([{ id: 1, estado: 'confirmado' }, { id: 2, estado: 'en_despacho' }, { id: 3, estado: 'confirmado' }]), [1, 3]);
});

test('historial en texto, con la hora de Chile', () => {
  assert.equal(textoCambio({ estado_anterior: 'confirmado', estado_nuevo: 'en_despacho' }), 'Confirmado → En despacho');
  assert.equal(textoCambio({ estado_anterior: null, estado_nuevo: 'pendiente' }), 'Creado como pendiente');
  assert.equal(textoAccion('planificar_ruta'), 'Planificación de ruta');
  assert.equal(textoAccion('otra_accion'), 'otra_accion');
  // 23:36 UTC del 5 de octubre = 20:36 en Chile (UTC-3).
  assert.match(fechaHora('2026-10-05T23:36:49.864993'), /05-10-2026,? 20:36/);
});
