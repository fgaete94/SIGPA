import { useEffect, useRef, useState } from 'react';
import { destinoArrastre } from './route';

const UMBRAL = 5;   // px que hay que mover el mouse para que un clic se vuelva arrastre
const BORDE = 56;   // px desde el borde de la lista en que se desplaza sola
const PASO = 14;    // px por cuadro al desplazarse sola

function contenedorDe(el) {
  for (let n = el?.parentElement; n; n = n.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(n).overflowY) && n.scrollHeight > n.clientHeight) return n;
  }
  return document.scrollingElement;
}

// Ordenar una lista arrastrando, como los íconos del celular: la fila sigue al puntero, las demás se
// corren para dejarle espacio y al soltar queda en ese lugar (onMover(desde, hasta)).
// - Mouse: se arrastra la fila completa (salvo sus botones y enlaces) después de moverlo unos píxeles.
// - Dedo o lápiz: se arrastra desde la manilla, para que deslizar sobre la lista siga desplazándola.
// - Teclado: flechas arriba y abajo sobre la manilla.
// Si la lista no cabe en pantalla, se desplaza sola al acercarse al borde.
export function useArrastre(cantidad, onMover, { deshabilitado = false } = {}) {
  const filas = useRef([]);
  const actual = useRef(null);
  const [visual, setVisual] = useState(null);
  useEffect(() => () => actual.current?.terminar(), []);

  function empezar(i, e, inmediato) {
    if (deshabilitado || cantidad < 2 || actual.current) return;
    const caja = contenedorDe(filas.current[i]);
    const d = {
      desde: i, hasta: i, y0: e.clientY, y: e.clientY, activo: false, cuadro: 0, caja, scroll0: caja.scrollTop,
      centros: filas.current.slice(0, cantidad).map(el => { const r = el.getBoundingClientRect(); return r.top + r.height / 2; }),
      alto: filas.current[i].getBoundingClientRect().height,
    };
    const calcular = () => {
      const dy = d.y - d.y0 + (d.caja.scrollTop - d.scroll0);
      d.hasta = destinoArrastre(d.centros, d.desde, d.centros[d.desde] + dy);
      setVisual({ desde: d.desde, hasta: d.hasta, dy, alto: d.alto });
    };
    const bucle = () => {
      const area = d.caja === document.scrollingElement ? { top: 0, bottom: innerHeight } : d.caja.getBoundingClientRect();
      if (d.y < area.top + BORDE) d.caja.scrollTop -= PASO;
      else if (d.y > area.bottom - BORDE) d.caja.scrollTop += PASO;
      calcular();
      d.cuadro = requestAnimationFrame(bucle);
    };
    const activar = () => {
      d.activo = true;
      // Sin esto, arrastrar con el mouse deja texto seleccionado en las filas.
      getSelection()?.removeAllRanges();
      document.body.classList.add('arrastrando-lista');
      d.cuadro = requestAnimationFrame(bucle);
    };
    const mover = ev => {
      d.y = ev.clientY;
      if (!d.activo) { if (Math.abs(d.y - d.y0) < UMBRAL) return; activar(); }
      ev.preventDefault();
      calcular();
    };
    const soltar = () => { const { activo, desde, hasta } = d; d.terminar(); if (activo && desde !== hasta) onMover(desde, hasta); };
    d.terminar = () => {
      cancelAnimationFrame(d.cuadro);
      removeEventListener('pointermove', mover);
      removeEventListener('pointerup', soltar);
      removeEventListener('pointercancel', d.terminar);
      document.body.classList.remove('arrastrando-lista');
      actual.current = null;
      setVisual(null);
    };
    actual.current = d;
    addEventListener('pointermove', mover, { passive: false });
    addEventListener('pointerup', soltar);
    addEventListener('pointercancel', d.terminar);
    if (inmediato) activar();
  }

  // Mientras se arrastra: la fila sigue al puntero y las que quedan entre medio se corren una posición.
  function estilo(i) {
    if (!visual) return undefined;
    const { desde, hasta, dy, alto } = visual;
    if (i === desde) return { transform: `translateY(${dy}px)` };
    if (desde < hasta && i > desde && i <= hasta) return { transform: `translateY(${-alto}px)` };
    if (hasta < desde && i >= hasta && i < desde) return { transform: `translateY(${alto}px)` };
    return undefined;
  }

  const propsFila = i => ({
    ref: el => { filas.current[i] = el; },
    className: visual ? (visual.desde === i ? 'ordenable arrastrada' : 'ordenable moviendose') : 'ordenable',
    style: estilo(i),
    onPointerDown: e => {
      if (e.pointerType !== 'mouse' || e.button !== 0 || e.target.closest('button, a, input, select, textarea, label')) return;
      e.preventDefault();
      empezar(i, e, false);
    },
  });

  const propsManilla = (i, nombre) => ({
    type: 'button',
    className: 'manilla',
    disabled: deshabilitado || cantidad < 2,
    title: 'Arrastra para cambiar el orden (o usa las flechas del teclado)',
    'aria-label': `Mover ${nombre}, parada ${i + 1} de ${cantidad}. Usa las flechas arriba y abajo`,
    onPointerDown: e => { if (e.button && e.button !== 0) return; e.preventDefault(); empezar(i, e, true); },
    onKeyDown: e => {
      if (e.key === 'ArrowUp' && i > 0) { e.preventDefault(); onMover(i, i - 1); }
      if (e.key === 'ArrowDown' && i < cantidad - 1) { e.preventDefault(); onMover(i, i + 1); }
    },
  });

  return { propsFila, propsManilla, arrastrando: Boolean(visual) };
}
