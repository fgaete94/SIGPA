import test from 'node:test';
import assert from 'node:assert/strict';
import { datosPedido, pedidoForm, totalEstimado } from './pedidos.js';
import { datosProducto, productoForm } from './inventario.js';

const productos = [{ id: 1, nombre: 'Bidón 20L Recarga', precio_unitario: '2500.00', stock: 0, capacidad_litros: '20.00' }, { id: 5, nombre: 'Dispensador Básico', precio_unitario: 7000, stock: 3, capacidad_litros: null }];

test('pedido manual: arma las líneas, ignora las vacías y estima el total', () => {
  const form = { ...pedidoForm(), cliente_id: '7', direccion_despacho: '  ', lineas: [{ producto_id: '1', cantidad: '3' }, { producto_id: '', cantidad: '1' }, { producto_id: '5', cantidad: ' 1 ' }] };
  assert.deepEqual(datosPedido(form, productos), { datos: { cliente_id: 7, direccion_despacho: null, lineas: [{ producto_id: 1, cantidad_solicitada: 3 }, { producto_id: 5, cantidad_solicitada: 1 }] } });
  assert.equal(totalEstimado(form.lineas, productos), 14500);
});

test('pedido manual: errores claros', () => {
  assert.equal(datosPedido(pedidoForm(), productos).error, 'Elige el cliente.');
  assert.equal(datosPedido({ ...pedidoForm(), cliente_id: '7' }, productos).error, 'Agrega al menos un producto.');
  assert.match(datosPedido({ ...pedidoForm(), cliente_id: '7', lineas: [{ producto_id: '1', cantidad: '1' }, { producto_id: '1', cantidad: '2' }] }, productos).error, /Bidón 20L Recarga está dos veces/);
  for (const cantidad of ['0', '1.5', 'dos', '1000', '']) assert.match(datosPedido({ ...pedidoForm(), cliente_id: '7', lineas: [{ producto_id: '5', cantidad }] }, productos).error, /entre 1 y 999/);
});

test('producto nuevo: precio en pesos chilenos, stock entero y capacidad opcional', () => {
  const form = { nombre: '  Bidón  6L Recarga ', precio_unitario: '$ 1.800', stock: '10', capacidad_litros: '6' };
  assert.deepEqual(datosProducto(form, productos), { datos: { nombre: 'Bidón 6L Recarga', precio_unitario: 1800, stock: 10, capacidad_litros: 6 } });
  assert.equal(datosProducto({ ...form, capacidad_litros: '1,5' }, productos).datos.capacidad_litros, 1.5);
  assert.equal(datosProducto({ ...form, capacidad_litros: '' }, productos).datos.capacidad_litros, null);
  assert.match(datosProducto({ ...form, nombre: 'dispensador básico' }, productos).error, /Ya existe/);
  assert.match(datosProducto({ ...form, precio_unitario: '0' }, productos).error, /mayor que 0/);
  assert.match(datosProducto({ ...form, precio_unitario: '1800,5' }, productos).error, /pesos/);
  assert.match(datosProducto({ ...form, stock: '-1' }, productos).error, /stock/);
  assert.match(datosProducto({ ...form, capacidad_litros: '0' }, productos).error, /litros/);
});

test('producto editado: solo los campos que cambiaron', () => {
  const original = productos[0];
  assert.deepEqual(datosProducto(productoForm(original), productos, original), { cambios: {} });
  assert.deepEqual(productoForm(original), { nombre: 'Bidón 20L Recarga', precio_unitario: '2500', stock: '0', capacidad_litros: '20' });
  assert.deepEqual(datosProducto({ ...productoForm(original), precio_unitario: '2.700', stock: '40' }, productos, original), { cambios: { precio_unitario: 2700, stock: 40 } });
});
