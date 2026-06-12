import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import Settings, get_settings


def mask_api_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if len(cleaned) < 13:
        return "******"
    return f"{cleaned[:5]}...{cleaned[-7:]}"


def fingerprint_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()[:24]


def encrypt_api_key(api_key: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(api_key.strip().encode("utf-8")).decode("ascii")


def decrypt_api_key(encrypted_api_key: str, settings: Settings | None = None) -> str:
    return _fernet(settings).decrypt(encrypted_api_key.encode("ascii")).decode("utf-8")


def _fernet(settings: Settings | None = None) -> Fernet:
    resolved = settings or get_settings()
    raw = (
        resolved.llm_config_encryption_key.get_secret_value().strip()
        if resolved.llm_config_encryption_key
        else ""
    )
    if not raw:
        raw = resolved.session_secret or "dev-insecure-llm-config-key"
    return Fernet(_normalize_fernet_key(raw))


def _normalize_fernet_key(raw: str) -> bytes:
    encoded = raw.encode("utf-8")
    try:
        Fernet(encoded)
        return encoded
    except Exception:
        digest = hashlib.sha256(encoded).digest()
        return base64.urlsafe_b64encode(digest)
