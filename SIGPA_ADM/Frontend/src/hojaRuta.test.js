import test from 'node:test';
import assert from 'node:assert/strict';
import { cantidadesPedido, cargaRuta, columnasProductos, etiquetaProducto, fechaHoja, filasHoja, formatoTelefono, totalPorCobrar } from './hojaRuta.js';

const linea = (producto_nombre, cantidad_solicitada, precio_unitario) => ({ producto_nombre, cantidad_solicitada, precio_unitario, cantidad_entregada: null });

test('las columnas se llaman como en la planilla de ADM', () => {
  assert.equal(etiquetaProducto('Bidón 20L Recarga'), 'Recarga 20L');
  assert.equal(etiquetaProducto('Bidón 12L Nuevo'), 'Nuevo 12L');
  assert.equal(etiquetaProducto('Dispensador USB'), 'Dispensador USB');
  assert.equal(etiquetaProducto('Promo Dispensador Básico + 2 Bidones'), 'Promo Básico +2');
  assert.equal(etiquetaProducto('Promo Dispensador USB + 1 Bidón'), 'Promo USB +1');
  assert.equal(etiquetaProducto('Agua 500cc'), 'Agua 500cc');
});

test('un producto repetido en el pedido suma cantidades y omite el precio si no coincide', () => {
  assert.deepEqual(cantidadesPedido([linea('Bidón 20L Recarga', 2, 2500), linea('Bidón 20L Recarga', 1, 2500)]), { 'Bidón 20L Recarga': { cantidad: 3, precio: 2500 } });
  assert.deepEqual(cantidadesPedido([linea('Bidón 20L Recarga', 2, 2500), linea('Bidón 20L Recarga', 1, 2000)]), { 'Bidón 20L Recarga': { cantidad: 3, precio: null } });
  assert.deepEqual(cantidadesPedido(undefined), {});
});

const paradas = [
  { id: 30, cliente_id: 3, cliente_nombre: 'Cliente Tres', cliente_telefono: '56911112222', comuna_nombre: 'Viña del Mar', direccion: 'Calle Uno 10', total: 5000 },
  { id: 10, cliente_id: 1, cliente_nombre: null, cliente_telefono: null, comuna_nombre: null, direccion: null, total: 2000 },
];
const detalles = {
  30: { total: 9200, lineas: [linea('Bidón 20L Recarga', 2, 2500), linea('Dispensador USB', 1, 4200)] },
  10: { total: 2000, lineas: [linea('Bidón 12L Recarga', 1, 2000)] },
};
const clientes = [{ id: 3, sector_id: 2 }, { id: 1, sector_id: 1 }];
const sectores = [{ id: 1, nombre: 'Villa Alemana', comuna_nombre: 'Villa Alemana' }, { id: 2, nombre: 'Achupallas', comuna_nombre: 'Viña del Mar' }];
const catalogo = ['Bidón 12L Nuevo', 'Bidón 12L Recarga', 'Bidón 20L Nuevo', 'Bidón 20L Recarga', 'Dispensador Básico', 'Dispensador USB'].map((nombre, i) => ({ id: i + 1, nombre }));

test('cada parada mantiene el orden recibido y suma el sector del cliente', () => {
  const filas = filasHoja(paradas, detalles, clientes, sectores);
  assert.deepEqual(filas.map(f => [f.numero, f.pedidoId]), [[1, 30], [2, 10]]);
  assert.equal(filas[0].sector, 'Achupallas');
  assert.equal(filas[0].zona, 'Viña del Mar · Achupallas');
  assert.equal(filas[0].telefono, '+56 9 1111 2222');
  assert.equal(filas[0].total, 9200, 'el total del detalle es el más reciente');
  // Sin comuna en el pedido se usa la del sector; un sector igual a su comuna no se repite.
  assert.equal(filas[1].comuna, 'Villa Alemana');
  assert.equal(filas[1].sector, null);
  assert.equal(filas[1].zona, 'Villa Alemana');
  // Si la dirección ya trae la comuna, bajo ella solo va el sector.
  assert.equal(filasHoja([{ ...paradas[0], direccion: 'Calle Uno 10, Viña del Mar' }], detalles, clientes, sectores)[0].zona, 'Achupallas');
  assert.equal(filas[1].cliente, 'Cliente 1');
});

test('las columnas fijas van siempre, en el orden de la planilla, y las promos solo si van en la ruta', () => {
  const filas = filasHoja(paradas, detalles, clientes, sectores);
  const columnas = columnasProductos(filas, catalogo);
  assert.deepEqual(columnas, ['Bidón 20L Recarga', 'Bidón 12L Recarga', 'Bidón 20L Nuevo', 'Bidón 12L Nuevo', 'Dispensador Básico', 'Dispensador USB']);
  assert.deepEqual(cargaRuta(filas, columnas), { 'Bidón 20L Recarga': 2, 'Bidón 12L Recarga': 1, 'Bidón 20L Nuevo': 0, 'Bidón 12L Nuevo': 0, 'Dispensador Básico': 0, 'Dispensador USB': 1 });
  assert.equal(totalPorCobrar(filas), 11200);
  // Las promos van después de las fijas en el orden del catálogo; lo que no está en el catálogo, al final.
  const conPromos = [...catalogo, { nombre: 'Promo Dispensador Básico + 1 Bidón' }, { nombre: 'Promo Dispensador USB + 2 Bidones' }];
  assert.deepEqual(columnasProductos([{ productos: { 'Agua 500cc': {}, 'Promo Dispensador USB + 2 Bidones': {}, 'Promo Dispensador Básico + 1 Bidón': {}, 'Dispensador USB': {} } }], conPromos).slice(6),
    ['Promo Dispensador Básico + 1 Bidón', 'Promo Dispensador USB + 2 Bidones', 'Agua 500cc']);
});

test('sin detalle cargado la parada queda sin productos y usa el total del listado', () => {
  const [fila] = filasHoja([paradas[1]], {});
  assert.deepEqual(fila.productos, {});
  assert.equal(fila.total, 2000);
});

test('teléfono y fecha en formato chileno', () => {
  assert.equal(formatoTelefono('+56 9 8765 4321'), '+56 9 8765 4321');
  assert.equal(formatoTelefono('987654321'), '987654321');
  assert.equal(formatoTelefono(null), null);
  assert.equal(fechaHoja('2026-10-12'), 'lunes 12-10-2026');
  assert.equal(fechaHoja(''), '');
});
