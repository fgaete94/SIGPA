import React, { useState } from 'react';
import { ordenar, siguienteOrden } from './ordenar';

// Historia #137: orden de una tabla. Devuelve las filas ordenadas y una función que arma cada encabezado
// presionable. Sin clic, la tabla conserva el orden que entrega el backend.
export function useOrden(filas, columnas) {
  const [orden, setOrden] = useState(null);
  const encabezado = (columna, texto) => {
    const sentido = orden?.columna === columna ? orden.sentido : null;
    return <th key={columna} aria-sort={sentido === 'asc' ? 'ascending' : sentido === 'desc' ? 'descending' : 'none'}>
      <button type="button" className="orden" onClick={() => setOrden(o => siguienteOrden(o, columna))}>
        {texto}<span aria-hidden="true">{sentido === 'asc' ? '▲' : sentido === 'desc' ? '▼' : '↕'}</span>
      </button>
    </th>;
  };
  return { filas: ordenar(filas, columnas, orden), encabezado };
}