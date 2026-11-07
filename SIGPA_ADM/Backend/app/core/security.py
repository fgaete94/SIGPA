import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient

from app.core.config import settings

# Roles en app_metadata del token: solo se pueden cambiar desde el servidor de Supabase.
ROL_ADMIN = "admin"
ROL_REPARTIDOR = "repartidor"

jwks_client = PyJWKClient(f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json")


def verify_supabase_jwt(token: str) -> dict:
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
    )
    return payload


def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="No autenticado")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        payload = verify_supabase_jwt(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    return {
        "user_id": payload.get("sub"),
        "email": payload.get("email"),
        "rol": (payload.get("app_metadata") or {}).get("rol"),
    }


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("rol") != ROL_ADMIN:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sección.")
    return current_user


def require_repartidor(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("rol") != ROL_REPARTIDOR:
        raise HTTPException(status_code=403, detail="Esta sección es solo para repartidores.")
    return current_user
