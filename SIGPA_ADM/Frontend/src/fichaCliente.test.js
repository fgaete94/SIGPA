import test from 'node:test';
import assert from 'node:assert/strict';
import { resumenCliente, textoTipo } from './fichaCliente.js';

const pedido = (id, estado, total, creado_en) => ({ id, estado, total, creado_en });

test('resume el historial: comprado es lo entregado y el último es el más reciente', () => {
  const r = resumenCliente([
    pedido(1, 'entregado', '5000.00', '2026-10-01T15:00:00'),
    pedido(4, 'pendiente', 2500, '2026-10-17T15:00:00'),
    pedido(2, 'cancelado', 9000, '2026-10-05T15:00:00'),
    pedido(3, 'entregado', 7500, '2026-10-10T15:00:00'),
    pedido(5, 'en_despacho', 2500, '2026-10-17T15:00:00'),
  ]);
  assert.deepEqual(r.pedidos.map(p => p.id), [5, 4, 3, 2, 1]);
  assert.equal(r.cantidad, 5);
  assert.equal(r.entregados, 2);
  assert.equal(r.enCurso, 2);
  assert.equal(r.cancelados, 1);
  assert.equal(r.comprado, 12500);
  assert.equal(r.promedio, 6250);
  assert.equal(r.ultimo.id, 5);
  assert.equal(r.ultimaEntrega.id, 3);
});

test('un cliente sin pedidos', () => {
  const r = resumenCliente([]);
  assert.equal(r.cantidad, 0);
  assert.equal(r.comprado, 0);
  assert.equal(r.promedio, null);
  assert.equal(r.ultimo, null);
  assert.equal(r.ultimaEntrega, null);
  assert.equal(resumenCliente(undefined).cantidad, 0);
});

test('el tipo muestra su equivalente B2C/B2B', () => {
  assert.equal(textoTipo('Particular'), 'Particular (B2C)');
  assert.equal(textoTipo('Empresa'), 'Empresa (B2B)');
  assert.equal(textoTipo('Institución'), 'Institución');
  assert.equal(textoTipo(null), 'Sin tipo');
});
