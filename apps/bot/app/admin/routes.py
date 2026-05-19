import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_302_FOUND

from app.admin.audit_repository import log_audit
from app.admin.auth import (
    SESSION_KEY_NAME,
    SESSION_KEY_PICTURE,
    SESSION_KEY_TG_ID,
    SESSION_KEY_USERNAME,
    AuthError,
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    get_tg_id_from_payload,
    is_admin,
    verify_id_token,
)
from app.admin.settings_repository import get_all_settings, get_setting, set_setting
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Auth routes ────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _render_login_page()


@router.get("/auth/telegram/start")
async def auth_telegram_start(request: Request):
    settings = get_settings()
    if not settings.telegram_oidc_client_id:
        return HTMLResponse("<h1>Telegram OIDC не настроен</h1>", status_code=500)

    state = _random_hex(32)
    code_verifier, code_challenge = generate_pkce_pair()
    request.session["oidc_state"] = state
    request.session["oidc_code_verifier"] = code_verifier

    url = build_authorization_url(settings, state, code_challenge)
    return RedirectResponse(url, status_code=HTTP_302_FOUND)


@router.get("/auth/telegram/callback")
async def auth_telegram_callback(request: Request):
    settings = get_settings()
    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")

    saved_state = request.session.pop("oidc_state", None)
    code_verifier = request.session.pop("oidc_code_verifier", None)

    if not code or not saved_state or state != saved_state:
        return HTMLResponse("<h1>Invalid state</h1>", status_code=400)

    if not code_verifier:
        return HTMLResponse("<h1>Session expired</h1>", status_code=400)

    try:
        tokens = await exchange_code_for_tokens(settings, code, code_verifier)
        id_token = tokens.get("id_token")
        if not id_token:
            raise AuthError("No id_token in response")

        payload = await verify_id_token(settings, str(id_token))
        tg_id = get_tg_id_from_payload(payload)

        async with _get_db_session() as session:
            await log_audit(
                session,
                admin_tg_id=tg_id,
                action="login_attempt",
                ip_address=request.client.host if request.client else None,
            )
            await session.commit()
    except AuthError as exc:
        logger.warning("admin_auth_error", extra={"error": str(exc)})
        return HTMLResponse(f"<h1>Authentication failed</h1><p>{exc}</p>", status_code=403)

    if not is_admin(tg_id, settings):
        async with _get_db_session() as session:
            await log_audit(
                session,
                admin_tg_id=tg_id,
                action="login_forbidden",
                ip_address=request.client.host if request.client else None,
            )
            await session.commit()
        return HTMLResponse("<h1>Access denied</h1>", status_code=403)

    request.session[SESSION_KEY_TG_ID] = tg_id
    request.session[SESSION_KEY_USERNAME] = str(payload.get("username") or "")
    request.session[SESSION_KEY_NAME] = str(payload.get("first_name") or "")
    request.session[SESSION_KEY_PICTURE] = str(payload.get("photo_url") or "")

    async with _get_db_session() as session:
        await log_audit(
            session,
            admin_tg_id=tg_id,
            action="login_success",
            ip_address=request.client.host if request.client else None,
        )
        await session.commit()

    return RedirectResponse("/admin/", status_code=HTTP_302_FOUND)


@router.post("/auth/logout")
async def auth_logout(request: Request):
    tg_id = request.session.get(SESSION_KEY_TG_ID, "")
    request.session.clear()
    if tg_id:
        async with _get_db_session() as session:
            await log_audit(
                session,
                admin_tg_id=str(tg_id),
                action="logout",
                ip_address=request.client.host if request.client else None,
            )
            await session.commit()
    return RedirectResponse("/admin/login", status_code=HTTP_302_FOUND)


# ── Protected helper ────────────────────────────────────────────────

async def _require_admin(request: Request) -> str:
    settings = get_settings()
    tg_id = request.session.get(SESSION_KEY_TG_ID)
    if not tg_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_admin(str(tg_id), settings):
        request.session.clear()
        raise HTTPException(status_code=403, detail="Access denied")
    return str(tg_id)


# ── API routes ──────────────────────────────────────────────────────

@router.get("/api/me")
async def api_me(request: Request):
    await _require_admin(request)
    return {
        "tgId": request.session.get(SESSION_KEY_TG_ID),
        "username": request.session.get(SESSION_KEY_USERNAME),
        "name": request.session.get(SESSION_KEY_NAME),
        "picture": request.session.get(SESSION_KEY_PICTURE),
        "role": "admin",
    }


