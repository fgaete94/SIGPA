import React, { useEffect, useMemo, useState } from 'react';
import { DIAS_REPARTO } from './clientes';
import { SIN_DIA, diaHoy } from './agrupacionDespacho';
import { api } from './api';
import { ETIQUETA_ESTADO } from './estados';
import { fechaCorta as fecha } from './fechas';
import { RUTA_MAX_PEDIDOS, alternarSeleccion, detallesErrorRuta, enlaceMapa, leerCoordenadas } from './planificacion';
import { datosConfirmar, estadoVistaPrevia } from './vistaPrevia';
import { LIMITE_SIN_UBICACION, desmarcarVisibles, enriquecerPendientes, filtrarPendientes, marcarVisibles, opcionesSeleccion, sinUbicacionEn } from './seleccionRuta';
import VistaPreviaRuta from './VistaPreviaRuta';

// Pestaña Rutas: elegir pedidos (máx. RUTA_MAX_PEDIDOS), corregir la ubicación de los que n8n no pudo
// ubicar (#107, #113) y calcular la ruta con POST /rutas/planificar en vista previa: se revisa y se guarda con
// POST /rutas/confirmar (#87). El orden guardado se ve en RutaEnCurso (#85).
const nombreDia = d => (d === SIN_DIA ? 'Sin día de reparto' : DIAS_REPARTO.find(([v]) => v === d)?.[1] || d);

