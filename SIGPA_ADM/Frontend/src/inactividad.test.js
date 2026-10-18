import test from 'node:test';
import assert from 'node:assert/strict';
import { EVENTOS_ACTIVIDAD, vigilarInactividad } from './inactividad.js';

// Reloj manual y objetivos de eventos falsos: la prueba controla el tiempo y no depende del navegador.
function escenario(limiteMs = 1000) {
  let t = 0;
  const objetivo = new EventTarget();
  const documento = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  let expiraciones = 0;
  const vigilante = vigilarInactividad({
    alExpirar: () => { expiraciones += 1; },
    limiteMs, objetivo, documento, ahora: () => t, intervaloMs: 60_000,
  });
  return {
    vigilante, objetivo, documento,
    avanzar: ms => { t += ms; },
    expiraciones: () => expiraciones,
  };
}

test('expira al cumplirse el tiempo sin actividad, y no antes', () => {
  const e = escenario(1000);
  e.avanzar(999); e.vigilante.revisar();
  assert.equal(e.expiraciones(), 0);
  e.avanzar(1); e.vigilante.revisar();
  assert.equal(e.expiraciones(), 1);
});

test('cada tipo de actividad del usuario reinicia el conteo', () => {
  for (const evento of EVENTOS_ACTIVIDAD) {
    const e = escenario(1000);
    e.avanzar(900);
    e.objetivo.dispatchEvent(new Event(evento));
    e.avanzar(900); e.vigilante.revisar();
    assert.equal(e.expiraciones(), 0, `${evento} debería contar como actividad`);
    e.avanzar(100); e.vigilante.revisar();
    assert.equal(e.expiraciones(), 1);
    e.vigilante.detener();
  }
});

test('un evento que no es del usuario no cuenta como actividad', () => {
  const e = escenario(1000);
  e.avanzar(900);
  e.objetivo.dispatchEvent(new Event('focus'));
  e.avanzar(100); e.vigilante.revisar();
  assert.equal(e.expiraciones(), 1);
});

test('avisa una sola vez y deja de escuchar al expirar', () => {
  const e = escenario(1000);
  e.avanzar(5000); e.vigilante.revisar(); e.vigilante.revisar();
  e.objetivo.dispatchEvent(new Event('keydown'));
  e.avanzar(5000); e.vigilante.revisar();
  assert.equal(e.expiraciones(), 1);
});

test('al volver a la pestaña revisa de inmediato', () => {
  const e = escenario(1000);
  e.documento.visibilityState = 'hidden';
  e.avanzar(5000);
  e.documento.dispatchEvent(new Event('visibilitychange'));
  assert.equal(e.expiraciones(), 0);
  e.documento.visibilityState = 'visible';
  e.documento.dispatchEvent(new Event('visibilitychange'));
  assert.equal(e.expiraciones(), 1);
});

test('detener evita que expire después', () => {
  const e = escenario(1000);
  e.vigilante.detener();
  e.avanzar(5000); e.vigilante.revisar();
  assert.equal(e.expiraciones(), 0);
});