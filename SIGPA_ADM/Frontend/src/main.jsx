import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { login, logout, onSessionExpired, restoreSession } from './auth';
import { api } from './api';
import { DIAS_REPARTO } from './clientes';
import { ACCION_DESPACHO, ESTADOS, ETIQUETA_ESTADO, textoAccion, textoCambio } from './estados';
import { fechaHora } from './fechas';
import FormCliente from './FormCliente';
import { vigilarInactividad } from './inactividad';
import Landing from './Landing';
import NuevoPedido from './NuevoPedido';
import { useOrden } from './Ordenable';
import { posicionEn } from './ordenar';
import Productos from './Productos';
import Reportes from './Reportes';
import RutaEnCurso from './RutaEnCurso';
import Rutas from './Rutas';
import './styles.css';

const money = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const name = c => [c.nombre, c.apellido_paterno, c.apellido_materno].filter(Boolean).join(' ');
function Brand({ blanco }) { return <span className="brand"><img src={blanco ? '/marca/logo-blanco.svg' : '/marca/logo.svg'} alt="Agua DM" width="2320" height="2280"/><span>SIGPA<small>Panel de operación</small></span></span>; }
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
  // #134: tras MINUTOS_INACTIVIDAD sin clics, teclado ni desplazamiento se cierra la sesión (también en el backend).
  useEffect(() => vigilarInactividad({ alExpirar: () => { logout(); onInactividad(); } }).detener, []);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [planning, setPlanning] = useState(false);
  const [detail, setDetail] = useState(null);
  const [editing, setEditing] = useState(null);
  const [creandoPedido, setCreandoPedido] = useState(false);
  const [aviso, setAviso] = useState('');
  const [actualizadoEn, setActualizadoEn] = useState(null);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    Promise.all(['/pedidos', '/clientes', '/rutas/pedidos-pendientes', '/productos'].map(p => api(p, { signal: controller.signal })))
      .then(([pedidos, clientes, pendientes, productos]) => { if (![pedidos, clientes, pendientes, productos].every(Array.isArray)) throw new Error('Formato de datos inesperado.'); setData({ pedidos, clientes, pendientes, productos }); setActualizadoEn(new Date()); })
      .catch(e => { if (e.name !== 'AbortError') { setError(e.message); setData(null); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  // #73 pide datos casi en tiempo real: con Reportes abierto se recargan cada minuto (no durante una planificación).
  useEffect(() => {
    if (tab !== 'Reportes' || planning) return;
    const timer = setInterval(() => { if (!document.hidden) setRevision(r => r + 1); }, 60000);
    return () => clearInterval(timer);
  }, [tab, planning]);
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
  const pedidos = (data?.pedidos || []).filter(p => (!status || p.estado === status) && `${p.id} ${p.cliente_nombre || ''} ${p.comuna_nombre || ''}`.toLowerCase().includes(query.toLowerCase()));
  const clientes = (data?.clientes || []).filter(c => `${name(c)} ${c.telefono || ''}`.toLowerCase().includes(query.toLowerCase()));
  // En el celular las pestañas van en una fila que se desliza: la elegida se trae a la vista.
  const navButton = t => <button key={t} className={tab === t ? 'active' : ''} aria-current={tab === t ? 'page' : undefined} onClick={e => { setTab(t); setQuery(''); setStatus(''); setAviso(''); e.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }}>{t}</button>;
  return <div className="shell"><aside><Brand blanco/><nav className="pestanas" aria-label="Secciones del panel"><div className="nav-label">Operación</div>{['Resumen','Pedidos','Clientes','Productos','Rutas'].map(navButton)}<div className="nav-label">Análisis</div>{navButton('Reportes')}</nav><div className="account"><small>{session.user.email}</small><button onClick={async () => { await logout(); onLogout(); }}>Cerrar sesión</button></div></aside><main className="workspace"><header><span>Panel administrativo</span><span className="badge">Agua DM</span></header><div className="content"><div className="title"><div><span className="eyebrow">Centro de operaciones</span><h1>{tab}</h1><p>Consulta y organiza la información de SIGPA.</p></div><button disabled={busy || planning} onClick={() => setRevision(r => r + 1)}>Actualizar datos</button></div>{error && <p role="alert" className="error">{error}</p>}{aviso && <p role="status" className="ok">{aviso}</p>}{busy && <p role="status">Cargando información…</p>}{data && <>
  {tab === 'Resumen' && <><div className="kpis">{[['Pedidos registrados',data.pedidos.length],['Pendientes',data.pedidos.filter(p=>p.estado==='pendiente').length],['Confirmados',data.pedidos.filter(p=>p.estado==='confirmado').length],['Clientes activos',data.clientes.filter(c=>c.activo).length]].map(([label,n]) => <article key={label}><span>{label}</span><strong>{n}</strong></article>)}</div><p className="muted">Totales de todos los registros disponibles, sin filtro de fecha.</p><section><h2>Últimos pedidos</h2><OrderTable rows={data.pedidos.slice(0,8)} view={view} cambiarEstado={cambiarEstado} cambiando={cambiando}/></section></>}
  {tab === 'Pedidos' && <section><div className="filters"><input aria-label="Buscar pedidos" placeholder="Buscar cliente, comuna o ID…" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="Estado del pedido" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Todos los estados</option>{ESTADOS.map(s=><option key={s} value={s}>{ETIQUETA_ESTADO[s]} ({data.pedidos.filter(p=>p.estado===s).length})</option>)}</select><button className="primary" onClick={()=>{setCreandoPedido(true); setAviso('');}}>Nuevo pedido</button></div><OrderTable rows={pedidos} view={view} cambiarEstado={cambiarEstado} cambiando={cambiando}/></section>}
  {tab === 'Clientes' && <section><div className="filters"><input aria-label="Buscar clientes" placeholder="Buscar nombre o teléfono…" value={query} onChange={e=>setQuery(e.target.value)}/><button className="primary" onClick={()=>{setEditing('nuevo'); setAviso('');}}>Nuevo cliente</button></div><TablaClientes clientes={clientes} onEditar={c => { setEditing(c); setAviso(''); }}/></section>}
  {tab === 'Productos' && <Productos productos={data.productos} onCambio={() => setRevision(r => r + 1)}/>}
  <div hidden={tab !== 'Reportes'}><Reportes pedidos={data.pedidos} clientes={data.clientes} actualizadoEn={actualizadoEn}/></div>
  <div hidden={tab !== 'Rutas'}><RutaEnCurso pedidos={data.pedidos} clientes={data.clientes} productos={data.productos} onActualizar={() => setRevision(r => r + 1)}/><Rutas pendientes={data.pendientes} busy={busy} onActualizar={() => setRevision(r => r + 1)} onPlanificando={setPlanning}/></div>
  </>}{detail && <DetallePedido detail={detail} onClose={()=>setDetail(null)}/>}{editing && <FormCliente cliente={editing === 'nuevo' ? null : editing} clientes={data?.clientes || []} onClose={()=>setEditing(null)} onSaved={c=>{setAviso(editing === 'nuevo' ? `Cliente ${name(c)} creado (#${c.id}). Ya puede pedir por WhatsApp desde su número.` : `Cambios de ${name(c)} guardados.`); setEditing(null); setRevision(r=>r+1);}}/>}{creandoPedido && data && <NuevoPedido clientes={data.clientes} productos={data.productos} onClose={()=>setCreandoPedido(false)} onSaved={p=>{setAviso(`Pedido #${p.id} creado para ${p.cliente_nombre || 'el cliente'} por ${money(p.total)}. Quedó pendiente.`); setCreandoPedido(false); setRevision(r=>r+1);}}/>}</div></main></div>;
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

function TablaClientes({ clientes, onEditar }) {
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
            <button aria-label={`Editar a ${name(c)}`} onClick={() => onEditar(c)}>Editar</button>
          </td>
        </tr>)}
      </tbody>
    </table>
    {!clientes.length && <p>No se encontraron clientes.</p>}
  </div>;
}

function DetallePedido({ detail, onClose }) {
  const { historial } = detail;
  return <div className="modal" role="dialog" aria-modal="true" aria-label="Detalle del pedido"><section>
    <button autoFocus onClick={onClose}>Cerrar detalle</button><h2>Pedido #{detail.id}</h2>
    <p>{detail.cliente_nombre} · <span className={`badge ${detail.estado}`}>{ETIQUETA_ESTADO[detail.estado]}</span></p>
    <p>{detail.direccion_despacho || 'Sin dirección de despacho informada'}</p>
    {detail.lineas.map((l,i)=><p key={i}>{l.cantidad_solicitada} × {l.producto_nombre} · {money(l.precio_unitario)}</p>)}<strong>Total: {money(detail.total)}</strong>
    <h3>Historial de estados</h3>
    {historial.error ? <p className="error">No se pudo cargar el historial: {historial.error}</p>
      : historial.length ? <ol className="historial">{historial.map((h, i) => <li key={i}><strong>{textoCambio(h)}</strong><small>{fechaHora(h.fecha)} · {textoAccion(h.accion)} · {h.usuario}</small></li>)}</ol>
      : <p className="muted">Sin cambios de estado registrados. Los pedidos creados por el bot y las entregas todavía no quedan en el historial.</p>}
  </section></div>;
}
// #137: columnas ordenables de la tabla de pedidos (Resumen y Pedidos). El estado sigue el ciclo del pedido.
const COLUMNAS_PEDIDO = {
  pedido: p => p.id,
  cliente: p => p.cliente_nombre,
  comuna: p => p.comuna_nombre,
  estado: p => posicionEn(ESTADOS)(p.estado),
  total: p => Number(p.total),
};

function OrderTable({ rows, view, cambiarEstado, cambiando }) {
  const { filas, encabezado } = useOrden(rows, COLUMNAS_PEDIDO);
  return <div className="table">
    <table className="tabla-movil">
      <thead>
        <tr>
          {encabezado('pedido', 'Pedido')}
          {encabezado('cliente', 'Cliente')}
          {encabezado('comuna', 'Comuna')}
          {encabezado('estado', 'Estado')}
          {encabezado('total', 'Total')}
          <th>Despacho</th>
          <th>Detalle</th>
        </tr>
      </thead>
      <tbody>
        {filas.map(p => {
          const accion = ACCION_DESPACHO[p.estado];
          return <tr key={p.id}>
            <td data-label="Pedido">#{p.id}</td>
            <td data-label="Cliente">{p.cliente_nombre || `Cliente ${p.cliente_id}`}</td>
            <td data-label="Comuna">{p.comuna_nombre || '—'}</td>
            <td data-label="Estado"><span className={`badge ${p.estado}`}>{ETIQUETA_ESTADO[p.estado] || p.estado}</span></td>
            <td data-label="Total">{money(p.total)}</td>
            <td className={accion ? 'celda-acciones' : 'sin-accion'}>
              {accion
                ? <button className={p.estado === 'confirmado' ? 'despachar' : ''} disabled={cambiando !== null} aria-label={`${accion.texto}: pedido ${p.id}`} onClick={() => cambiarEstado(p)}>
                    {cambiando === p.id ? 'Guardando…' : accion.texto}
                  </button>
                : '—'}
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
