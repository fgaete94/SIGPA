import React, { useState } from 'react';
import { api } from './api';
import { datosProducto, productoForm } from './inventario';
import { useOrden } from './Ordenable';
import { BIDONES, DISPENSADORES, PROMOS } from './marca';

// Foto de cada producto: la misma de la página web, buscada por el nombre exacto del producto.
const FOTOS = new Map([
  ...BIDONES.flatMap(b => b.precios.map(([, nombre]) => [nombre, b.imagen])),
  ...[...DISPENSADORES, ...PROMOS].map(p => [p.producto, p.imagen]),
]);

const dinero = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP' }).format(n);
const litros = n => n == null ? '—' : `${new Intl.NumberFormat('es-CL').format(Number(n))} L`;

// #137: columnas ordenables del catálogo. Sin clic, los productos van por nombre.
const COLUMNAS_PRODUCTO = {
  producto: p => p.nombre,
  precio: p => Number(p.precio_unitario),
  stock: p => p.stock,
  capacidad: p => (p.capacidad_litros == null ? null : Number(p.capacidad_litros)),
};

// Pestaña Productos: catálogo con precio, stock y capacidad (GET, POST y PATCH /productos).
export default function Productos({ productos, onCambio }) {
  const [editando, setEditando] = useState(null);
  const [aviso, setAviso] = useState('');
  const porNombre = [...productos].sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));
  const { filas, encabezado } = useOrden(porNombre, COLUMNAS_PRODUCTO);
  return <section className="productos-vista">
    <div className="title"><div><h2>{productos.length} productos</h2><p className="muted">El precio se usa en los pedidos nuevos y en la página web.</p></div><button className="primary" onClick={() => { setEditando('nuevo'); setAviso(''); }}>Nuevo producto</button></div>
    {aviso && <p role="status" className="ok">{aviso}</p>}
    <div className="table">
      <table className="tabla-movil">
        <thead>
          <tr>
            {encabezado('producto', 'Producto')}
            {encabezado('precio', 'Precio')}
            {encabezado('stock', 'Stock')}
            {encabezado('capacidad', 'Capacidad')}
            <th>Acciones</th>
          </tr>
        </thead>
        <tbody>
          {filas.map(p => <tr key={p.id}>
            <td data-label="Producto" className="col-producto"><span className="foto-producto">{FOTOS.has(p.nombre) && <img src={FOTOS.get(p.nombre)} alt="" loading="lazy"/>}</span>{p.nombre}</td>
            <td data-label="Precio" className="col-precio">{dinero(Number(p.precio_unitario))}</td>
            <td data-label="Stock" className="col-stock">{p.stock}</td>
            <td data-label="Capacidad">{p.capacidad_litros == null ? '—' : <span className="etiqueta-cliente">{litros(p.capacidad_litros)}</span>}</td>
            <td className="celda-acciones">
              <button aria-label={`Editar ${p.nombre}`} onClick={() => { setEditando(p); setAviso(''); }}>Editar</button>
            </td>
          </tr>)}
        </tbody>
      </table>
      {!productos.length && <p>No hay productos registrados.</p>}
    </div>
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

  return <div className="modal" role="dialog" aria-modal="true" aria-label={nuevo ? 'Nuevo producto' : 'Editar producto'}><section className="form-cliente form-producto"><form onSubmit={submit} noValidate>
    <header className="ficha-cabecera">
      <div><h2>{nuevo ? 'Nuevo producto' : producto.nombre}</h2><p>{nuevo ? 'Se agrega al catálogo y queda disponible para los pedidos.' : `Editar producto #${producto.id}`}</p></div>
      {!nuevo && FOTOS.has(producto.nombre) && <span className="foto-producto"><img src={FOTOS.get(producto.nombre)} alt=""/></span>}
    </header>
    <h3 className="grupo-form">Producto</h3>
    {field('nombre', 'Nombre', { autoFocus: true, maxLength: 50, placeholder: 'Ej. Bidón 20L Recarga' })}
    <div className="campos">
      {field('precio_unitario', 'Precio (CLP)', { inputMode: 'numeric', placeholder: '2.500' })}
      {field('stock', 'Stock', { inputMode: 'numeric' })}
    </div>
    <h3 className="grupo-form">Capacidad</h3>
    {field('capacidad_litros', 'Capacidad en litros (opcional)', { inputMode: 'decimal', placeholder: 'Vacío si no aplica, ej. dispensadores' })}
    {renombrado && <p className="notice">La página web busca este producto por su nombre exacto. Si lo cambias, mostrará el precio de respaldo hasta actualizar su nombre en <code>marca.js</code>.</p>}
    {otroPrecio && <p className="notice">El nuevo precio se aplica a los pedidos nuevos y a la página web. Los pedidos ya ingresados conservan su precio.</p>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="actions"><button type="button" onClick={onClose} disabled={busy}>Cancelar</button><button className="primary" disabled={busy}>{busy ? 'Guardando…' : nuevo ? 'Crear producto' : 'Guardar cambios'}</button></div>
  </form></section></div>;
}
