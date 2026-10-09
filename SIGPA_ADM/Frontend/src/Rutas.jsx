import React, { useEffect, useState } from 'react';
import { api } from './api';
import { moveStop } from './route';
import { RUTA_MAX_PEDIDOS, alternarSeleccion, detallesErrorRuta, enlaceMapa, leerCoordenadas } from './planificacion';

const fecha = iso => new Date(iso).toLocaleDateString('es-CL', { timeZone: 'America/Santiago' });

// Pestaña Rutas: elegir pedidos (máx. RUTA_MAX_PEDIDOS), corregir la ubicación de los que n8n no pudo
// ubicar (#107, #113) y planificar con POST /rutas/planificar.
export default function Rutas({ pendientes, busy, onActualizar, onPlanificando }) {
  const [selected, setSelected] = useState([]);
  const [result, setResult] = useState(null);
  const [manual, setManual] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState(null);
  const [corrigiendo, setCorrigiendo] = useState(null);
  const [aviso, setAviso] = useState('');
  // Al actualizar la lista se descartan los pedidos seleccionados que ya no son planificables.
  useEffect(() => { setSelected(ids => ids.filter(id => pendientes.some(p => p.pedido_id === id))); }, [pendientes]);

  const lleno = selected.length >= RUTA_MAX_PEDIDOS;
  const porRevisar = pendientes.filter(p => p.motivo_revision_direccion).length;

  async function plan() {
    if (!window.confirm(`Se planificarán y confirmarán ${selected.length} pedidos. El backend guardará el orden obtenido. ¿Continuar?`)) return;
    setPlanning(true); onPlanificando(true); setError(null); setAviso('');
    try { setResult(await api('/rutas/planificar', { method: 'POST', body: JSON.stringify({ pedido_ids: selected }) })); setManual(false); setSelected([]); onActualizar(); }
    catch (e) { setError({ mensaje: e.message, detalles: detallesErrorRuta(e) }); }
    finally { setPlanning(false); onPlanificando(false); }
  }

  return <>
    <section className="no-print">
      <div className="title"><h2>Preparar ruta</h2><span className={`contador${lleno ? ' lleno' : ''}`} aria-live="polite">{selected.length} / {RUTA_MAX_PEDIDOS} pedidos</span></div>
      <p>Selecciona los pedidos que se incluirán, hasta {RUTA_MAX_PEDIDOS} por ruta. Se muestran todas las fechas: el backend todavía no registra una fecha de entrega.</p>
      {porRevisar > 0 && <p className="notice">{porRevisar === 1 ? 'Hay 1 pedido' : `Hay ${porRevisar} pedidos`} con la dirección por revisar: corrige su ubicación para que entren a la próxima ruta.</p>}
      {aviso && <p role="status" className="ok">{aviso}</p>}
      {pendientes.map(p => <div className="parada" key={p.pedido_id}>
        <div className="stop">
          <input id={`pedido-${p.pedido_id}`} type="checkbox" disabled={planning || (lleno && !selected.includes(p.pedido_id))} checked={selected.includes(p.pedido_id)} onChange={e => setSelected(ids => alternarSeleccion(ids, p.pedido_id, e.target.checked))}/>
          <label htmlFor={`pedido-${p.pedido_id}`}>
            <strong>#{p.pedido_id} · {p.cliente_nombre}</strong>
            <span className="etiquetas"><span className={`badge ${p.estado}`}>{p.estado.replaceAll('_', ' ')}</span>{p.motivo_revision_direccion && <span className="badge revisar">Dirección por revisar</span>}</span>
            <small>{p.direccion_texto || 'Sin dirección'} · {fecha(p.creado_en)}</small>
            {p.motivo_revision_direccion && <small className="motivo">Motivo: {p.motivo_revision_direccion}</small>}
          </label>
          {p.motivo_revision_direccion && corrigiendo !== p.pedido_id && <button onClick={() => { setCorrigiendo(p.pedido_id); setAviso(''); }}>Corregir ubicación</button>}
        </div>
        {corrigiendo === p.pedido_id && <CorregirUbicacion pedido={p} onCancelar={() => setCorrigiendo(null)} onGuardado={() => { setCorrigiendo(null); setAviso(`Ubicación del pedido #${p.pedido_id} guardada. Ya puede entrar en la próxima ruta.`); onActualizar(); }}/>}
      </div>)}
      {!pendientes.length && <p>No hay pedidos pendientes.</p>}
      {lleno && <p className="muted">Llegaste al máximo de {RUTA_MAX_PEDIDOS} pedidos por ruta. Desmarca alguno para elegir otro.</p>}
      {error && <div role="alert" className="error"><strong>{error.mensaje}</strong><ul>{error.detalles.map(d => <li key={d}>{d}</li>)}</ul></div>}
      <button className="primary" disabled={!selected.length || planning || busy} onClick={plan}>{planning ? 'Planificando… (puede tardar hasta 2 minutos)' : `Planificar y confirmar (${selected.length})`}</button>
    </section>
    {result && <section className="print-route">
      <div className="title"><h2>Orden de reparto</h2><button className="no-print" disabled={!result.ruta.length} onClick={() => window.print()}>Imprimir ruta</button></div>
      <p className="notice">{manual ? 'Orden modificado para esta impresión. Los cambios manuales NO están guardados en el servidor y se pierden al salir o recargar.' : 'Orden generado y guardado por el backend. Puedes ajustarlo para esta impresión.'}</p>
      {result.ruta.map((p, i) => <div className="stop" key={p.pedido_id}><b className="number">{i + 1}</b><span><strong>#{p.pedido_id} · {p.cliente_nombre}</strong><small>{p.direccion_texto || 'Sin dirección'}</small></span><div className="no-print"><button aria-label={`Subir pedido ${p.pedido_id}`} disabled={i === 0} onClick={() => { setResult(r => ({ ...r, ruta: moveStop(r.ruta, i, -1) })); setManual(true); }}>↑</button><button aria-label={`Bajar pedido ${p.pedido_id}`} disabled={i === result.ruta.length - 1} onClick={() => { setResult(r => ({ ...r, ruta: moveStop(r.ruta, i, 1) })); setManual(true); }}>↓</button></div></div>)}
      {result.sin_resolver?.length > 0 && <div className="error no-print"><h3>Pedidos sin ubicación resuelta</h3>{result.sin_resolver.map(p => <p key={p.pedido_id}>#{p.pedido_id} · {p.cliente_nombre}: {p.motivo}</p>)}<p>Quedaron confirmados pero fuera de la ruta. Aparecen arriba como "Dirección por revisar": corrige su ubicación y vuelve a planificar.</p></div>}
      {result.confirmados_con_orden_fuera_de_solicitud?.length > 0 && <p className="notice">Hay {result.confirmados_con_orden_fuera_de_solicitud.length} pedidos confirmados con orden de otras planificaciones. No se incluyen en esta impresión.</p>}
    </section>}
  </>;
}

// Historia #113: la ejecutiva busca la dirección en Google Maps, copia las coordenadas y las pega aquí.
function CorregirUbicacion({ pedido, onCancelar, onGuardado }) {
  const [texto, setTexto] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function guardar(e) {
    e.preventDefault();
    const coordenadas = leerCoordenadas(texto);
    if (coordenadas.error) return setError(coordenadas.error);
    setBusy(true); setError('');
    try { await api(`/pedidos/${pedido.pedido_id}/coordenadas`, { method: 'POST', body: JSON.stringify(coordenadas) }); onGuardado(); }
    catch (e) { setError(e.message); setBusy(false); }
  }
  return <form className="coordenadas" onSubmit={guardar}>
    <ol>
      <li>Abre la dirección en <a href={enlaceMapa(pedido.direccion_texto || pedido.cliente_nombre || '')} target="_blank" rel="noopener noreferrer">Google Maps</a> y ubica la casa.</li>
      <li>Haz clic derecho sobre ella (en el celular, mantenla presionada): las coordenadas aparecen arriba del menú y se copian al hacerles clic.</li>
      <li>Pégalas aquí y guarda.</li>
    </ol>
    <label>Coordenadas (latitud, longitud)<input autoFocus inputMode="decimal" placeholder="-33.0458, -71.6197" value={texto} onChange={e => setTexto(e.target.value)} disabled={busy}/></label>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onCancelar} disabled={busy}>Cancelar</button><button className="primary" disabled={busy || !texto.trim()}>{busy ? 'Guardando…' : 'Guardar ubicación'}</button></div>
  </form>;
}
