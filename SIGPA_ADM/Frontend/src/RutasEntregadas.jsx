import React, { useState } from 'react';
import MapaRuta from './MapaRuta';
import { resumenEntregas, textoResultado } from './reparto';
import { rutasDeLaSemana } from './datosRutas';
import { agruparPorViaje } from './viajes';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// La semana elegida, siempre de lunes a viernes, como una franja de 5 días: cada día con lo entregado y su
// avance. Al elegir un día se ven sus rutas debajo, en el orden en que se planificaron. Parte en hoy (o en
// el primer día con despachos).
export default function RutasEntregadas({ pedidos, clientes, semana, hoy }) {
  const rutas = rutasDeLaSemana(pedidos, clientes, semana);
  const conDespachos = rutas.filter(r => r.paradas.length);
  const inicial = (conDespachos.find(r => r.dia === hoy) ?? conDespachos[0])?.dia ?? null;
  const [elegido, setElegido] = useState(inicial);
  const dia = rutas.find(r => r.dia === elegido && r.paradas.length) ?? rutas.find(r => r.dia === inicial);
  return <section className="rutas-entregadas despachos-vista">
    <h2>Despachos realizados</h2>
    <div className="semana-dias" role="group" aria-label="Días de la semana">
      {rutas.map(r => {
        const res = resumenEntregas(r.paradas);
        const listas = res.entregadas + res.noEntregadas;
        const [nombre, ...fecha] = r.etiqueta.split(', ');
        return <button type="button" key={r.dia} className={`semana-dia${r.paradas.length ? '' : ' vacio'}${r.dia === dia?.dia ? ' elegido' : ''}`} disabled={!r.paradas.length} aria-pressed={r.dia === dia?.dia} onClick={() => setElegido(r.dia)}>
          <span className="semana-dia-nombre">{nombre}{r.dia === hoy && <em>Hoy</em>}</span>
          <small>{fecha.join(', ')}</small>
          {r.paradas.length ? <>
            <strong>{dinero(res.montoEntregado)}</strong>
            <span className="semana-dia-conteo">✓ {res.entregadas} · ✗ {res.noEntregadas} de {res.total}</span>
            <span className="semana-dia-nivel" aria-hidden="true"><i style={{ width: `${res.total ? (listas / res.total) * 100 : 0}%` }}/></span>
          </> : <span className="semana-dia-conteo">Sin despachos</span>}
        </button>;
      })}
    </div>
    {dia ? <RutaDelDia key={dia.dia} ruta={dia}/> : <p className="muted">Esta semana no hay despachos.</p>}
  </section>;
}

function RutaDelDia({ ruta }) {
  const [verMapa, setVerMapa] = useState(false);
  const grupos = agruparPorViaje(ruta.paradas);
  return <div className="dia-detalle">
    <div className="dia-detalle-titulo">
      <h3>{ruta.etiqueta}</h3>
      <button type="button" aria-expanded={verMapa} onClick={() => setVerMapa(v => !v)}>{verMapa ? 'Ocultar mapa' : 'Ver en el mapa'}</button>
    </div>
    {verMapa && <MapaRuta grupos={grupos}/>}
    {grupos.map(g => {
      const res = resumenEntregas(g.paradas);
      const listas = res.entregadas + res.noEntregadas;
      return <div className={`ruta-hoy${res.total && listas === res.total ? ' terminada' : ''}`} key={g.viaje ?? 'sin-viaje'}>
        <div className="ruta-hoy-franja" style={{ '--avance': `${res.total ? (listas / res.total) * 100 : 0}%` }}>
          <span className="ruta-agua" aria-hidden="true"/>
          <h4>{g.viaje != null ? `Ruta ${g.viaje}` : 'Sin ruta'}</h4>
          <p><b>{listas} de {res.total}</b> {res.total === 1 ? 'parada' : 'paradas'}</p>
          <p><b>{dinero(res.montoEntregado)}</b> entregado</p>
          {res.noEntregadas > 0 && <p className="ruta-hoy-fallas">{res.noEntregadas} no {res.noEntregadas === 1 ? 'entregado' : 'entregados'}</p>}
        </div>
        <div className="table entregas-dia"><table className="tabla-movil">
          <thead><tr><th>Orden</th><th>Cliente</th><th>Dirección</th><th className="num">Total</th><th>Resultado</th></tr></thead>
          <tbody>{g.paradas.map((p, i) => <tr key={p.id} className={p.estado === 'entregado' ? 'fila-entregada' : 'fila-no-entregada'}>
            <td data-label="Orden" className="parada-num">{i + 1}</td>
            <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
            <td data-label="Dirección">{p.direccion || 'Sin dirección registrada'}</td>
            <td data-label="Total" className="num">{dinero(p.total)}</td>
            <td data-label="Resultado"><span className={`badge ${p.estado}`}>{textoResultado(p)}</span></td>
          </tr>)}</tbody>
        </table></div>
      </div>;
    })}
  </div>;
}