export default function Rutas({ pendientes, clientes = [], busy, onActualizar, onPlanificando }) {
  // #81: armar la ruta por día de reparto y comuna del cliente.
  const [dia, setDia] = useState('');
  const [comuna, setComuna] = useState('');
  const lista = useMemo(() => enriquecerPendientes(pendientes, clientes), [pendientes, clientes]);
  const { dias, comunas } = opcionesSeleccion(lista, dia);
  const visibles = filtrarPendientes(lista, { dia, comuna });
  const hoy = diaHoy();
  const [selected, setSelected] = useState([]);
  const [result, setResult] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState(null);
  const [corrigiendo, setCorrigiendo] = useState(null);
  const [aviso, setAviso] = useState('');
  const [vista, setVista] = useState(null);
  const [guardando, setGuardando] = useState(false);
  const [errorVista, setErrorVista] = useState(null);
  // Al actualizar la lista se descartan los pedidos seleccionados que ya no son planificables.
  useEffect(() => { setSelected(ids => ids.filter(id => pendientes.some(p => p.pedido_id === id))); }, [pendientes]);

  const lleno = selected.length >= RUTA_MAX_PEDIDOS;
  const porRevisar = pendientes.filter(p => p.motivo_revision_direccion).length;
  const sinUbicacionElegidos = sinUbicacionEn(lista, selected);
  function marcar() {
    const r = marcarVisibles(selected, visibles, RUTA_MAX_PEDIDOS);
    setSelected(r.ids);
    setAviso([
      r.agregados === 1 ? 'Se marcó 1 pedido.' : `Se marcaron ${r.agregados} pedidos.`,
      r.sinCupo ? `${r.sinCupo} no caben: el máximo por ruta es ${RUTA_MAX_PEDIDOS}.` : '',
      r.porRevisar ? `${r.porRevisar} con la dirección por revisar quedaron fuera: corrige su ubicación primero.` : '',
    ].filter(Boolean).join(' '));
  }

  // #87: calcular no guarda nada; la ruta se revisa en la vista previa y se guarda al confirmar.
  async function calcular() {
    setPlanning(true); onPlanificando(true); setError(null); setAviso(''); setResult(null); setVista(null); setErrorVista(null);
    try { setVista(estadoVistaPrevia(await api('/rutas/planificar', { method: 'POST', body: JSON.stringify({ pedido_ids: selected, vista_previa: true }) }))); }
    catch (e) { setError({ mensaje: e.message, detalles: detallesErrorRuta(e) }); onPlanificando(false); }
    finally { setPlanning(false); }
  }
  function cerrarVista() { setVista(null); setErrorVista(null); onPlanificando(false); }
  async function confirmar() {
    setGuardando(true); setErrorVista(null);
    try {
      setResult(await api('/rutas/confirmar', { method: 'POST', body: JSON.stringify(datosConfirmar(vista)) }));
      setSelected([]); cerrarVista(); onActualizar();
    } catch (e) { setErrorVista({ mensaje: e.message, detalles: detallesErrorRuta(e) }); }
    finally { setGuardando(false); }
  }

  return <>
    <section className="no-print">
      <div className="title"><h2>Preparar ruta</h2><span className={`contador${lleno ? ' lleno' : ''}`} aria-live="polite">{selected.length} / {RUTA_MAX_PEDIDOS} pedidos</span></div>
      <p>Selecciona los pedidos que se incluirán, hasta {RUTA_MAX_PEDIDOS} por ruta, y calcula la ruta: antes de guardarla podrás revisarla y cambiar el orden. Se muestran todas las fechas: el backend todavía no registra una fecha de entrega.</p>
      {porRevisar > 0 && <p className="notice">{porRevisar === 1 ? 'Hay 1 pedido' : `Hay ${porRevisar} pedidos`} con la dirección por revisar: corrige su ubicación para que entren a la próxima ruta.</p>}
      <div className="filters filtros-ruta">
        <select aria-label="Día de reparto" value={dia} onChange={e => { setDia(e.target.value); setComuna(''); }}>
          <option value="">Todos los días ({lista.length})</option>
          {dias.map(([d, n]) => <option key={d} value={d}>{nombreDia(d)}{d === hoy ? ' · hoy' : ''} ({n})</option>)}
        </select>
        <select aria-label="Comuna" value={comuna} onChange={e => setComuna(e.target.value)}>
          <option value="">Todas las comunas{dia ? ' del día' : ''}</option>
          {comunas.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
        </select>
        <button disabled={planning || !visibles.length} onClick={marcar}>Marcar los {visibles.length} visibles</button>
        <button disabled={planning || !visibles.some(p => selected.includes(p.pedido_id))} onClick={() => { setSelected(ids => desmarcarVisibles(ids, visibles)); setAviso(''); }}>Desmarcar visibles</button>
      </div>
      {aviso && <p role="status" className="ok">{aviso}</p>}
      {sinUbicacionElegidos > LIMITE_SIN_UBICACION && <p className="notice">{sinUbicacionElegidos} de los pedidos marcados no tienen ubicación: el servicio de rutas tendría que buscarlos todos y podría no alcanzar a responder. Calcula en dos tandas o corrige primero algunas ubicaciones.</p>}
      {visibles.map(p => <div className="parada" key={p.pedido_id}>
        <div className="stop">
          <input id={`pedido-${p.pedido_id}`} type="checkbox" disabled={planning || (lleno && !selected.includes(p.pedido_id))} checked={selected.includes(p.pedido_id)} onChange={e => setSelected(ids => alternarSeleccion(ids, p.pedido_id, e.target.checked))}/>
          <label htmlFor={`pedido-${p.pedido_id}`}>
            <strong>#{p.pedido_id} · {p.cliente_nombre}</strong>
            <span className="etiquetas"><span className={`badge ${p.estado}`}>{ETIQUETA_ESTADO[p.estado] || p.estado}</span>{p.motivo_revision_direccion ? <span className="badge revisar">Dirección por revisar</span> : p.sinUbicacion && <span className="badge sin-ubicacion" title="Al calcular, el servicio de rutas buscará la dirección en el mapa">Sin ubicación</span>}</span>
            <small>{p.direccion_texto || 'Sin dirección'} · {p.comuna} · {nombreDia(p.dia)} · {fecha(p.creado_en)}</small>
            {p.motivo_revision_direccion && <small className="motivo">Motivo: {p.motivo_revision_direccion}</small>}
          </label>
          {p.motivo_revision_direccion && corrigiendo !== p.pedido_id && <button onClick={() => { setCorrigiendo(p.pedido_id); setAviso(''); }}>Corregir ubicación</button>}
        </div>
        {corrigiendo === p.pedido_id && <CorregirUbicacion pedido={p} onCancelar={() => setCorrigiendo(null)} onGuardado={() => { setCorrigiendo(null); setAviso(`Ubicación del pedido #${p.pedido_id} guardada. Ya puede entrar en la próxima ruta.`); onActualizar(); }}/>}
      </div>)}
      {!pendientes.length && <p>No hay pedidos pendientes.</p>}
      {pendientes.length > 0 && !visibles.length && <p>No hay pedidos pendientes con estos filtros.</p>}
      {lleno && <p className="muted">Llegaste al máximo de {RUTA_MAX_PEDIDOS} pedidos por ruta. Desmarca alguno para elegir otro.</p>}
      {error && <div role="alert" className="error"><strong>{error.mensaje}</strong><ul>{error.detalles.map(d => <li key={d}>{d}</li>)}</ul></div>}
      <button className="primary" disabled={!selected.length || planning || busy || !!vista} onClick={calcular}>{planning ? 'Calculando… (puede tardar hasta 2 minutos)' : `Calcular ruta (${selected.length})`}</button>
    </section>
    {vista && <VistaPreviaRuta estado={vista} onCambiar={setVista} onCancelar={cerrarVista} onConfirmar={confirmar} onRecalcular={calcular} guardando={guardando} error={errorVista}/>}
    {result && <section className="no-print">
      <div className="title"><h2>Última planificación</h2><button onClick={() => setResult(null)}>Ocultar</button></div>
      <p role="status" className="ok">Se confirmaron {result.pedidos_confirmados} pedidos: {result.ruta.length} en la ruta{result.sin_resolver?.length ? ` y ${result.sin_resolver.length} sin ubicación` : ''}.{result.ruta.length ? ' El orden quedó guardado y se ve en "Ruta en curso".' : ' Ninguno entró a la ruta: quedaron marcados para revisar su dirección.'}</p>
      {result.sin_resolver?.length > 0 && <div className="error"><h3>Pedidos sin ubicación resuelta</h3>{result.sin_resolver.map(p => <p key={p.pedido_id}>#{p.pedido_id} · {p.cliente_nombre}: {p.motivo}</p>)}<p>Quedaron confirmados pero fuera de la ruta. Aparecen en "Preparar ruta" como "Dirección por revisar": corrige su ubicación y vuelve a planificar.</p></div>}
      {result.confirmados_con_orden_fuera_de_solicitud?.length > 0 && <p className="notice">Hay {result.confirmados_con_orden_fuera_de_solicitud.length} pedidos confirmados con orden de otras planificaciones: aparecen mezclados en "Ruta en curso".</p>}
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
