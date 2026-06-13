from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_repository import get_setting, set_setting
from app.config import Settings
from app.db.models import User

TOKEN_STORE_KEY = "admin.one_time_login_tokens"
ONE_TIME_LINK_TTL_SECONDS = 600
_SALT = "admin-one-time-login"


class AdminOneTimeLinkError(Exception):
    """Raised when a one-time admin login link cannot be consumed."""


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    secret = settings.session_secret or "change-me-in-production"
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_store(store: dict[str, Any]) -> dict[str, Any]:
    tokens = store.get("tokens")
    if not isinstance(tokens, dict):
        return {"tokens": {}}

    now = _now()
    cleaned: dict[str, Any] = {}
    for digest, record in tokens.items():
        if not isinstance(record, dict):
            continue
        expires_at = record.get("expires_at")
        if not isinstance(expires_at, str):
            continue
        try:
            expires = datetime.fromisoformat(expires_at)
        except ValueError:
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > now:
            cleaned[str(digest)] = record
    return {"tokens": cleaned}


async def create_admin_one_time_login_link(
    session: AsyncSession,
    *,
    user: User,
    app_base_url: str,
    settings: Settings,
) -> str:
    tg_id = str(user.telegram_user_id)
    username = user.telegram_username or ""
    name = " ".join(
        part
        for part in (user.telegram_first_name or "", user.telegram_last_name or "")
        if part
    )
    payload = {
        "tg_id": tg_id,
        "username": username,
        "name": name,
        "jti": token_urlsafe(18),
    }
    token = _serializer(settings).dumps(payload)

    store = _clean_store(await get_setting(session, TOKEN_STORE_KEY))
    tokens = store.setdefault("tokens", {})
    expires_at = _now() + timedelta(seconds=ONE_TIME_LINK_TTL_SECONDS)
    tokens[_token_hash(token)] = {
        "tg_id": tg_id,
        "expires_at": expires_at.isoformat(),
    }
    await set_setting(session, TOKEN_STORE_KEY, store, tg_id=tg_id)

    base_url = app_base_url.rstrip("/")
    return f"{base_url}/admin/auth/telegram/one-time?token={token}"


async def consume_admin_one_time_login_token(
    session: AsyncSession,
    *,
    token: str,
    settings: Settings,
) -> dict[str, str]:
    if not token:
        raise AdminOneTimeLinkError("missing token")

    try:
        payload = _serializer(settings).loads(
            token,
            max_age=ONE_TIME_LINK_TTL_SECONDS,
        )
    except SignatureExpired as exc:
        raise AdminOneTimeLinkError("expired token") from exc
    except BadSignature as exc:
        raise AdminOneTimeLinkError("invalid token") from exc

    if not isinstance(payload, dict):
        raise AdminOneTimeLinkError("invalid payload")

    tg_id = str(payload.get("tg_id") or "")
    if not tg_id:
        raise AdminOneTimeLinkError("missing tg_id")

    store = _clean_store(await get_setting(session, TOKEN_STORE_KEY))
    tokens = store.setdefault("tokens", {})
    digest = _token_hash(token)
    record = tokens.pop(digest, None)
    if not isinstance(record, dict):
        await set_setting(session, TOKEN_STORE_KEY, store, tg_id=tg_id)
        raise AdminOneTimeLinkError("used token")

    if str(record.get("tg_id") or "") != tg_id:
        await set_setting(session, TOKEN_STORE_KEY, store, tg_id=tg_id)
        raise AdminOneTimeLinkError("token owner mismatch")

    await set_setting(session, TOKEN_STORE_KEY, store, tg_id=tg_id)
    return {
        "tg_id": tg_id,
        "username": str(payload.get("username") or ""),
        "name": str(payload.get("name") or ""),
    }
