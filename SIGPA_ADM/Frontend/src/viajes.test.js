import test from 'node:test';
import assert from 'node:assert/strict';
import { SIN_VIAJE, agruparPorViaje, contarViajes, encabezadoViaje, ordenViaje, problemasViajes, reemplazarViaje, resumenPeso, terminaEn, tituloGrupo } from './viajes.js';

const parada = (pedido_id, viaje, orden_entrega) => ({ pedido_id, viaje, orden_entrega });

test('ordena por viaje y, dentro de cada viaje, por parada; sin viaje al final', () => {
  const ps = [parada(1, 2, 1), parada(2, 1, 2), parada(3, null, 1), parada(4, 1, 1)];
  assert.deepEqual([...ps].sort(ordenViaje).map(p => p.pedido_id), [4, 2, 1, 3]);
});

test('agrupa las paradas por viaje con su peso y destino', () => {
  const grupos = agruparPorViaje([parada(4, 1, 1), parada(2, 1, 2), parada(1, 2, 1)], [{ viaje: 2, peso_kg: 380, termina_en: 'casa' }, { viaje: 1, peso_kg: 1240.5, termina_en: 'planta' }]);
  assert.deepEqual(grupos.map(g => [g.viaje, g.peso_kg, g.paradas.map(p => p.pedido_id)]), [[1, 1240.5, [4, 2]], [2, 380, [1]]]);
  assert.equal(encabezadoViaje(grupos[0]), 'Viaje 1 · 1.240,5 kg · termina en planta');
  assert.equal(encabezadoViaje(grupos[1]), 'Viaje 2 · 380 kg · termina en casa del despachador');
  assert.equal(encabezadoViaje({ viaje: 3 }), 'Viaje 3', 'sin datos del viaje, solo el número');
  assert.equal(encabezadoViaje({ viaje: null }), null, 'ruta anterior a los viajes: sin encabezado');
});

test('los pedidos sin viaje de una ruta anterior no cuentan como viaje y llevan su propio título', () => {
  const mixtos = agruparPorViaje([parada(1, 1, 1), parada(2, 2, 1), parada(3, null, 1)]);
  assert.equal(contarViajes(mixtos), 2);
  assert.deepEqual(mixtos.map(g => tituloGrupo(g, mixtos)), ['Viaje 1', 'Viaje 2', SIN_VIAJE]);
  const antiguos = agruparPorViaje([parada(3, null, 1), parada(4, null, 2)]);
  assert.equal(contarViajes(antiguos), 0);
  assert.equal(tituloGrupo(antiguos[0], antiguos), null, 'si toda la ruta es anterior, sin título');
});

test('reemplazar el orden de un viaje no mueve los de otros viajes', () => {
  const ps = [parada(1, 1, 1), parada(2, 1, 2), parada(3, 2, 1), parada(4, 2, 2)];
  assert.deepEqual(reemplazarViaje(ps, 2, [ps[3], ps[2]]).map(p => p.pedido_id), [1, 2, 4, 3]);
});

test('dónde termina cada viaje: solo el último, con 2 o más viajes y menos de 800 kg, en casa', () => {
  assert.deepEqual(terminaEn([500]), ['planta'], 'un solo viaje termina en planta');
  assert.deepEqual(terminaEn([1100, 799]), ['planta', 'casa']);
  assert.deepEqual(terminaEn([1100, 800]), ['planta', 'planta'], 'el umbral es estricto');
  assert.deepEqual(terminaEn([1000, 900, 300]), ['planta', 'planta', 'casa']);
});

test('problemas que el backend rechazaría al confirmar', () => {
  assert.deepEqual(problemasViajes([1100, 700]), []);
  assert.deepEqual(problemasViajes([700, 700]), [], 'empatar con el último está permitido');
  assert.match(problemasViajes([1101])[0], /pesa 1\.101 kg/);
  assert.match(problemasViajes([500, 600])[0], /más liviano/);
  assert.match(problemasViajes([100, 100, 100, 50])[0], /máximo es 3/);
});

test('resumen del peso de lo marcado', () => {
  const r = resumenPeso([{ pedido_id: 1, peso_kg: 600 }, { pedido_id: 2, peso_kg: 700 }, { pedido_id: 3, peso_kg: null }, { pedido_id: 4, peso_kg: 0 }]);
  assert.deepEqual([r.total, r.viajes, r.sinPeso.map(p => p.pedido_id), r.excedido], [1300, 2, [3], false]);
  assert.equal(resumenPeso([]).viajes, 1);
  assert.deepEqual(resumenPeso([{ pedido_id: 9, peso_kg: 1200 }]).sobreCapacidad.map(p => p.pedido_id), [9]);
  assert.equal(resumenPeso([{ peso_kg: 1100 }, { peso_kg: 1100 }, { peso_kg: 1101 }]).excedido, true);
});
