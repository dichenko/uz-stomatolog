import base64
import hashlib
import logging
import secrets
from typing import Any

import httpx
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

TELEGRAM_OIDC_AUTH = "https://oauth.telegram.org/auth"
TELEGRAM_OIDC_TOKEN = "https://oauth.telegram.org/token"
TELEGRAM_OIDC_JWKS = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"


def generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = (
        base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    )
    return code_verifier, code_challenge


def build_authorization_url(
    settings: Settings, state: str, code_challenge: str
) -> str:
    params = {
        "client_id": settings.telegram_oidc_client_id,
        "redirect_uri": settings.telegram_oidc_redirect_uri,
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{TELEGRAM_OIDC_AUTH}?{query}"


async def exchange_code_for_tokens(
    settings: Settings, code: str, code_verifier: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TELEGRAM_OIDC_TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.telegram_oidc_client_id,
                "client_secret": settings.telegram_oidc_client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": settings.telegram_oidc_redirect_uri,
            },
        )
        response.raise_for_status()
        return response.json()


async def verify_id_token(
    settings: Settings, id_token: str
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        jwks_response = await client.get(TELEGRAM_OIDC_JWKS)
        jwks_response.raise_for_status()
        jwks_data = jwks_response.json()

    try:
        payload = jose_jwt.decode(
            id_token,
            key=jwks_data,
            algorithms=["RS256"],
            audience=settings.telegram_oidc_client_id,
            issuer=TELEGRAM_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except JWTError as exc:
        raise AuthError(f"id_token verification failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise AuthError("id_token payload is not a dict")
    return payload


def get_tg_id_from_payload(payload: dict[str, Any]) -> str:
    tg_id = payload.get("id")
    if tg_id is None:
        raise AuthError("id_token missing 'id' claim")
    return str(tg_id)


def is_admin(tg_id: str, settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    admin_ids = (
        resolved.telegram_admin_ids.split(",") if resolved.telegram_admin_ids else ""
    )
    return tg_id in {aid.strip() for aid in admin_ids.split(",") if aid.strip()}


SESSION_KEY_TG_ID = "tg_id"
SESSION_KEY_USERNAME = "username"
SESSION_KEY_NAME = "name"
SESSION_KEY_PICTURE = "picture"


class AuthError(RuntimeError):
    pass
