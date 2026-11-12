import test from 'node:test';
import assert from 'node:assert/strict';
import { agregarCatalogo, ajustarEntregada, datosEntrega, errorEntrega, formEntrega, leerCantidad, puedeEntregarse, resumenEntrega } from './entregas.js';

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

test('el repartidor entrega menos o más de lo pedido, y el total se recalcula', () => {
  const pedido = [{ id: 1, producto_nombre: 'Bidón 20L', cantidad_solicitada: 3, cantidad_entregada: null, precio_unitario: 2500 }];
  const menos = ajustarEntregada(formEntrega(pedido), 1, -1);
  assert.equal(menos[0].entregada, '2');
  assert.equal(resumenEntrega(menos).total, 5000);
  assert.equal(resumenEntrega(menos).totalPedido, 7500);
  assert.equal(resumenEntrega(menos).parcial, true);
  const mas = ajustarEntregada(formEntrega(pedido), 1, 1);
  assert.equal(mas[0].entregada, '4');
  assert.equal(resumenEntrega(mas).total, 10000);
  assert.deepEqual(resumenEntrega(mas).demas, ['Bidón 20L']);
  assert.equal(ajustarEntregada([{ ...menos[0], entregada: '0' }], 1, -1)[0].entregada, '0');
});

const catalogo = [
  { id: 4, nombre: 'Bidón 20L Recarga', precio_unitario: 2500 },
  { id: 1, nombre: 'Bidón 12L Nuevo', precio_unitario: 6000 },
  { id: 5, nombre: 'Dispensador Básico', precio_unitario: '7000.00' },
];
const pedidoUno = [{ id: 31, producto_id: 4, producto_nombre: 'Bidón 20L Recarga', cantidad_solicitada: 1, cantidad_entregada: null, precio_unitario: 2500 }];

test('el catálogo agrega en 0 los productos que no están en el pedido', () => {
  const form = agregarCatalogo(formEntrega(pedidoUno), catalogo);
  assert.deepEqual(form.map(l => [l.producto, l.entregada, Boolean(l.extra)]), [
    ['Bidón 20L Recarga', '1', false],
    ['Bidón 12L Nuevo', '0', true],
    ['Dispensador Básico', '0', true],
  ]);
  // Sin subir nada, la entrega es igual a la de siempre.
  assert.deepEqual(datosEntrega(form), { lineas: [{ detalle_id: 31, cantidad_entregada: 1 }] });
  assert.equal(resumenEntrega(form).total, 2500);
  assert.deepEqual(agregarCatalogo(formEntrega(pedidoUno), undefined).length, 1);
});

test('un producto que no pidió se cobra al precio del catálogo y se envía aparte', () => {
  let form = agregarCatalogo(formEntrega(pedidoUno), catalogo);
  form = ajustarEntregada(form, 'producto-1', 1);
  form = ajustarEntregada(form, 'producto-5', 1);
  form = ajustarEntregada(form, 'producto-5', 1);
  const r = resumenEntrega(form);
  assert.equal(r.total, 2500 + 6000 + 14000);
  assert.equal(r.totalPedido, 2500);
  assert.deepEqual(datosEntrega(form), {
    lineas: [{ detalle_id: 31, cantidad_entregada: 1 }],
    extras: [{ producto_id: 1, cantidad: 1 }, { producto_id: 5, cantidad: 2 }],
  });
  // Solo lo agregado, sin lo pedido, también es una entrega.
  const soloExtra = ajustarEntregada(form, 31, -1);
  assert.equal(errorEntrega(resumenEntrega(soloExtra)), null);
});
