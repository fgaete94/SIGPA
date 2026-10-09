# Imagen de n8n para desplegar el workflow de rutas de SIGPA en un host
# público (Render). Ver DESPLIEGUE.md.
#
# La versión debe ser la misma que N8N_VERSION en el .env del docker-compose
# local. REEMPLAZAR_VERSION_N8N es un placeholder: reemplazarlo por la
# versión fija (ej. "1.xx.y") o pasar --build-arg N8N_VERSION=... Con el
# placeholder el build falla a propósito (la imagen no existe).
ARG N8N_VERSION=REEMPLAZAR_VERSION_N8N
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
