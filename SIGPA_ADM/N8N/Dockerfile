# Imagen de n8n para desplegar el workflow de rutas de SIGPA en un host
# público (Render). Ver DESPLIEGUE.md.
#
# Versión fija: 2.42.5, la que corre en producción (la base de datos de n8n ya
# tiene sus migraciones y no se puede volver a una versión anterior). El workflow
# se validó en local con 2.41.6. Debe ser la misma que N8N_VERSION en el .env del
# docker-compose local. No usar "latest".
#
# Alternativa: en Render se puede usar la imagen oficial docker.io/n8nio/n8n:2.42.5
# sin este Dockerfile, cargando a mano las variables ENV de abajo (ver DESPLIEGUE.md).
ARG N8N_VERSION=2.42.5
FROM n8nio/n8n:${N8N_VERSION}

# Valores no secretos por defecto (se pueden sobrescribir en el entorno).
# Los secretos (OPENROUTESERVICE_API_KEY, N8N_ENCRYPTION_KEY, DB_*) y el
# depósito NUNCA van aquí: se cargan como variables de entorno del servicio.
ENV GENERIC_TIMEZONE=America/Santiago \
    TZ=America/Santiago \
    N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
    N8N_DIAGNOSTICS_ENABLED=false \
    EXECUTIONS_TIMEOUT=40 \
    N8N_PORT=5678

EXPOSE 5678
