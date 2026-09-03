# SIGPA – Sistema Integral de Gestión de Pedidos y Administración

Proyecto de portafolio (Proyecto de Título) desarrollado para **ADM**, distribuidora de agua purificada que opera en la V Región de Chile, atendiendo clientes B2C y B2B a través de WhatsApp.

> Curso PY71461-012V – Proyecto de Portafolio, Duoc UC (Sede Viña del Mar)
> Defensa proyectada para el segundo semestre de 2026

---

## 📌 Descripción del proyecto

Actualmente, ADM recibe pedidos por WhatsApp y los consolida manualmente en Excel, un proceso propenso a errores y con baja escalabilidad. Este proyecto busca **automatizar la toma de pedidos y la gestión de despachos** mediante un chatbot con inteligencia artificial integrado a WhatsApp, un panel administrativo web para el equipo de ADM y la automatización de la planificación de rutas de despacho.

Los despachos se organizan por **comuna y día de la semana** a lo largo de distintas ciudades de la región, por lo que el sistema también apunta a mejorar la trazabilidad y organización logística del negocio.

### Épicas del proyecto (priorización MoSCoW)

| ID | Épica | Prioridad |
|---|---|---|
| EP-01 | Chatbot conversacional en WhatsApp para la recepción de pedidos | Must have |
| EP-02 | Panel administrativo web para la gestión de pedidos y clientes | Must have |
| EP-03 | Dashboard de ventas con indicadores comerciales | Should have |
| EP-04 | Optimización y automatización de rutas de despacho | Should have |

> ⚠️ Fuera del alcance: facturación electrónica a través del portal del SII.

---

## 🛒 Catálogo de productos (MVP)

- Botellón 20L
- Bidón 10L
- Botella 1.5L
- Botella 500ml

---

## 🧠 Estado actual del proyecto

Se encuentra en desarrollo el **agente conversacional de pedidos**. La arquitectura confirmada del proyecto usa la **API de OpenAI** como motor de lenguaje para interpretar los mensajes del chatbot (ver tabla de arquitectura más abajo).

Durante las primeras pruebas locales se evaluó también un modelo autoalojado con [Ollama](https://ollama.com/) (`llama3.2`), en un entorno virtual gestionado con **Miniconda**. Esa opción se descartó en favor de la API de OpenAI: un LLM autoalojado implica un costo fijo mensual independiente del volumen de uso además de overhead de mantención (RAM, reinicios, actualizaciones, escalamiento manual), mientras que el costo estimado de la API de OpenAI para el volumen de este proyecto es marginal.

El foco actual ha sido refinar el flujo conversacional multi-turno para evitar comportamientos indeseados del agente, como iniciar la conversación sin input del usuario, no volver a preguntar por productos adicionales, o inferir cantidades en lugar de solicitarlas explícitamente. El agente mantiene historial de conversación por cada sesión de chat y expone una función de extracción estructurada (JSON) para detectar pedidos completados.

---

## 🏗️ Arquitectura confirmada

| Capa | Tecnología |
|---|---|
| Mensajería | Meta WhatsApp Cloud API |
| Backend | FastAPI, contenedorizado con Docker |
| Motor de lenguaje (LLM) | API de OpenAI (servicio externo) |
| Panel administrativo web | React |
| Base de datos | Supabase (sobre PostgreSQL) |
| Automatización de rutas | **n8n** — recibe el aviso de pedido confirmado desde el panel (nodo Webhook), lee/actualiza pedidos (nodo Postgres/Supabase) y calcula distancias/optimiza el orden de paradas vía **OpenRouteService** (nodo HTTP Request) |
| Despliegue | Render o Railway (pendiente decisión final) |

La automatización de rutas mediante n8n **ya forma parte del alcance del MVP**, no es una fase futura. Se eligió OpenRouteService (API de rutas basada en OpenStreetMap) por sobre Google Maps Platform, que ya no ofrece un nivel gratuito perpetuo.

El backend, el panel, la base de datos y n8n caben completos dentro del nivel gratuito de Render; los servicios externos (API de OpenAI, OpenRouteService) se pagan por uso o tienen nivel gratuito según volumen.

---

## 🚧 Riesgos identificados

- Aprobación de la API de WhatsApp Business (Meta) para uso productivo.
- Calidad y estructura de las direcciones ingresadas por los clientes (texto no estructurado).
- Manejo de pedidos concurrentes en tiempo real.

## 📋 Decisiones pendientes

- Elegir definitivamente entre Render y Railway para el hosting de producción.

---

## 👥 Equipo

Proyecto desarrollado por un equipo de 3 estudiantes de Ingeniería en Informática, Duoc UC – Sede Viña del Mar:

- **Ariel Olivares** — Project Manager / mandante
- **Felipe Gaete** — Integración y automatización (incluye la automatización de rutas de despacho vía n8n)
- **Julio Cifuentes** — Arquitectura y sistemas

---

## 📄 Licencia

Proyecto académico desarrollado con fines educativos en el marco del Proyecto de Título de Duoc UC.
