import React, { useEffect, useMemo, useState } from 'react';
import { useCerrarAlSalir } from './cerrarAlSalir';
import { api } from './api';
import { ETIQUETA_ESTADO, despachables, enDespacho, resuelta } from './estados';
import HojaDeRuta from './HojaDeRuta';
import MapaRuta, { NumeroParada } from './MapaRuta';
import { opcionesDescarga } from './hojaRuta';
import { enlaceComoLlegar, rutaEnCurso, textoResultado } from './reparto';
import { useArrastre } from './Arrastre';
import { moverA } from './route';
import { agruparPorViaje, contarViajes, reemplazarViaje, tituloGrupo } from './viajes';
import { siguienteParada } from './capasMapa';
import { resumenCombustible, textoCarga } from './combustible';

const RECARGA_RUTA_MS = 15000;

// Historia #85: la ruta guardada en la base, para coordinar al repartidor (asignarla o descargar su hoja de ruta).
// Agrupada por viaje: cada viaje es una carga del camión y numera sus paradas desde 1. viajes: el peso y
// dónde termina cada viaje (GET /rutas/actual); sin esos datos el encabezado muestra solo el número.
export default function RutaEnCurso({ pedidos, clientes, productos, viajes = [], activa = true, onActualizar, onEntregar }) {
  const { paradas: guardadas, repetidos, sinUbicacion } = useMemo(() => rutaEnCurso(pedidos, clientes), [pedidos, clientes]);
  // Reordenar (arrastrando) solo cambia esta vista: impresión y texto. Para guardar otro orden se vuelve a calcular la ruta (#87).
  const [paradas, setParadas] = useState(guardadas);
  // Copia de las paradas al abrir la hoja de ruta: una recarga de datos no la cambia mientras está abierta.
  const [hoja, setHoja] = useState(null);
  // El mapa se abre a pedido: la pestaña Rutas se monta oculta y no debe descargar el mapa sin que se use.
  const [verMapa, setVerMapa] = useState(false);
  const [foco, setFoco] = useState(null);
  function verParada(id) { setVerMapa(true); setFoco(f => ({ id, n: (f?.n ?? 0) + 1 })); }
  useEffect(() => setParadas(guardadas), [guardadas]);
  const manual = paradas.some((p, i) => p.id !== guardadas[i]?.id);
  // Lo entregado sigue en la lista para ver el avance, pero no va en la hoja de ruta.
  const porEntregar = paradas.filter(p => !resuelta(p));
  const entregadas = paradas.filter(p => p.estado === 'entregado').length;
  const noEntregadas = paradas.filter(p => p.estado === 'no_entregado').length;
  const grupos = agruparPorViaje(paradas, viajes);
  const siguiente = siguienteParada(grupos);
  // Mientras se ve esta pantalla, se recarga sola para mostrar lo que marca el repartidor, y de inmediato al volver a la pestaña.
  // Se pausa con la hoja abierta o el orden cambiado a mano, que una recarga perdería.
  useEffect(() => {
    if (!activa || hoja || manual) return undefined;
    const recargar = () => { if (!document.hidden) onActualizar(); };
    const timer = setInterval(recargar, RECARGA_RUTA_MS);
    document.addEventListener('visibilitychange', recargar);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', recargar); };
  }, [activa, hoja, manual, onActualizar]);
  const moverEnViaje = viaje => (desde, hasta) => setParadas(ps => reemplazarViaje(ps, viaje, moverA(ps.filter(p => (p.viaje ?? null) === viaje), desde, hasta)));

  // #70: cuando el camión sale con una ruta, sus confirmados pasan a "en despacho" (uno por uno en el backend).
  // Si se marcó por error, se puede volver a confirmado. despachando: "ruta:estado" de lo que se está marcando.
  const [despachando, setDespachando] = useState(null);
  const [resultado, setResultado] = useState(null);
  async function cambiarRuta(grupo, destino) {
    const sale = destino === 'en_despacho';
    const porDespachar = sale ? despachables(grupo.paradas) : enDespacho(grupo.paradas);
    const nombre = grupo.viaje != null ? `la Ruta ${grupo.viaje}` : 'la ruta';
    const cuales = porDespachar.length === 1 ? 'el pedido' : `los ${porDespachar.length} pedidos`;
    if (!window.confirm(sale ? `¿Marcar ${cuales} de ${nombre} como "en despacho"?` : `¿Volver ${cuales} de ${nombre} a "confirmado"? Úsalo solo si el camión todavía no salió.`)) return;
    setDespachando(`${grupo.viaje ?? 'sin-viaje'}:${destino}`); setResultado(null);
    const fallidos = [];
    for (const id of porDespachar) {
      try { await api(`/pedidos/${id}/estado`, { method: 'POST', body: JSON.stringify({ estado: destino }) }); }
      catch (e) { fallidos.push(`#${id}: ${e.message}`); }
    }
    setDespachando(null);
    setResultado({ listos: porDespachar.length - fallidos.length, fallidos, nombre, sale });
    onActualizar();
  }

  return <section className="print-route">
    <div className="title">
      <div><h2>Ruta en curso</h2><p className="muted">{paradas.length === 1 ? '1 parada' : `${paradas.length} paradas`}{contarViajes(grupos) > 1 && ` en ${contarViajes(grupos)} rutas`}{entregadas + noEntregadas > 0 && <strong aria-live="polite"> · {entregadas} {entregadas === 1 ? 'entregada' : 'entregadas'}{noEntregadas > 0 && `, ${noEntregadas} no ${noEntregadas === 1 ? 'entregada' : 'entregadas'}`}</strong>} · en el orden guardado al planificar</p></div>
    </div>
    {paradas.length > 0 && <div className="barra-ruta no-print">
      <AsignarRepartidor paradas={paradas} porEntregar={porEntregar} onAsignada={onActualizar}/>
      <div className="acciones">
        <button aria-expanded={verMapa} onClick={() => setVerMapa(v => !v)}>{verMapa ? 'Ocultar mapa' : 'Ver mapa'}</button>
        {porEntregar.length > 0 && <DescargarPdf opciones={opcionesDescarga(agruparPorViaje(porEntregar, viajes))} onElegir={o => setHoja({ paradas: o.paradas, viajes, ruta: o.ruta })}/>}
      </div>
    </div>}
    {paradas.length > 0 && <CombustibleDeHoy revision={pedidos}/>}
    {resultado && <div role="status" className={`${resultado.fallidos.length ? 'error' : 'ok'} no-print`}>
      {resultado.listos > 0 && <p>{resultado.listos === 1 ? `1 pedido de ${resultado.nombre} quedó` : `${resultado.listos} pedidos de ${resultado.nombre} quedaron`} {resultado.sale ? 'en despacho' : resultado.listos === 1 ? 'confirmado' : 'confirmados'}.</p>}
      {resultado.fallidos.length > 0 && <><p>No se pudieron cambiar:</p><ul>{resultado.fallidos.map(f => <li key={f}>{f}</li>)}</ul></>}
    </div>}
    {repetidos.length > 0 && <p className="notice">Hay pedidos de más de una planificación con el mismo número de parada ({repetidos.map(r => (r.viaje != null ? `ruta ${r.viaje}: ${r.numeros.join(', ')}` : r.numeros.join(', '))).join('; ')}). Revisa el orden antes de entregarla al repartidor o vuelve a planificar esos pedidos.</p>}
    {manual && <p className="notice">Orden cambiado solo para imprimir o enviar. No se guarda en el servidor: al actualizar los datos vuelve al orden planificado. <button className="no-print enlace" onClick={() => setParadas(guardadas)}>Volver al orden planificado</button></p>}
    {verMapa && paradas.length > 0 && <MapaRuta grupos={grupos} foco={foco} avance/>}
    {paradas.length > 0 && !porEntregar.length && <p role="status" className="ok">Ruta terminada: el repartidor marcó todas las paradas.</p>}
    {grupos.map(v => <ViajeEnCurso key={v.viaje ?? 'sin-viaje'} viaje={v} titulo={tituloGrupo(v, grupos)} onMover={moverEnViaje(v.viaje)} onEntregar={onEntregar} onVer={verParada} siguienteId={siguiente?.id} despachando={despachando} onCambiarRuta={destino => cambiarRuta(v, destino)}/>)}
    {!paradas.length && <p>No hay una ruta en curso. Selecciona pedidos en "Preparar ruta" y planifícala.</p>}
    {hoja && <HojaDeRuta paradas={hoja.paradas} viajes={hoja.viajes} ruta={hoja.ruta} clientes={clientes} productos={productos} onClose={() => setHoja(null)}/>}
    {sinUbicacion > 0 && <p className="notice no-print">{sinUbicacion === 1 ? '1 pedido confirmado quedó' : `${sinUbicacion} pedidos confirmados quedaron`} fuera de la ruta por dirección por revisar. Corrige su ubicación en "Preparar ruta" y vuelve a planificar.</p>}
  </section>;
}

