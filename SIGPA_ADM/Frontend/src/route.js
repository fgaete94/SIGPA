export function moveStop(stops, index, delta) {
  const next = index + delta;
  if (index < 0 || index >= stops.length || next < 0 || next >= stops.length) return stops;
  const result = [...stops];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}

// Mueve la parada de la posición "desde" a "hasta" (arrastrar y soltar); las demás se corren.
export function moverA(stops, desde, hasta) {
  if (desde === hasta || desde < 0 || hasta < 0 || desde >= stops.length || hasta >= stops.length) return stops;
  const result = [...stops];
  const [parada] = result.splice(desde, 1);
  result.splice(hasta, 0, parada);
  return result;
}

// Posición donde cae la parada arrastrada: cuántas de las otras quedaron con su centro por encima del
// centro de la arrastrada (centros: el centro vertical de cada fila al empezar a arrastrar).
export const destinoArrastre = (centros, desde, centroArrastrada) =>
  centros.filter((centro, i) => i !== desde && centro < centroArrastrada).length;
