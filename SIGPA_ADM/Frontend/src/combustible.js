// Combustible del día: si el repartidor cargó, cuánto y cómo pagó. Lógica sin React.
import { aFecha, horaCorta } from './fechas.js';

export const MEDIOS_COMBUSTIBLE = [['efectivo', 'Efectivo'], ['tarjeta', 'Tarjeta']];
const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// Acepta "20000", "20.000" o "$20.000". null si no es un monto válido.
export function leerMonto(texto) {
  const limpio = String(texto ?? '').replace(/[$\s.]/g, '');
  if (!/^\d+$/.test(limpio)) return null;
  const monto = Number(limpio);
  return monto > 0 ? monto : null;
}

// registros: los del día, cada uno { cargo, monto, medio_pago, creado_en }.
export function resumenCombustible(registros = []) {
  const cargas = registros.filter(r => r.cargo);
  return { cargas, total: cargas.reduce((suma, r) => suma + r.monto, 0), noCargo: !cargas.length && registros.some(r => !r.cargo) };
}

export function textoCarga(r) {
  const medio = MEDIOS_COMBUSTIBLE.find(([clave]) => clave === r.medio_pago)?.[1];
  return [dinero(r.monto), medio, r.creado_en && horaCorta(aFecha(r.creado_en))].filter(Boolean).join(' · ');
}

// Texto del botón del repartidor.
export function etiquetaCombustible(registros) {
  const { cargas, total, noCargo } = resumenCombustible(registros);
  if (cargas.length) return `Combustible ✓ ${dinero(total)}`;
  return noCargo ? 'Combustible: no cargó' : 'Combustible';
}