// Lo que informó cada repartidor sobre el combustible de hoy. Se recarga junto con los pedidos (revision).
function CombustibleDeHoy({ revision }) {
  const [filas, setFilas] = useState([]);
  useEffect(() => { api('/rutas/combustible').then(setFilas).catch(() => setFilas([])); }, [revision]);
  if (!filas.length) return null;
  return <div className="combustible-admin no-print">{filas.map(f => {
    const { cargas, total, noCargo } = resumenCombustible(f.registros);
    return <p key={f.repartidor}><strong>Combustible · {f.repartidor}:</strong> {noCargo ? 'no cargó hoy' : cargas.map(textoCarga).join(' | ')}{cargas.length > 1 && ` (total ${new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(total)})`}</p>;
  })}</div>;
}

// Descargar la hoja de ruta: con una sola ruta va directo; con varias pregunta cuál.
function DescargarPdf({ opciones, onElegir }) {
  const [abierto, setAbierto] = useState(false);
  const caja = useCerrarAlSalir(abierto, () => setAbierto(false));
  if (opciones.length === 1) return <button onClick={() => onElegir(opciones[0])}>Descargar PDF</button>;
  return <div className="menu-pdf" ref={caja}>
    <button aria-haspopup="menu" aria-expanded={abierto} onClick={() => setAbierto(a => !a)}>Descargar PDF <span aria-hidden="true">▾</span></button>
    {abierto && <div className="menu-pdf-lista" role="menu">{opciones.map(o => <button key={o.texto} role="menuitem" onClick={() => { setAbierto(false); onElegir(o); }}>{o.texto}</button>)}</div>}
  </div>;
}

