import test from 'node:test';
import assert from 'node:assert/strict';
import { ordenar, posicionEn, siguienteOrden } from './ordenar.js';

const filas = [
  { id: 12, nombre: 'Óscar', total: 5000, estado: 'entregado' },
  { id: 3, nombre: 'ana', total: 2500, estado: 'pendiente' },
  { id: 40, nombre: 'Álvaro', total: null, estado: 'confirmado' },
  { id: 7, nombre: 'Beatriz', total: 12500, estado: 'pendiente' },
];
const columnas = {
  id: f => f.id, nombre: f => f.nombre, total: f => f.total,
  estado: f => posicionEn(['pendiente', 'confirmado', 'en_despacho', 'entregado', 'cancelado'])(f.estado),
};
const ids = (orden) => ordenar(filas, columnas, orden).map(f => f.id);

test('sin orden elegido se mantiene el orden original', () => {
  assert.deepEqual(ids(null), [12, 3, 40, 7]);
  assert.deepEqual(ids({ columna: 'no-existe', sentido: 'asc' }), [12, 3, 40, 7]);
});
test('los números se ordenan como números, de menor a mayor y al revés', () => {
  assert.deepEqual(ids({ columna: 'id', sentido: 'asc' }), [3, 7, 12, 40]);
  assert.deepEqual(ids({ columna: 'id', sentido: 'desc' }), [40, 12, 7, 3]);
});
test('los textos se ordenan en español, sin importar mayúsculas ni tildes', () => {
  assert.deepEqual(ordenar(filas, columnas, { columna: 'nombre', sentido: 'asc' }).map(f => f.nombre), ['Álvaro', 'ana', 'Beatriz', 'Óscar']);
});
test('los vacíos quedan al final en ambos sentidos', () => {
  assert.deepEqual(ids({ columna: 'total', sentido: 'asc' }), [3, 12, 7, 40]);
  assert.deepEqual(ids({ columna: 'total', sentido: 'desc' }), [7, 12, 3, 40]);
});
test('una lista propia ordena por su posición y a igual valor se respeta el orden original', () => {
  assert.deepEqual(ids({ columna: 'estado', sentido: 'asc' }), [3, 7, 40, 12]);
  assert.deepEqual(ids({ columna: 'estado', sentido: 'desc' }), [12, 40, 3, 7]);
});
test('no modifica la lista original', () => {
  const copia = filas.map(f => f.id);
  ordenar(filas, columnas, { columna: 'id', sentido: 'desc' });
  assert.deepEqual(filas.map(f => f.id), copia);
});
test('el primer clic ordena ascendente y el siguiente en la misma columna invierte', () => {
  const a = siguienteOrden(null, 'total');
  assert.deepEqual(a, { columna: 'total', sentido: 'asc' });
  assert.deepEqual(siguienteOrden(a, 'total'), { columna: 'total', sentido: 'desc' });
  assert.deepEqual(siguienteOrden(siguienteOrden(a, 'total'), 'total'), { columna: 'total', sentido: 'asc' });
  assert.deepEqual(siguienteOrden({ columna: 'total', sentido: 'desc' }, 'id'), { columna: 'id', sentido: 'asc' });
});