@router.get("/api/settings")
async def api_settings(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        return await get_all_settings(session)


@router.put("/api/settings/system-prompt")
async def api_save_system_prompt(request: Request):
    tg_id = await _require_admin(request)
    body = await request.json()
    text = str(body.get("text", ""))[:80000]
    async with _get_db_session() as session:
        old = await get_setting(session, "llm.system_prompt")
        await set_setting(session, "llm.system_prompt", {"text": text}, tg_id)
        await log_audit(
            session, admin_tg_id=tg_id, action="update_setting",
            setting_key="llm.system_prompt", old_value=old, new_value={"text": text},
            ip_address=request.client.host if request.client else None,
        )
        await session.commit()
    return {"ok": True}


@router.put("/api/settings/welcome-messages")
async def api_save_welcome_messages(request: Request):
    tg_id = await _require_admin(request)
    body = await request.json()
    value = {
        "ru": str(body.get("ru", "") or "")[:10000],
        "uz": str(body.get("uz", "") or "")[:10000],
        "en": str(body.get("en", "") or "")[:10000],
    }
    async with _get_db_session() as session:
        old = await get_setting(session, "bot.welcome_messages")
        await set_setting(session, "bot.welcome_messages", value, tg_id)
        await log_audit(
            session, admin_tg_id=tg_id, action="update_setting",
            setting_key="bot.welcome_messages", old_value=old, new_value=value,
            ip_address=request.client.host if request.client else None,
        )
        await session.commit()
    return {"ok": True}


@router.put("/api/settings/tts-prompts")
async def api_save_tts_prompts(request: Request):
    tg_id = await _require_admin(request)
    body = await request.json()
    value = {
        "ru": str(body.get("ru", "") or "")[:20000],
        "uz": str(body.get("uz", "") or "")[:20000],
        "en": str(body.get("en", "") or "")[:20000],
    }
    async with _get_db_session() as session:
        old = await get_setting(session, "tts.prompts")
        await set_setting(session, "tts.prompts", value, tg_id)
        await log_audit(
            session, admin_tg_id=tg_id, action="update_setting",
            setting_key="tts.prompts", old_value=old, new_value=value,
            ip_address=request.client.host if request.client else None,
        )
        await session.commit()
    return {"ok": True}


@router.put("/api/settings/clinic-info")
async def api_save_clinic_info(request: Request):
    tg_id = await _require_admin(request)
    body = await request.json()
    text = str(body.get("text", ""))[:200000]
    async with _get_db_session() as session:
        old = await get_setting(session, "clinic.info")
        await set_setting(session, "clinic.info", {"text": text}, tg_id)
        await log_audit(
            session, admin_tg_id=tg_id, action="update_setting",
            setting_key="clinic.info", old_value=old, new_value={"text": text},
            ip_address=request.client.host if request.client else None,
        )
        await session.commit()
    return {"ok": True}


# ── Page routes ─────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def admin_index():
    return RedirectResponse("/admin/system-prompt", status_code=HTTP_302_FOUND)


@router.get("/system-prompt", response_class=HTMLResponse)
async def system_prompt_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "llm.system_prompt")
    current = str(data.get("text", ""))
    return _render_page("Системный промпт", "system-prompt", current, "text", single_field=True)


@router.get("/welcome-messages", response_class=HTMLResponse)
async def welcome_messages_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "bot.welcome_messages")
    return _render_page(
        "Первое сообщение", "welcome-messages",
        data, "welcome", single_field=False,
    )


@router.get("/tts-prompts", response_class=HTMLResponse)
async def tts_prompts_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "tts.prompts")
    return _render_page(
        "Промпты TTS", "tts-prompts",
        data, "tts", single_field=False,
    )


@router.get("/clinic-info", response_class=HTMLResponse)
async def clinic_info_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "clinic.info")
    current = str(data.get("text", ""))
    return _render_page("Справка о клинике", "clinic-info", current, "text", single_field=True)


# ── HTML rendering helpers ──────────────────────────────────────────

def _render_login_page() -> str:
    return _base_html(
        title="Вход",
        body="""
        <div class="login-box">
            <h2>uz-stomatolog Admin</h2>
            <a href="/admin/auth/telegram/start" class="tg-btn">Войти через Telegram</a>
        </div>
        """,
    )


