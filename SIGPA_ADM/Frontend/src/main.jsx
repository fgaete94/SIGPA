import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { login, logout, onSessionExpired, restoreSession } from './auth';
import { api } from './api';
import { DIAS_REPARTO } from './clientes';
import Despacho from './Despacho';
import EditarPedido from './EditarPedido';
import FichaDelCliente from './FichaDelCliente';
import { ACCION_DESPACHO, ESTADOS, ETIQUETA_ESTADO, textoAccion, textoCambio } from './estados';
import { puedeEntregarse } from './entregas';
import { fechaCorta, fechaHora, hoyChile } from './fechas';
import { VISTA_INICIAL, VISTAS_PEDIDOS, cuentaVistas, filtrarPedidos, nombreDia, opcionesFiltros, siguienteDiaReparto, totalDe } from './filtrosPedidos';
import FormCliente from './FormCliente';
import { vigilarInactividad } from './inactividad';
import Landing from './Landing';
import NuevoPedido from './NuevoPedido';
import { puedeEditarse } from './pedidos';
import { useOrden } from './Ordenable';
import { posicionEn } from './ordenar';
import Productos from './Productos';
import RegistrarEntrega from './RegistrarEntrega';
import Reportes from './Reportes';
import ResumenDia from './ResumenDia';
import RutaEnCurso from './RutaEnCurso';
import Rutas from './Rutas';
import VistaRepartidor from './VistaRepartidor';
import './styles.css';

