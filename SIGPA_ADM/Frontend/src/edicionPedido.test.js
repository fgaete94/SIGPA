import test from 'node:test';
import assert from 'node:assert/strict';
import { datosEdicion, formEdicion, puedeEditarse, totalEdicion } from './pedidos.js';

const productos = [{ id: 4, nombre: 'Bidón 20L Recarga', precio_unitario: '2600.00' }, { id: 6, nombre: 'Dispensador USB', precio_unitario: 7000 }];
const pedido = { id: 50, estado: 'confirmado', direccion_despacho: null, lineas: [{ producto_id: 4, cantidad_solicitada: 2, precio_unitario: 2500, producto_nombre: 'Bidón 20L Recarga' }] };

test('solo se editan pedidos pendientes, confirmados o en despacho', () => {
  for (const e of ['pendiente', 'confirmado', 'en_despacho']) assert.equal(puedeEditarse(e), true);
  for (const e of ['entregado', 'cancelado']) assert.equal(puedeEditarse(e), false);
});

test('el formulario parte con lo que tiene el pedido y sin cambios no se guarda', () => {
  const form = formEdicion(pedido);
  assert.deepEqual(form, { direccion_despacho: '', lineas: [{ producto_id: '4', cantidad: '2' }] });
  assert.equal(datosEdicion(form, pedido, productos).error, 'No hay cambios para guardar.');
});

test('solo se envía lo que cambió', () => {
  const form = formEdicion(pedido);
  const masProductos = { ...form, lineas: [{ producto_id: '4', cantidad: '3' }, { producto_id: '6', cantidad: '1' }] };
  assert.deepEqual(datosEdicion(masProductos, pedido, productos), { datos: { lineas: [{ producto_id: 4, cantidad_solicitada: 3 }, { producto_id: 6, cantidad_solicitada: 1 }] }, direccionCambia: false });
  const otraDireccion = { ...form, direccion_despacho: ' Oficina 200 ' };
  assert.deepEqual(datosEdicion(otraDireccion, pedido, productos), { datos: { direccion_despacho: 'Oficina 200' }, direccionCambia: true });
  // Mismas líneas en otro orden o la misma dirección con otras mayúsculas: no son cambios.
  const conDireccion = { ...pedido, direccion_despacho: 'Calle Dos 5', lineas: [...pedido.lineas, { producto_id: 6, cantidad_solicitada: 1, precio_unitario: 7000 }] };
  const reordenado = { direccion_despacho: 'calle  dos 5', lineas: [{ producto_id: '6', cantidad: '1' }, { producto_id: '4', cantidad: '2' }] };
  assert.equal(datosEdicion(reordenado, conDireccion, productos).error, 'No hay cambios para guardar.');
  // Vaciar la dirección = volver a la del cliente.
  assert.deepEqual(datosEdicion({ ...reordenado, direccion_despacho: '' }, conDireccion, productos).datos, { direccion_despacho: null });
});

test('valida igual que un pedido nuevo', () => {
  const form = formEdicion(pedido);
  assert.equal(datosEdicion({ ...form, lineas: [{ producto_id: '', cantidad: '1' }] }, pedido, productos).error, 'Agrega al menos un producto.');
  assert.match(datosEdicion({ ...form, lineas: [{ producto_id: '4', cantidad: '0' }] }, pedido, productos).error, /entre 1 y 999/);
  assert.match(datosEdicion({ ...form, lineas: [{ producto_id: '4', cantidad: '1' }, { producto_id: '4', cantidad: '2' }] }, pedido, productos).error, /dos veces/);
});

test('el total mantiene el precio pactado de lo que ya estaba', () => {
  // Bidón ya estaba a 2500 (hoy 2600); el dispensador es nuevo y toma el precio vigente.
  assert.equal(totalEdicion([{ producto_id: '4', cantidad: '3' }, { producto_id: '6', cantidad: '1' }], productos, pedido), 3 * 2500 + 7000);
  assert.equal(totalEdicion([{ producto_id: '4', cantidad: 'x' }], productos, pedido), 0);
});