// Asignar la ruta a un repartidor: desde ahí la ve en su celular y solo esa.
function AsignarRepartidor({ paradas, porEntregar, onAsignada }) {
  const [repartidores, setRepartidores] = useState(null);
  const [elegido, setElegido] = useState('');
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState('');
  // Lo que falta por entregar dice quién lleva la ruta ahora; con todo resuelto, quién la llevó.
  const asignado = (porEntregar.length ? porEntregar : paradas).find(p => p.repartidor_id)?.repartidor_id ?? '';
  useEffect(() => { api('/repartidores').then(setRepartidores).catch(() => setRepartidores([])); }, []);
  useEffect(() => { setElegido(String(asignado)); }, [asignado]);
  if (!repartidores?.length) return null;
  // Elegir un repartidor lo asigna de inmediato. Si falla, el selector vuelve a quien estaba.
  async function asignar(valor) {
    setElegido(valor);
    setGuardando(true); setError('');
    try { await api('/rutas/asignar', { method: 'POST', body: JSON.stringify({ repartidor_id: valor ? Number(valor) : null, pedido_ids: porEntregar.map(p => p.id) }) }); onAsignada(); }
    catch (e) { setError(e.message); setElegido(String(asignado)); }
    finally { setGuardando(false); }
  }
  return <div className="asignar-repartidor no-print">
    <label>Repartidor<select value={elegido} disabled={guardando || !porEntregar.length} onChange={e => asignar(e.target.value)}><option value="">Sin asignar</option>{repartidores.map(r => <option key={r.id} value={r.id}>{r.nombre}</option>)}</select></label>
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}

