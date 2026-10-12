// Productos del catálogo (pestaña Productos): validación para POST /productos y PATCH /productos/{id}.
export const productoForm = (p = {}) => ({
  nombre: p.nombre ?? '',
  precio_unitario: p.precio_unitario == null ? '' : String(Number(p.precio_unitario)),
  stock: String(p.stock ?? 0),
  capacidad_litros: p.capacidad_litros == null ? '' : String(Number(p.capacidad_litros)),
});

// Pesos chilenos: acepta "3500", "3.500" o "$ 3.500" (el punto es separador de miles).
const leerPesos = texto => { const limpio = String(texto).replace(/[$\s.]/g, ''); return /^\d+$/.test(limpio) ? Number(limpio) : NaN; };
// Litros: acepta "12", "1,5" o "1.5".
const leerLitros = texto => { const limpio = String(texto).trim().replace(',', '.'); return /^\d+(\.\d+)?$/.test(limpio) ? Number(limpio) : NaN; };

export function datosProducto(form, productos = [], original = null) {
  const nombre = form.nombre.trim().replace(/\s+/g, ' ');
  if (!nombre) return { error: 'El nombre es obligatorio.' };
  if (nombre.length > 50) return { error: 'El nombre puede tener hasta 50 caracteres.' };
  if (productos.some(p => p.id !== original?.id && p.nombre.trim().toLowerCase() === nombre.toLowerCase())) return { error: `Ya existe un producto llamado "${nombre}".` };
  const precio = leerPesos(form.precio_unitario);
  if (!(precio > 0)) return { error: 'El precio debe ser un monto en pesos mayor que 0 (ej. 3.500).' };
  if (!/^\d+$/.test(form.stock.trim())) return { error: 'El stock debe ser un número entero, 0 o más.' };
  const capacidad = form.capacidad_litros.trim() ? leerLitros(form.capacidad_litros) : null;
  if (Number.isNaN(capacidad) || capacidad === 0) return { error: 'La capacidad debe ser un número de litros mayor que 0, o quedar vacía si no aplica.' };
  const datos = { nombre, precio_unitario: precio, stock: Number(form.stock), capacidad_litros: capacidad };
  if (!original) return { datos };
  // Edición: solo lo que cambió, para que la auditoría refleje el cambio real.
  const actual = { nombre: original.nombre, precio_unitario: Number(original.precio_unitario), stock: original.stock, capacidad_litros: original.capacidad_litros == null ? null : Number(original.capacidad_litros) };
  return { cambios: Object.fromEntries(Object.entries(datos).filter(([k, v]) => v !== actual[k])) };
}
