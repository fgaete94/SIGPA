# ADM Bot – Gestión Inteligente de Pedidos y Despacho

Proyecto de portafolio (Proyecto de Título) desarrollado para **ADM**, distribuidora de agua purificada que opera en la V Región de Chile, atendiendo clientes B2C y B2B a través de WhatsApp.

> Curso PY71461-012V – Proyecto de Portafolio, Duoc UC (Sede Viña del Mar)
> Defensa proyectada para el segundo semestre de 2026

---

## 📌 Descripción del proyecto

Actualmente, ADM recibe pedidos por WhatsApp y los consolida manualmente en Excel, un proceso propenso a errores y con baja escalabilidad. Este proyecto busca **automatizar la toma de pedidos y la gestión de despachos** mediante un chatbot con inteligencia artificial integrado a WhatsApp, junto con un panel administrativo web para el equipo de ADM.

Los despachos se organizan por **comuna y día de la semana** a lo largo de distintas ciudades de la región, por lo que el sistema también apunta a mejorar la trazabilidad y organización logística del negocio.

### Objetivos principales

- 🤖 **Chatbot conversacional por WhatsApp** para la toma de pedidos, capaz de reconocer productos, cantidades y direcciones de forma natural.
- 🖥️ **Panel de administración web** para gestión de clientes, historial de pedidos y despachos.
- 📊 **Dashboard de ventas** con visibilidad del negocio para el equipo de ADM.
- 🗺️ **(Futuro)** Optimización de rutas de despacho mediante integración con Google Maps / Waze.

> ⚠️ Fuera del alcance: facturación electrónica a través del portal del SII.

---

## 🛒 Catálogo de productos (MVP)

- Botellón 20L
- Bidón 10L
- Botella 1.5L
- Botella 500ml

---

## 🧠 Estado actual del proyecto

Se encuentra en desarrollo el **agente conversacional de pedidos**, probado localmente con:

- [Ollama](https://ollama.com/) + modelo `llama3.2`
- Entorno virtual gestionado con **Miniconda**
- Historial de conversación mantenido en cada sesión de chat
- Función de extracción estructurada (JSON) para detectar pedidos completados

El foco actual ha sido refinar el flujo conversacional multi-turno para evitar comportamientos indeseados del agente, como iniciar la conversación sin input del usuario, no volver a preguntar por productos adicionales, o inferir cantidades en lugar de solicitarlas explícitamente.

---

## 🏗️ Arquitectura planificada

| Capa | Tecnología |
|---|---|
| Backend | FastAPI |
| Orquestación de agente / IA | LangChain / LlamaIndex |
| Frontend (panel admin) | React + Tailwind |
| Base de datos | PostgreSQL + SQLAlchemy |
| Mensajería | Meta WhatsApp Cloud API |
| Ruteo (fase futura) | Google Maps Platform / Waze |
| Despliegue | Railway / Render |

---

## 🚧 Riesgos identificados

- Aprobación de la API de WhatsApp Business (Meta) para uso productivo.
- Calidad y estructura de las direcciones ingresadas por los clientes (texto no estructurado).
- Manejo de pedidos concurrentes en tiempo real.

---

## 👥 Equipo

Proyecto desarrollado por un equipo de 3 estudiantes de Ingeniería en Informática, Duoc UC – Sede Viña del Mar.

---

## 📄 Licencia

Proyecto académico desarrollado con fines educativos en el marco del Proyecto de Título de Duoc UC.
