import React, { useState } from 'react';
import MapaRuta from './MapaRuta';
import { resumenEntregas, textoResultado } from './reparto';
import { rutasDeLaSemana } from './datosRutas';
import { agruparPorViaje } from './viajes';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// La semana elegida, siempre de lunes a viernes, con las paradas en el orden en que se planificaron. Hoy parte abierto.
export default function RutasEntregadas({ pedidos, clientes, semana, hoy }) {
  const rutas = rutasDeLaSemana(pedidos, clientes, semana);
  return <section className="rutas-entregadas">
    <h2>Despachos realizados</h2>
    {rutas.map(r => (r.paradas.length
      ? <RutaDelDia key={r.dia} ruta={r} abierta={r.dia === hoy}/>
      : <div className="ruta-dia sin-despachos" key={r.dia}><strong>{r.etiqueta}</strong><span>Sin despachos</span></div>))}
  </section>;
}

function RutaDelDia({ ruta, abierta }) {
  const [verMapa, setVerMapa] = useState(false);
  const grupos = agruparPorViaje(ruta.paradas);
  const r = resumenEntregas(ruta.paradas);
  return <details className="ruta-dia" open={abierta}>
    <summary>
      <strong>{ruta.etiqueta}</strong>
      <span>{r.total} {r.total === 1 ? 'parada' : 'paradas'} · ✓ {r.entregadas} · ✗ {r.noEntregadas} · {dinero(r.montoEntregado)} entregado</span>
    </summary>
    <button type="button" className="enlace" aria-expanded={verMapa} onClick={() => setVerMapa(v => !v)}>{verMapa ? 'Ocultar mapa' : 'Ver en el mapa'}</button>
    {verMapa && <MapaRuta grupos={grupos}/>}
    {grupos.map(g => <div key={g.viaje ?? 'sin-viaje'}>
      {g.viaje != null && <h4 className="viaje-titulo">Ruta {g.viaje}</h4>}
      <div className="table entregas-dia"><table className="tabla-movil">
        <thead><tr><th>Orden</th><th>Cliente</th><th>Dirección</th><th>Total</th><th>Resultado</th></tr></thead>
        <tbody>{g.paradas.map((p, i) => <tr key={p.id} className={p.estado === 'entregado' ? 'fila-entregada' : 'fila-no-entregada'}>
          <td data-label="Orden"><b className="number">{i + 1}</b></td>
          <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
          <td data-label="Dirección">{p.direccion || 'Sin dirección registrada'}</td>
          <td data-label="Total">{dinero(p.total)}</td>
          <td data-label="Resultado"><span className={`badge ${p.estado}`}>{textoResultado(p)}</span></td>
        </tr>)}</tbody>
      </table></div>
    </div>)}
  </details>;
}
