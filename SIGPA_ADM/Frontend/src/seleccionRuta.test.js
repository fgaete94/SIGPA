import test from 'node:test';
import assert from 'node:assert/strict';
import { SIN_COMUNA, SIN_DIA } from './agrupacionDespacho.js';
import { desmarcarVisibles, enriquecerPendientes, filtrarPendientes, marcarVisibles, opcionesSeleccion, sinUbicacionEn } from './seleccionRuta.js';

const clientes = [
  { id: 1, dia_reparto: 'Martes', comuna_nombre: 'Viña del Mar' },
  { id: 2, dia_reparto: 'Martes', comuna_nombre: 'Quilpué' },
  { id: 3, dia_reparto: 'Lunes', comuna_nombre: 'Viña del Mar' },
  { id: 4, dia_reparto: null, comuna_nombre: null },
];
const pendiente = (pedido_id, cliente_id, extra = {}) => ({ pedido_id, cliente_id, latitud: -33, longitud: -71.5, motivo_revision_direccion: null, ...extra });
const pendientes = [
  pendiente(10, 1), pendiente(11, 1, { latitud: null, longitud: null }), pendiente(12, 2),
  pendiente(13, 3), pendiente(14, 4), pendiente(15, 1, { latitud: null, longitud: null, motivo_revision_direccion: 'dirección no encontrada' }),
];
const lista = enriquecerPendientes(pendientes, clientes);

test('cada pedido toma el día y la comuna de su cliente y si le faltan coordenadas', () => {
  assert.deepEqual(lista.map(p => [p.pedido_id, p.dia, p.comuna, p.sinUbicacion]), [
    [10, 'Martes', 'Viña del Mar', false], [11, 'Martes', 'Viña del Mar', true], [12, 'Martes', 'Quilpué', false],
    [13, 'Lunes', 'Viña del Mar', false], [14, SIN_DIA, SIN_COMUNA, false], [15, 'Martes', 'Viña del Mar', true],
  ]);
});

test('opciones de día en orden de la semana y comunas del día elegido', () => {
  const { dias, comunas } = opcionesSeleccion(lista, 'Martes');
  assert.deepEqual(dias, [['Lunes', 1], ['Martes', 4], [SIN_DIA, 1]]);
  assert.deepEqual(comunas, [['Quilpué', 1], ['Viña del Mar', 3]]);
  assert.deepEqual(opcionesSeleccion(lista).comunas, [['Quilpué', 1], ['Viña del Mar', 4], [SIN_COMUNA, 1]]);
});

test('filtra por día y comuna', () => {
  assert.deepEqual(filtrarPendientes(lista, { dia: 'Martes', comuna: 'Viña del Mar' }).map(p => p.pedido_id), [10, 11, 15]);
  assert.equal(filtrarPendientes(lista).length, 6);
});

test('marcar los visibles respeta el máximo y deja fuera los que tienen la dirección por revisar', () => {
  const martes = filtrarPendientes(lista, { dia: 'Martes' });
  assert.deepEqual(marcarVisibles([], martes, 30), { ids: [10, 11, 12], agregados: 3, porRevisar: 1, sinCupo: 0 });
  // Ya había uno marcado de otro día y el máximo es 3: solo caben 2 más.
  assert.deepEqual(marcarVisibles([13], martes, 3), { ids: [13, 10, 11], agregados: 2, porRevisar: 1, sinCupo: 1 });
  assert.deepEqual(desmarcarVisibles([13, 10, 11], martes), [13]);
  assert.equal(sinUbicacionEn(lista, [10, 11, 13]), 1);
});
