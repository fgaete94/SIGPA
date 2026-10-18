// Historia #137: orden de las tablas del panel al presionar el encabezado de una columna.
// Los textos se comparan en español (sin distinguir mayúsculas ni tildes) y los vacíos van siempre al final.
const textos = new Intl.Collator('es', { sensitivity: 'base', numeric: true });
const vacio = v => v == null || v === '';

export function comparar(a, b) {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return textos.compare(String(a), String(b));
}

// Para columnas con un orden propio (estados del pedido, días de la semana): la posición en la lista.
export const posicionEn = lista => valor => { const i = lista.indexOf(valor); return i < 0 ? null : i; };

// columnas: { nombre: fila => valor }. orden: { columna, sentido: 'asc' | 'desc' } o null (orden original).
// Devuelve una copia ordenada. A igual valor se mantiene el orden original.
export function ordenar(filas, columnas, orden) {
  const valor = orden && columnas[orden.columna];
  if (!valor) return filas;
  const signo = orden.sentido === 'desc' ? -1 : 1;
  return filas
    .map((fila, i) => ({ fila, i, v: valor(fila) }))
    .sort((x, y) => {
      if (vacio(x.v) || vacio(y.v)) return vacio(x.v) - vacio(y.v) || x.i - y.i;
      return signo * comparar(x.v, y.v) || x.i - y.i;
    })
    .map(x => x.fila);
}

// Primer clic en una columna: ascendente. Clic de nuevo en la misma: invierte el sentido.
export function siguienteOrden(actual, columna) {
  if (actual?.columna !== columna) return { columna, sentido: 'asc' };
  return { columna, sentido: actual.sentido === 'asc' ? 'desc' : 'asc' };
}