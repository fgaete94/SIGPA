// Campos de texto que el panel permite editar; los vacíos opcionales se guardan como null.
const TEXTO = ['nombre', 'apellido_paterno', 'apellido_materno', 'telefono', 'direccion'];
const IDS = ['tipo_cliente_id', 'sector_id'];
const OBLIGATORIOS = { nombre: 'El nombre es obligatorio.', telefono: 'El teléfono es obligatorio.' };
// Valores del enum dia_semana de la base (sin tilde) y cómo se muestran.
export const DIAS_REPARTO = [['Lunes', 'Lunes'], ['Martes', 'Martes'], ['Miercoles', 'Miércoles'], ['Jueves', 'Jueves'], ['Viernes', 'Viernes']];

// Como normalizar_telefono del backend (cliente_lookup.py): solo dígitos y con 56 delante.
export function normalizarTelefono(telefono) {
  const digitos = String(telefono ?? '').replace(/\D/g, '');
  return digitos.length === 9 ? `56${digitos}` : digitos;
}
export const telefonoRepetido = (telefono, clientes, exceptoId) => clientes.find(c => c.id !== exceptoId && normalizarTelefono(c.telefono) === telefono);

export function clienteForm(c = {}) {
  return {
    ...Object.fromEntries(TEXTO.map(k => [k, c[k] ?? ''])),
    ...Object.fromEntries(IDS.map(k => [k, c[k] == null ? '' : String(c[k])])),
    dia_reparto: c.dia_reparto ?? '', activo: c.activo !== false, opt_out_whatsapp: !!c.opt_out_whatsapp,
  };
}

function leerForm(form, telefonoActual) {
  const valores = {
    ...Object.fromEntries(TEXTO.map(k => [k, form[k].trim() || null])),
    ...Object.fromEntries(IDS.map(k => [k, form[k] === '' ? null : Number(form[k])])),
    dia_reparto: form.dia_reparto || null, activo: form.activo, opt_out_whatsapp: form.opt_out_whatsapp,
  };
  for (const [k, mensaje] of Object.entries(OBLIGATORIOS)) if (!valores[k]) return { error: mensaje };
  const telefono = normalizarTelefono(valores.telefono);
  // El mismo número de antes escrito con otro formato no es un cambio (y no se valida un dato antiguo que no se tocó).
  if (telefonoActual && telefono === normalizarTelefono(telefonoActual)) return { valores: { ...valores, telefono: telefonoActual }, aviso: '' };
  // El bot reconoce al cliente por su teléfono: los nuevos se guardan siempre como 56 + 9 dígitos.
  if (!/^56\d{9}$/.test(telefono)) return { error: 'El teléfono debe tener 9 dígitos (ej. 9 1234 5678), con o sin +56.' };
  valores.telefono = telefono;
  return { valores, aviso: telefono[2] === '9' ? '' : 'Ese número no es un celular: el cliente no recibirá mensajes por WhatsApp.' };
}

const repetido = c => ({ error: `Ya existe un cliente con ese teléfono: ${[c.nombre, c.apellido_paterno].filter(Boolean).join(' ')} (#${c.id}).` });

// Datos para POST /clientes.
export function nuevoCliente(form, clientes = []) {
  const { valores, aviso, error } = leerForm(form);
  if (error) return { error };
  const otro = telefonoRepetido(valores.telefono, clientes);
  if (otro) return repetido(otro);
  return aviso ? { datos: valores, aviso } : { datos: valores };
}

// Devuelve solo los campos modificados, para que el PATCH y la auditoría reflejen el cambio real.
export function cambiosCliente(original, form, clientes = []) {
  const { valores: nuevo, aviso, error } = leerForm(form, original.telefono);
  if (error) return { error };
  const cambios = Object.fromEntries(Object.entries(nuevo).filter(([k, v]) => v !== (original[k] ?? (typeof v === 'boolean' ? false : null))));
  if (!cambios.telefono) return { cambios };
  const otro = telefonoRepetido(cambios.telefono, clientes, original.id);
  if (otro) return repetido(otro);
  return aviso ? { cambios, aviso } : { cambios };
}
