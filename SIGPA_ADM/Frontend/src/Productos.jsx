import React, { useState } from 'react';
import { api } from './api';
import { datosProducto, productoForm } from './inventario';

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const litros = n => n == null ? '—' : `${new Intl.NumberFormat('es-CL').format(Number(n))} L`;

// Pestaña Productos: catálogo con precio, stock y capacidad (GET, POST y PATCH /productos).
export default function Productos({ productos, onCambio }) {
  const [editando, setEditando] = useState(null);
  const [aviso, setAviso] = useState('');
  const ordenados = [...productos].sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));
  return <section>
    <div className="title"><div><h2>Catálogo</h2><p className="muted">{productos.length} productos. El precio se usa en los pedidos nuevos y en la página web.</p></div><button className="primary" onClick={() => { setEditando('nuevo'); setAviso(''); }}>Nuevo producto</button></div>
    {aviso && <p role="status" className="ok">{aviso}</p>}
    <div className="table"><table>
      <thead><tr><th>Producto</th><th>Precio</th><th>Stock</th><th>Capacidad</th><th>Acciones</th></tr></thead>
      <tbody>{ordenados.map(p => <tr key={p.id}><td>{p.nombre}</td><td>{dinero(Number(p.precio_unitario))}</td><td>{p.stock}</td><td>{litros(p.capacidad_litros)}</td><td><button aria-label={`Editar ${p.nombre}`} onClick={() => { setEditando(p); setAviso(''); }}>Editar</button></td></tr>)}</tbody>
    </table>{!productos.length && <p>No hay productos registrados.</p>}</div>
    {editando && <FormProducto producto={editando === 'nuevo' ? null : editando} productos={productos} onClose={() => setEditando(null)} onSaved={p => { setEditando(null); setAviso(`Producto "${p.nombre}" guardado.`); onCambio(); }}/>}
  </section>;
}

function FormProducto({ producto, productos, onClose, onSaved }) {
  const nuevo = !producto;
  const [form, setForm] = useState(() => productoForm(producto || {}));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const field = (key, label, props) => <label>{label}<input value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} {...props}/></label>;
  const renombrado = !nuevo && form.nombre.trim().replace(/\s+/g, ' ') !== producto.nombre;
  const otroPrecio = !nuevo && productoForm(producto).precio_unitario !== form.precio_unitario.replace(/[$\s.]/g, '');

  async function submit(e) {
    e.preventDefault();
    const { datos, cambios, error } = datosProducto(form, productos, producto);
    if (error) return setError(error);
    if (cambios && !Object.keys(cambios).length) return onClose();
    setBusy(true); setError('');
    try {
      onSaved(nuevo
        ? await api('/productos', { method: 'POST', body: JSON.stringify(datos) })
        : await api(`/productos/${producto.id}`, { method: 'PATCH', body: JSON.stringify(cambios) }));
    } catch (e) { setError(e.message); setBusy(false); }
  }

  return <div className="modal" role="dialog" aria-modal="true" aria-label={nuevo ? 'Nuevo producto' : 'Editar producto'}><section><form onSubmit={submit} noValidate>
    <span className="eyebrow">{nuevo ? 'Catálogo' : `Producto #${producto.id}`}</span><h2>{nuevo ? 'Nuevo producto' : 'Editar producto'}</h2>
    {field('nombre', 'Nombre', { autoFocus: true, maxLength: 50, placeholder: 'Ej. Bidón 20L Recarga' })}
    <div className="campos">
      {field('precio_unitario', 'Precio (CLP)', { inputMode: 'numeric', placeholder: '2.500' })}
      {field('stock', 'Stock', { inputMode: 'numeric' })}
    </div>
    {field('capacidad_litros', 'Capacidad en litros (opcional)', { inputMode: 'decimal', placeholder: 'Vacío si no aplica, ej. dispensadores' })}
    {renombrado && <p className="notice">La página web busca este producto por su nombre exacto. Si lo cambias, mostrará el precio de respaldo hasta actualizar su nombre en <code>marca.js</code>.</p>}
    {otroPrecio && <p className="notice">El nuevo precio se aplica a los pedidos nuevos y a la página web. Los pedidos ya ingresados conservan su precio.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : nuevo ? 'Crear producto' : 'Guardar cambios'}</button></div>
  </form></section></div>;
}
