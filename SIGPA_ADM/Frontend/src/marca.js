// Información pública de Agua DM para la landing.
// Los precios vienen de la tabla producto vía GET /productos (ver catalogo.js); aquí solo van
// fotos y textos. Días y contacto se tomaron de Facebook: confirmar con ADM.
export const WHATSAPP = '56990667641';
export const WHATSAPP_VISIBLE = '+56 9 9066 7641';
export const SITIO = 'aguadm.cl';

export const COMUNAS = ['Viña del Mar', 'Recreo', 'Reñaca', 'Concón', 'Quilpué', 'Villa Alemana'];

// dia: índice de Date#getDay (1 = lunes)
export const REPARTO = [
  { dia: 1, nombre: 'Lunes', zona: 'Quilpué y Villa Alemana' },
  { dia: 2, nombre: 'Martes', zona: 'Recreo' },
  { dia: 3, nombre: 'Miércoles', zona: 'Viña Centro' },
  { dia: 4, nombre: 'Jueves', zona: 'Concón' },
  { dia: 5, nombre: 'Viernes', zona: 'Reñaca' },
];

// producto: nombre exacto en la tabla producto; de ahí sale el precio. Si un producto deja de
// existir en la base, su tarjeta (o línea de precio) desaparece de la landing.
export const BIDONES = [
  { id: 'bidon-20', nombre: 'Bidón 20 L', imagen: '/marca/bidon-20.webp', precios: [['Bidón nuevo', 'Bidón 20L Nuevo'], ['Recarga', 'Bidón 20L Recarga']] },
  { id: 'bidon-12', nombre: 'Bidón 12 L', imagen: '/marca/bidon-12.webp', precios: [['Bidón nuevo', 'Bidón 12L Nuevo'], ['Recarga', 'Bidón 12L Recarga']] },
];

export const DISPENSADORES = [
  { id: 'dispensador-basico', nombre: 'Dispensador básico', producto: 'Dispensador Básico', detalle: 'De sobremesa, sin electricidad.', imagen: '/marca/dispensador-basico.webp' },
  { id: 'dispensador-usb', nombre: 'Dispensador USB', producto: 'Dispensador USB', detalle: 'Bomba recargable que va sobre el bidón.', imagen: '/marca/dispensador-usb.webp' },
];

export const PROMOS = [
  { id: 'promo-basico-1', nombre: 'Dispensador básico + 1 bidón', producto: 'Promo Dispensador Básico + 1 Bidón', detalle: 'Un dispensador básico y un bidón.', imagen: '/marca/promo-basico-1.webp' },
  { id: 'promo-basico-2', nombre: 'Dispensador básico + 2 bidones', producto: 'Promo Dispensador Básico + 2 Bidones', detalle: 'Un dispensador básico y dos bidones.', imagen: '/marca/promo-basico-2.webp' },
  { id: 'promo-usb-1', nombre: 'Dispensador USB + 1 bidón', producto: 'Promo Dispensador USB + 1 Bidón', detalle: 'Un dispensador USB y un bidón.', imagen: '/marca/promo-usb-1.webp' },
  { id: 'promo-usb-2', nombre: 'Dispensador USB + 2 bidones', producto: 'Promo Dispensador USB + 2 Bidones', detalle: 'Un dispensador USB y dos bidones.', imagen: '/marca/promo-usb-2.webp' },
];

// Últimos precios conocidos de la tabla producto (2026-10-08). Solo se muestran mientras la API
// responde (Render tarda en despertar) o si falla; nunca reemplazan a los de la base.
export const PRECIOS_RESPALDO = {
  'Bidón 12L Nuevo': 6000, 'Bidón 12L Recarga': 2000, 'Bidón 20L Nuevo': 6000, 'Bidón 20L Recarga': 2500,
  'Dispensador Básico': 7000, 'Dispensador USB': 7000,
  'Promo Dispensador Básico + 1 Bidón': 11000, 'Promo Dispensador Básico + 2 Bidones': 17000,
  'Promo Dispensador USB + 1 Bidón': 11000, 'Promo Dispensador USB + 2 Bidones': 17000,
};

export const pesos = n => new Intl.NumberFormat('es-CL', { style: 'currency', currency: 'CLP', maximumFractionDigits: 0 }).format(n);

// Abre WhatsApp con el mensaje ya escrito; sin mensaje, abre el chat vacío.
export function enlaceWhatsApp(mensaje) {
  return `https://wa.me/${WHATSAPP}${mensaje ? `?text=${encodeURIComponent(mensaje)}` : ''}`;
}