const money = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const name = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
function Brand({ blanco }) { return <span className="brand"><img src={blanco ? '/marca/logo-blanco.svg' : '/marca/logo.svg'} alt="Agua DM" width="2320" height="2280"/><span>SIGPA<small>Panel de operación</small></span></span>; }
const OPCIONES_RUTA = ['Preparar ruta', 'Ruta en curso'];
function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState('inicio');
  const [avisoLogin, setAvisoLogin] = useState('');
  useEffect(() => {
    let active = true;
    const unsubscribe = onSessionExpired(() => { setSession(null); setPage('login'); });
    restoreSession().then(user => { if (active) { setSession(user ? { user } : null); setLoading(false); } });
    return () => { active = false; unsubscribe(); };
  }, []);
  if (loading) return <p className="loading">Cargando sesión…</p>;
  // El repartidor solo tiene su interfaz: no pasa por el panel administrativo.
  if (session?.user.rol === 'repartidor') return <VistaRepartidor session={session} onLogout={() => { setSession(null); setPage('inicio'); }}/>;
  if (session) return <Dashboard key={session.user.id} session={session} onLogout={() => { setSession(null); setPage('inicio'); }} onInactividad={() => { setSession(null); setPage('login'); setAvisoLogin('Tu sesión se cerró por inactividad. Vuelve a ingresar.'); }}/>;
  if (page === 'inicio') return <Landing onAdmin={() => { setPage('login'); window.scrollTo(0, 0); }}/>;
  return <><header><Brand/><button onClick={() => setPage('inicio')}>Volver al inicio</button></header><Login aviso={avisoLogin} onLogin={user => { setAvisoLogin(''); setSession({ user }); }}/></>;
}
function Login({ aviso, onLogin }) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault(); setBusy(true); setError('');
    const form = new FormData(e.currentTarget);
    try { onLogin(await login(form.get('email'), form.get('password'))); }
    catch (error) { setError(error.message || 'No se pudo iniciar sesión. Revisa el correo, la contraseña y la conexión.'); setBusy(false); }
  }
  return <main className="login"><form onSubmit={submit}><span className="eyebrow">Bienvenido a SIGPA</span><h1>Acceso al equipo</h1><p>Ingresa con tu cuenta autorizada.</p>{aviso && !error && <p role="status" className="notice">{aviso}</p>}<label>Correo electrónico<input autoComplete="username" name="email" type="email" required/></label><label>Contraseña<input autoComplete="current-password" name="password" type="password" required/></label>{error && <p role="alert" className="error">{error}</p>}<button disabled={busy} className="primary">{busy ? 'Ingresando…' : 'Ingresar'}</button></form></main>;
}
function Dashboard({ session, onLogout, onInactividad }) {
  const [tab, setTab] = useState('Resumen');
  // El botón Ruta despliega sus dos opciones: Preparar ruta y Ruta en curso.
  const [rutaAbierta, setRutaAbierta] = useState(false);
  // #134: tras MINUTOS_INACTIVIDAD sin clics, teclado ni desplazamiento se cierra la sesión (también en el backend).
  useEffect(() => vigilarInactividad({ alExpirar: () => { logout(); onInactividad(); } }).detener, []);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [vistaPedidos, setVistaPedidos] = useState(VISTA_INICIAL);
  const [comuna, setComuna] = useState('');
  const [diaReparto, setDiaReparto] = useState('');
  const [planning, setPlanning] = useState(false);
  const [detail, setDetail] = useState(null);
  const [editing, setEditing] = useState(null);
  const [creandoPedido, setCreandoPedido] = useState(false);
  const [entregando, setEntregando] = useState(null);
  const [ficha, setFicha] = useState(null);
  const [editandoPedido, setEditandoPedido] = useState(null);
  const [aviso, setAviso] = useState('');
  const [actualizadoEn, setActualizadoEn] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    // /rutas/actual solo aporta el peso y el destino de cada viaje: si falla, la ruta se muestra igual sin esos datos.
    const rutaActual = api('/rutas/actual', { signal: controller.signal }).catch(e => { if (e.name === 'AbortError') throw e; return null; });
    // Los intentos fallidos de pedidos ya reagendados o cancelados solo completan el historial: si fallan, el panel se ve igual.
    const anteriores = api('/pedidos/no-entregados-anteriores', { signal: controller.signal }).catch(e => { if (e.name === 'AbortError') throw e; return []; });
    Promise.all([...['/pedidos', '/clientes', '/rutas/pedidos-pendientes', '/productos'].map(p => api(p, { signal: controller.signal })), rutaActual, anteriores])
      .then(([pedidos, clientes, pendientes, productos, actual, noEntregadosAnteriores]) => { if (![pedidos, clientes, pendientes, productos].every(Array.isArray)) throw new Error('Formato de datos inesperado.'); setData({ pedidos, clientes, pendientes, productos, viajes: Array.isArray(actual?.viajes) ? actual.viajes : [], noEntregadosAnteriores: Array.isArray(noEntregadosAnteriores) ? noEntregadosAnteriores : [] }); setActualizadoEn(new Date()); })
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setData(null); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  const pedidosConAnteriores = useMemo(() => [...(data?.pedidos ?? []), ...(data?.noEntregadosAnteriores ?? [])], [data]);
  // #73 pide datos casi en tiempo real: con Reportes abierto se recargan cada minuto (no durante una planificación).
  useEffect(() => {
    if (tab !== 'Reportes' || planning) return;
    const timer = setInterval(() => { if (!document.hidden) setRevision(r => r + 1); }, 60000);
    return () => clearInterval(timer);
  }, [tab, planning]);
  const actualizarRuta = useCallback(() => setRevision(r => r + 1), []);
  // El historial (#70) se pide junto al detalle; si falla, el detalle se muestra igual.
  async function view(id) {
    setDetail(null);
    try {
      const [pedido, historial] = await Promise.all([api(`/pedidos/${id}`), api(`/pedidos/${id}/historial`).catch(e => ({ error: e.message }))]);
      setDetail({ ...pedido, historial });
    } catch(e) { setError(e.message); }
  }
  // #70: un clic para confirmado → en despacho, o para volver si fue un error. El backend valida la transición.
  const [cambiando, setCambiando] = useState(null);
  async function cambiarEstado(p) {
    const accion = ACCION_DESPACHO[p.estado];
    if (!accion || cambiando) return;
    setCambiando(p.id); setError(''); setAviso('');
    try {
      const nuevo = await api(`/pedidos/${p.id}/estado`, { method: 'POST', body: JSON.stringify({ estado: accion.destino }) });
      setData(d => ({ ...d, pedidos: d.pedidos.map(x => x.id === nuevo.id ? nuevo : x) }));
      setAviso(`Pedido #${p.id}: ${ETIQUETA_ESTADO[p.estado]} → ${ETIQUETA_ESTADO[nuevo.estado]}.`);
      setRevision(r => r + 1);
    } catch(e) { setError(`Pedido #${p.id}: ${e.message}`); }
    finally { setCambiando(null); }
  }
  // Decisión sobre un pedido no entregado: vuelve a Pendientes para el siguiente día de reparto, o se cancela.
  async function resolverNoEntregado(p, ruta, cuerpo, mensaje) {
    if (cambiando) return;
    setCambiando(p.id); setError(''); setAviso('');
    try { await api(ruta, { method: 'POST', body: JSON.stringify(cuerpo) }); setAviso(mensaje); setRevision(r => r + 1); }
    catch (e) { setError(`Pedido #${p.id}: ${e.message}`); }
    finally { setCambiando(null); }
  }
  function reagendar(p) {
    const para = siguienteDiaReparto(hoyChile());
    resolverNoEntregado(p, `/pedidos/${p.id}/reagendar`, { para }, `Pedido #${p.id} reagendado para el ${fechaCorta(`${para}T12:00:00Z`)}: volvió a Pendientes.`);
  }
  function cancelarPedido(p) {
    if (!window.confirm(`¿Cancelar el pedido #${p.id} de ${p.cliente_nombre || 'este cliente'}? No se volverá a despachar.`)) return;
    resolverNoEntregado(p, `/pedidos/${p.id}/cancelar`, {}, `Pedido #${p.id} cancelado.`);
  }
  // #99: abre el registro de entrega (desde la tabla, el detalle o la ruta en curso).
  function entregar(id) { setDetail(null); setAviso(''); setError(''); setEntregando(id); }
  function entregaRegistrada(p) {
    const { lineas, ...pedido } = p;
    const parcial = lineas.some(l => (l.cantidad_entregada ?? 0) < l.cantidad_solicitada);
    setData(d => ({ ...d, pedidos: d.pedidos.map(x => x.id === pedido.id ? pedido : x) }));
    setAviso(`Pedido #${p.id} entregado${parcial ? ' en forma parcial' : ''} por ${money(p.total)}.`);
    setEntregando(null); setRevision(r => r + 1);
  }
  const filtrosPedidos = { texto: query, estado: vistaPedidos, comuna, dia: diaReparto };
  const pedidos = filtrarPedidos(data?.pedidos || [], filtrosPedidos);
  const opciones = opcionesFiltros(data?.pedidos || [], filtrosPedidos);
  const cuentas = cuentaVistas(data?.pedidos || [], filtrosPedidos);
  const hayFiltros = Boolean(query || vistaPedidos !== VISTA_INICIAL || comuna || diaReparto);
  function limpiarFiltros() { setQuery(''); setVistaPedidos(VISTA_INICIAL); setComuna(''); setDiaReparto(''); }
  const clientes = (data?.clientes || []).filter(c => `${name(c)} ${c.telefono || ''}`.toLowerCase().includes(query.toLowerCase()));
  // En el celular las pestañas van en una fila que se desliza: la elegida se trae a la vista.
  const irA = (t, e) => { setTab(t); limpiarFiltros(); setAviso(''); e.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' }); };
  const navButton = t => <button key={t} className={tab === t ? 'active' : ''} aria-current={tab === t ? 'page' : undefined} onClick={e => { setRutaAbierta(false); irA(t, e); }}>{t}</button>;
  const opcionRuta = t => <button key={t} className={`nav-sub${tab === t ? ' active' : ''}`} aria-current={tab === t ? 'page' : undefined} onClick={e => irA(t, e)}>{t}</button>;
  const enRuta = OPCIONES_RUTA.includes(tab);
  return <div className="shell"><aside><Brand blanco/><nav className="pestanas" aria-label="Secciones del panel"><div className="nav-label">Operación</div>{['Resumen','Pedidos'].map(navButton)}<button className={`nav-grupo${enRuta ? ' en-grupo' : ''}`} aria-expanded={rutaAbierta} onClick={() => setRutaAbierta(a => !a)}>Ruta<span aria-hidden="true">{rutaAbierta ? '▴' : '▾'}</span></button>{rutaAbierta && OPCIONES_RUTA.map(opcionRuta)}{['Despachos'].map(navButton)}<div className="nav-label">Administración</div>{['Clientes','Productos'].map(navButton)}<div className="nav-label">Análisis</div>{navButton('Reportes')}</nav><div className="account"><small>{session.user.email}</small><button onClick={async () => { await logout(); onLogout(); }}>Cerrar sesión</button></div></aside><main className="workspace"><header><span>Panel administrativo</span><span className="badge">Agua DM</span></header><div className="content"><div className="title"><div><span className="eyebrow">Centro de operaciones</span><h1>{tab}</h1><p>Consulta y organiza la información de SIGPA.</p></div><button disabled={busy || planning} onClick={() => setRevision(r => r + 1)}>Actualizar datos</button></div>{error && <p role="alert" className="error">{error}</p>}{aviso && <p role="status" className="ok">{aviso}</p>}{busy && <p role="status">Cargando información…</p>}{data && <>
  {tab === 'Resumen' && <><ResumenDia pedidos={pedidosConAnteriores} clientes={data.clientes}/></>}
  {tab === 'Despachos' && <Despacho pedidos={pedidosConAnteriores} clientes={data.clientes}/>}
  {tab === 'Pedidos' && <section><div className="vistas-pedidos" role="group" aria-label="Qué pedidos ver">{VISTAS_PEDIDOS.map(([v, texto]) => <button type="button" key={v} aria-pressed={vistaPedidos === v} onClick={() => setVistaPedidos(v)}>{texto} ({cuentas.get(v) ?? 0})</button>)}</div><div className="filtros-pedidos">
    <label className="filtro-buscar">Cliente<input placeholder="Buscar por nombre o N° de pedido…" value={query} onChange={e=>setQuery(e.target.value)}/></label>
    <label>Comuna<select value={comuna} onChange={e=>setComuna(e.target.value)}><option value="">Todas</option>{opciones.comunas.map(([c,n])=><option key={c} value={c}>{c} ({n})</option>)}</select></label>
    <label>Día de reparto<select value={diaReparto} onChange={e=>setDiaReparto(e.target.value)}><option value="">Todos</option>{opciones.dias.map(([d,n])=><option key={d} value={d}>{nombreDia(d)} ({n})</option>)}</select></label>
  </div>
  <div className="resultado-filtros"><p className="resumen-filtros muted" aria-live="polite">{pedidos.length === 1 ? `1 pedido ${ETIQUETA_ESTADO[vistaPedidos].toLowerCase()}` : `${pedidos.length} pedidos ${ETIQUETA_ESTADO[vistaPedidos].toLowerCase()}s`} · {money(totalDe(pedidos))}{hayFiltros && <> · <button type="button" className="enlace" onClick={limpiarFiltros}>Limpiar filtros</button></>}</p><button className="primary" onClick={()=>{setCreandoPedido(true); setAviso('');}}>Nuevo pedido</button></div>
  {pedidos.length ? <OrderTable paraPestanaPedidos rows={pedidos} view={view} cambiarEstado={cambiarEstado} cambiando={cambiando} entregar={entregar} reagendar={reagendar} cancelarPedido={cancelarPedido}/> : <p>No hay pedidos con estos filtros.</p>}</section>}
  {tab === 'Clientes' && <section><div className="filters"><input aria-label="Buscar clientes" placeholder="Buscar nombre o teléfono…" value={query} onChange={e=>setQuery(e.target.value)}/><button className="primary" onClick={()=>{setEditing('nuevo'); setAviso('');}}>Nuevo cliente</button></div><TablaClientes clientes={clientes} onEditar={c => { setEditing(c); setAviso(''); }} onVerFicha={id => { setAviso(''); setFicha(id); }}/></section>}
  {tab === 'Productos' && <Productos productos={data.productos} onCambio={() => setRevision(r => r + 1)}/>}
  <div hidden={tab !== 'Reportes'}><Reportes pedidos={data.pedidos} pedidosConAnteriores={pedidosConAnteriores} clientes={data.clientes} actualizadoEn={actualizadoEn}/></div>
  <div hidden={tab !== 'Ruta en curso'}><RutaEnCurso pedidos={data.pedidos} clientes={data.clientes} productos={data.productos} viajes={data.viajes} activa={tab === 'Ruta en curso' && !planning} onActualizar={actualizarRuta} onEntregar={entregar}/></div>
  <div hidden={tab !== 'Preparar ruta'}><Rutas pendientes={data.pendientes} clientes={data.clientes} busy={busy} onActualizar={() => setRevision(r => r + 1)} onPlanificando={setPlanning}/></div>
  </>}{detail && <DetallePedido detail={detail} onClose={()=>setDetail(null)} onEntregar={entregar} onEditar={p => { setDetail(null); setAviso(''); setEditandoPedido(p); }}/>}{editandoPedido && data && <EditarPedido pedido={editandoPedido} cliente={data.clientes.find(c => c.id === editandoPedido.cliente_id)} productos={data.productos} onClose={()=>setEditandoPedido(null)} onSaved={p => { const { lineas, ...pedido } = p; setData(d => ({ ...d, pedidos: d.pedidos.map(x => x.id === pedido.id ? pedido : x) })); setAviso(`Pedido #${p.id} actualizado: ${lineas.map(l => `${l.cantidad_solicitada} × ${l.producto_nombre}`).join(', ')} · ${money(p.total)}.`); setEditandoPedido(null); setRevision(r => r + 1); }}/>}{ficha && <FichaDelCliente clienteId={ficha} onClose={()=>setFicha(null)} onVerPedido={id => { setFicha(null); view(id); }} onEditar={c => { setFicha(null); setEditing(data?.clientes.find(x => x.id === c.id) || c); }}/>}{entregando && <RegistrarEntrega pedidoId={entregando} onClose={()=>setEntregando(null)} onRegistrado={entregaRegistrada}/>}{editing && <FormCliente cliente={editing === 'nuevo' ? null : editing} clientes={data?.clientes || []} onClose={()=>setEditing(null)} onSaved={c=>{setAviso(editing === 'nuevo' ? `Cliente ${name(c)} creado (#${c.id}). Ya puede pedir por WhatsApp desde su número.` : `Cambios de ${name(c)} guardados.`); setEditing(null); setRevision(r=>r+1);}}/>}{creandoPedido && data && <NuevoPedido clientes={data.clientes} productos={data.productos} onClose={()=>setCreandoPedido(false)} onSaved={p=>{setAviso(`Pedido #${p.id} creado para ${p.cliente_nombre || 'el cliente'} por ${money(p.total)}. Quedó pendiente.`); setCreandoPedido(false); setRevision(r=>r+1);}}/>}</div></main></div>;
}

// #137: columnas ordenables de la tabla de clientes. El día de reparto sigue el orden de la semana.
const COLUMNAS_CLIENTE = {
  cliente: c => name(c),
  telefono: c => c.telefono,
  direccion: c => c.direccion,
  comuna: c => c.comuna_nombre,
  tipo: c => c.tipo_cliente_nombre,
  dia: c => posicionEn(DIAS_REPARTO.map(([valor]) => valor))(c.dia_reparto),
  estado: c => (c.activo ? 'Activo' : 'Inactivo'),
};
const dia = valor => DIAS_REPARTO.find(([v]) => v === valor)?.[1] || valor;

function TablaClientes({ clientes, onEditar, onVerFicha }) {
  const { filas, encabezado } = useOrden(clientes, COLUMNAS_CLIENTE);
  return <div className="table">
    <table className="tabla-movil">
      <thead>
        <tr>
          {encabezado('cliente', 'Cliente')}
          {encabezado('telefono', 'Teléfono')}
          {encabezado('direccion', 'Dirección')}
          {encabezado('comuna', 'Comuna')}
          {encabezado('tipo', 'Tipo')}
          {encabezado('dia', 'Día de reparto')}
          {encabezado('estado', 'Estado')}
          <th>Acciones</th>
        </tr>
      </thead>
      <tbody>
        {filas.map(c => <tr key={c.id}>
          <td data-label="Cliente">{name(c)}</td>
          <td data-label="Teléfono">{c.telefono || '—'}</td>
          <td data-label="Dirección">{c.direccion || 'Sin dirección'}</td>
          <td data-label="Comuna">{c.comuna_nombre || 'Sin comuna'}</td>
          <td data-label="Tipo">{c.tipo_cliente_nombre || '—'}</td>
          <td data-label="Día de reparto">{c.dia_reparto ? dia(c.dia_reparto) : 'Sin asignar'}</td>
          <td data-label="Estado">{c.activo ? 'Activo' : 'Inactivo'}</td>
          <td className="celda-acciones">
            <button aria-label={`Ver ficha de ${name(c)}`} onClick={() => onVerFicha(c.id)}>Ver ficha</button>
            <button aria-label={`Editar a ${name(c)}`} onClick={() => onEditar(c)}>Editar</button>
          </td>
        </tr>)}
      </tbody>
    </table>
    {!clientes.length && <p>No se encontraron clientes.</p>}
  </div>;
}

function DetallePedido({ detail, onClose, onEntregar, onEditar }) {
  const { historial } = detail;
  return <div className="modal" role="dialog" aria-modal="true" aria-label="Detalle del pedido"><section>
    <button autoFocus onClick={onClose}>Cerrar detalle</button><h2>Pedido #{detail.id}</h2>
    <p>{detail.cliente_nombre} · <span className={`badge ${detail.estado}`}>{ETIQUETA_ESTADO[detail.estado]}</span></p>
    <p>{detail.direccion_despacho || 'Sin dirección de despacho informada'}</p>
    {detail.lineas.map((l,i)=><p key={i}>{l.cantidad_solicitada === 0 ? <>{l.cantidad_entregada ?? 0} × {l.producto_nombre} · {money(l.precio_unitario)} · agregado al entregar</> : <>{l.cantidad_solicitada} × {l.producto_nombre} · {money(l.precio_unitario)}{l.cantidad_entregada != null && ` · entregó ${l.cantidad_entregada}`}</>}</p>)}<strong>Total: {money(detail.total)}</strong>
    {(puedeEntregarse(detail.estado) || puedeEditarse(detail.estado)) && <p className="acciones">
      {puedeEntregarse(detail.estado) && <button className="primary" onClick={() => onEntregar(detail.id)}>Registrar entrega</button>}
      {puedeEditarse(detail.estado) && <button onClick={() => onEditar(detail)}>Editar pedido</button>}
    </p>}
    <h3>Historial</h3>
    {historial.error ? <p className="error">No se pudo cargar el historial: {historial.error}</p>
      : historial.length ? <ol className="historial">{historial.map((h, i) => <li key={i}><strong>{textoCambio(h)}</strong>{h.detalle && <span className="detalle-cambio">{h.detalle}</span>}<small>{fechaHora(h.fecha)} · {textoAccion(h.accion)} · {h.usuario}</small></li>)}</ol>
      : <p className="muted">Sin cambios de estado registrados. Los pedidos creados por el bot no quedan en el historial.</p>}
  </section></div>;
}
// #137: columnas ordenables de la tabla de pedidos (Resumen y Pedidos). El estado sigue el ciclo del pedido.
const COLUMNAS_PEDIDO = {
  pedido: p => p.id,
  fecha: p => p.creado_en,
  cliente: p => p.cliente_nombre,
  comuna: p => p.comuna_nombre,
  estado: p => posicionEn(ESTADOS)(p.estado),
  total: p => Number(p.total),
};

// paraPestanaPedidos: agrega la fecha en que se hizo el pedido y omite el estado, que ya lo dice la vista elegida.
function OrderTable({ rows, view, cambiarEstado, cambiando, entregar, paraPestanaPedidos = false, reagendar, cancelarPedido }) {
  const { filas, encabezado } = useOrden(rows, COLUMNAS_PEDIDO);
  return <div className="table">
    <table className="tabla-movil">
      <thead>
        <tr>
          {encabezado('pedido', 'Pedido')}
          {paraPestanaPedidos && encabezado('fecha', 'Fecha')}
          {encabezado('cliente', 'Cliente')}
          {encabezado('comuna', 'Comuna')}
          {!paraPestanaPedidos && encabezado('estado', 'Estado')}
          {encabezado('total', 'Total')}
          <th>Despacho</th>
          <th>Detalle</th>
        </tr>
      </thead>
      <tbody>
        {filas.map(p => {
          const accion = ACCION_DESPACHO[p.estado];
          const decidir = paraPestanaPedidos && p.estado === 'no_entregado';
          return <tr key={p.id}>
            <td data-label="Pedido">#{p.id}</td>
            {paraPestanaPedidos && <td data-label="Fecha">{fechaCorta(p.creado_en)}{p.reagendado_para && <small className="reagendado">Reagendado para el {fechaCorta(`${p.reagendado_para}T12:00:00Z`)}</small>}</td>}
            <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
            <td data-label="Comuna">{p.comuna_nombre || '—'}</td>
            {!paraPestanaPedidos && <td data-label="Estado"><span className={`badge ${p.estado}`}>{ETIQUETA_ESTADO[p.estado] || p.estado}</span></td>}
            <td data-label="Total">{money(p.total)}</td>
            <td className={accion || decidir ? 'celda-acciones' : 'sin-accion'}>
              {accion
                ? <button className={p.estado === 'confirmado' ? 'despachar' : ''} disabled={cambiando !== null} aria-label={`${accion.texto}: pedido ${p.id}`} onClick={() => cambiarEstado(p)}>
                    {cambiando === p.id ? 'Guardando…' : accion.texto}
                  </button>
                : decidir
                  ? <>
                      <button className="boton-reagendar" disabled={cambiando !== null} aria-label={`Reagendar: pedido ${p.id}`} onClick={() => reagendar(p)}>{cambiando === p.id ? 'Guardando…' : 'Reagendar'}</button>
                      <button className="boton-cancelar" disabled={cambiando !== null} aria-label={`Cancelar: pedido ${p.id}`} onClick={() => cancelarPedido(p)}>Cancelar pedido</button>
                    </>
                  : '—'}
              {p.estado === 'en_despacho' && <button aria-label={`Registrar entrega: pedido ${p.id}`} disabled={cambiando !== null} onClick={() => entregar(p.id)}>Entregado</button>}
            </td>
            <td className="celda-acciones">
              <button aria-label={`Ver pedido ${p.id}`} onClick={() => view(p.id)}>Ver detalle</button>
            </td>
          </tr>;
        })}
      </tbody>
    </table>
    {!rows.length && <p>No hay pedidos para mostrar.</p>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