def _render_page(
    title: str,
    endpoint: str,
    data: Any,
    preset_key: str,
    single_field: bool,
) -> str:
    fields_html = ""
    init_js = ""

    if single_field:
        text = str(data) if isinstance(data, str) else str(data.get("text", ""))
        escaped = _js_escape(text)
        fields_html = _textarea("text", "Текст", escaped)
        init_js = f'document.getElementById("field-text").value = "{escaped}";'
    else:
        labels = {"ru": "Русский", "uz": "Узбекский", "en": "Английский"}
        for lang, label in labels.items():
            val = str(data.get(lang, "")) if isinstance(data, dict) else ""
            escaped = _js_escape(val)
            fields_html += _textarea(f"lang-{lang}", label, escaped)
            init_js += f'document.getElementById("field-lang-{lang}").value = "{escaped}";'

    body = f"""
    <div class="page">
        <h2>{title}</h2>
        <form id="settings-form" onsubmit="return saveSettings(event)">
            {fields_html}
            <button type="submit" class="save-btn">Сохранить</button>
            <div id="msg" class="msg"></div>
        </form>
    </div>

    <script>
    {init_js}

    async function saveSettings(e) {{
        e.preventDefault();
        const msg = document.getElementById('msg');
        msg.textContent = '';
        let body;
        if ('{_js_escape("true" if single_field else "false")}' === 'true') {{
            body = {{ text: document.getElementById('field-text').value }};
        }} else {{
            body = {{
                ru: document.getElementById('field-lang-ru').value,
                uz: document.getElementById('field-lang-uz').value,
                en: document.getElementById('field-lang-en').value,
            }};
        }}
        try {{
            const resp = await fetch('/admin/api/settings/{endpoint}', {{
                method: 'PUT',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify(body),
            }});
            if (resp.ok) {{
                msg.className = 'msg success';
                msg.textContent = 'Сохранено';
            }} else {{
                msg.className = 'msg error';
                msg.textContent = 'Ошибка: ' + resp.status;
            }}
        }} catch(err) {{
            msg.className = 'msg error';
            msg.textContent = 'Ошибка сети';
        }}
    }}
    </script>
    """
    return _base_html(title=title, body=body, is_page=True)


def _textarea(id: str, label: str, value: str) -> str:
    return f"""
    <div class="field">
        <label for="field-{id}">{label}</label>
        <textarea id="field-{id}" class="ta">{value}</textarea>
    </div>"""


def _base_html(
    title: str = "",
    body: str = "",
    is_page: bool = False,
) -> str:
    nav = ""
    if is_page:
        nav = """
        <nav>
            <a href="/admin/system-prompt">Системный промпт</a>
            <a href="/admin/welcome-messages">Первое сообщение</a>
            <a href="/admin/tts-prompts">Промпты TTS</a>
            <a href="/admin/clinic-info">Справка о клинике</a>
            <form action="/admin/auth/logout" method="POST" style="display:inline">
                <button type="submit" class="logout-btn">Выйти</button>
            </form>
        </nav>
        """
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – uz-stomatolog Admin</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #222; min-height: 100vh; }}
  nav {{ display: flex; gap: 16px; flex-wrap: wrap; background: #1a1a2e; padding: 12px 24px; }}
  nav a {{ color: #e0e0e0; text-decoration: none; font-size: 14px; }}
  nav a:hover {{ color: #fff; }}
  .logout-btn {{ background: #c0392b; color: #fff; border: none; padding: 4px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; }}
  .login-box {{ max-width: 400px; margin: 100px auto; background: #fff; padding: 40px; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,.1); text-align: center; }}
  .login-box h2 {{ margin-bottom: 24px; }}
  .tg-btn {{ display: inline-block; background: #2AABEE; color: #fff; padding: 12px 32px; border-radius: 8px; text-decoration: none; font-size: 16px; font-weight: 600; }}
  .tg-btn:hover {{ background: #229ED9; }}
  .page {{ max-width: 800px; margin: 24px auto; background: #fff; padding: 32px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  .page h2 {{ margin-bottom: 20px; }}
  .field {{ margin-bottom: 16px; }}
  .field label {{ display: block; font-weight: 600; margin-bottom: 6px; font-size: 14px; }}
  .ta {{ width: 100%; min-height: 120px; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; resize: vertical; font-family: inherit; }}
  .save-btn {{ background: #27ae60; color: #fff; border: none; padding: 10px 28px; border-radius: 6px; font-size: 15px; cursor: pointer; }}
  .save-btn:hover {{ background: #219a52; }}
  .msg {{ margin-top: 12px; padding: 8px 12px; border-radius: 4px; font-size: 14px; }}
  .msg.success {{ background: #d4edda; color: #155724; }}
  .msg.error {{ background: #f8d7da; color: #721c24; }}
</style>
</head>
<body>
{nav}
{body}
</body>
</html>"""


def _js_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("</", "<\\/")
    )


def _random_hex(length: int) -> str:
    import secrets
    return secrets.token_hex(length)


async def _get_db_session() -> AsyncSession:
    from app.db.session import async_session_factory
    return async_session_factory()
