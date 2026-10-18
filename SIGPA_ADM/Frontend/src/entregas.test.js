import test from 'node:test';
import assert from 'node:assert/strict';
import { datosEntrega, errorEntrega, formEntrega, leerCantidad, puedeEntregarse, resumenEntrega } from './entregas.js';

const lineas = [
  { id: 11, producto_nombre: 'Bidón 20L Recarga', cantidad_solicitada: 3, cantidad_entregada: null, precio_unitario: '2500.00' },
  { id: 12, producto_nombre: 'Dispensador USB', cantidad_solicitada: 1, cantidad_entregada: null, precio_unitario: 7000 },
];

test('solo se entregan pedidos confirmados o en despacho', () => {
  assert.equal(puedeEntregarse('confirmado'), true);
  assert.equal(puedeEntregarse('en_despacho'), true);
  for (const estado of ['pendiente', 'entregado', 'cancelado']) assert.equal(puedeEntregarse(estado), false);
});

test('el formulario parte con todo lo pedido y el total del pedido', () => {
  const form = formEntrega(lineas);
  assert.deepEqual(form.map(l => l.entregada), ['3', '1']);
  const r = resumenEntrega(form);
  assert.equal(r.total, 14500);
  assert.equal(r.totalPedido, 14500);
  assert.equal(r.parcial, false);
  assert.equal(errorEntrega(r), null);
  assert.deepEqual(datosEntrega(form), { lineas: [{ detalle_id: 11, cantidad_entregada: 3 }, { detalle_id: 12, cantidad_entregada: 1 }] });
});

test('una entrega parcial recalcula el total y avisa', () => {
  const form = formEntrega(lineas).map(l => (l.id === 11 ? { ...l, entregada: '2' } : { ...l, entregada: '0' }));
  const r = resumenEntrega(form);
  assert.equal(r.total, 5000);
  assert.equal(r.parcial, true);
  assert.equal(errorEntrega(r), null);
});

test('cantidades inválidas o nada entregado no se pueden registrar', () => {
  assert.equal(leerCantidad(' 2 '), 2);
  for (const malo of ['', '-1', '1.5', 'dos', '12345']) assert.equal(leerCantidad(malo), null);
  const invalida = resumenEntrega(formEntrega(lineas).map(l => ({ ...l, entregada: l.id === 12 ? 'x' : '3' })));
  assert.match(errorEntrega(invalida), /Dispensador USB/);
  const nada = resumenEntrega(formEntrega(lineas).map(l => ({ ...l, entregada: '0' })));
  assert.match(errorEntrega(nada), /ninguna unidad/);
});

test('entregar más de lo pedido se permite pero se informa', () => {
  const r = resumenEntrega(formEntrega(lineas).map(l => (l.id === 11 ? { ...l, entregada: '4' } : l)));
  assert.deepEqual(r.demas, ['Bidón 20L Recarga']);
  assert.equal(r.total, 17000);
});

test('si ya había cantidades entregadas se parte de ellas', () => {
  assert.deepEqual(formEntrega([{ ...lineas[0], cantidad_entregada: 2 }]).map(l => l.entregada), ['2']);
});
