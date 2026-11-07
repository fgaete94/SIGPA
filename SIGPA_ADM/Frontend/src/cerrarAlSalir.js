import { useEffect, useRef } from 'react';

// Para menús y paneles desplegables: se cierran al hacer clic afuera o con Escape. Devuelve la ref de la caja.
export function useCerrarAlSalir(abierto, cerrar) {
  const caja = useRef(null);
  const alCerrar = useRef(cerrar);
  alCerrar.current = cerrar;
  useEffect(() => {
    if (!abierto) return undefined;
    const clicAfuera = e => { if (!caja.current?.contains(e.target)) alCerrar.current(); };
    const conEscape = e => { if (e.key === 'Escape') alCerrar.current(); };
    document.addEventListener('pointerdown', clicAfuera);
    document.addEventListener('keydown', conEscape);
    return () => { document.removeEventListener('pointerdown', clicAfuera); document.removeEventListener('keydown', conEscape); };
  }, [abierto]);
  return caja;
}