// Un viaje de la ruta en curso: encabezado y paradas, que se pueden reordenar arrastrando dentro del viaje.
function ViajeEnCurso({ viaje, titulo, onMover, onEntregar, onVer, siguienteId, despachando, onCambiarRuta }) {
  const { paradas } = viaje;
  const { propsFila, propsManilla } = useArrastre(paradas.length, onMover);
  const porDespachar = despachables(paradas).length;
  const despachados = enDespacho(paradas).length;
  const clave = viaje.viaje ?? 'sin-viaje';
  const delViaje = viaje.viaje != null ? ` de la ruta ${viaje.viaje}` : '';
  return <>
    {(titulo || porDespachar > 0 || despachados > 0) && <div className="encabezado-ruta">
      {titulo && <h3 className="viaje-titulo">{titulo}</h3>}
      {despachados > 0 && <button className="no-print" disabled={despachando !== null} aria-label={`Deshacer confirmación${delViaje}: ${despachados} ${despachados === 1 ? 'pedido' : 'pedidos'}`} onClick={() => onCambiarRuta('confirmado')}>{despachando === `${clave}:confirmado` ? 'Marcando…' : 'Deshacer confirmación'}</button>}
      {porDespachar > 0 && <button className="primary no-print" disabled={despachando !== null} aria-label={`Salió a reparto${delViaje}: ${porDespachar} ${porDespachar === 1 ? 'pedido' : 'pedidos'}`} onClick={() => onCambiarRuta('en_despacho')}>{despachando === `${clave}:en_despacho` ? 'Marcando…' : `Salió a reparto (${porDespachar})`}</button>}
    </div>}
    {paradas.map((p, i) => {
      const enlace = enlaceComoLlegar(p);
      const fila = propsFila(i);
      const lista = resuelta(p);
      const claseResultado = p.estado === 'entregado' ? ' parada-entregada' : p.estado === 'no_entregado' ? ' parada-no-entregada' : '';
      return <div key={p.id} {...fila} className={`stop ${fila.className}${claseResultado}${p.id === siguienteId ? ' parada-siguiente' : ''}`}>
        <button {...propsManilla(i, p.cliente_nombre || `pedido ${p.id}`)} className="manilla no-print">⠿</button>
        <NumeroParada numero={i + 1} parada={p} nombre={p.cliente_nombre || `pedido ${p.id}`} onVer={() => onVer(p.id)}/>
        <span>
          <strong>{p.cliente_nombre || `Cliente ${p.cliente_id}`} · #{p.id}</strong>
          <span className="etiquetas"><span className={`badge ${p.estado}`}>{lista ? textoResultado(p) : ETIQUETA_ESTADO[p.estado]}</span>{p.id === siguienteId && <span className="badge siguiente">Siguiente</span>}</span>
          <small>{p.direccion || 'Sin dirección registrada'}{p.comuna_nombre && !p.direccion?.includes(p.comuna_nombre) && ` · ${p.comuna_nombre}`}{p.cliente_telefono && ` · Tel: ${p.cliente_telefono}`}</small>
        </span>
        <div className="no-print acciones-parada">
          {enlace && !lista && <a className="boton" href={enlace} target="_blank" rel="noopener noreferrer">Cómo llegar</a>}
          {!lista && <button aria-label={`Registrar entrega de la parada ${i + 1}${delViaje}`} onClick={() => onEntregar(p.id)}>Entregado</button>}
        </div>
      </div>;
    })}
  </>;
}
