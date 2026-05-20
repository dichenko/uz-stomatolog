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
from app.admin.history_repository import (
    HistoryFilters,
    HistoryQuery,
    MessageHistoryRepository,
    parse_date,
    parse_time,
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

    cookie_header = request.headers.get("cookie", "(none)")
    logger.info(
        "admin_callback_diag",
        extra={
            "cookie_header": cookie_header,
            "session_keys": list(request.session.keys()),
            "query_state": state,
            "query_code_len": len(code),
        },
    )

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
        logger.info(
            "admin_login_check",
            extra={
                "tg_id": tg_id,
                "admin_ids": settings.telegram_admin_ids or "(empty)",
            },
        )

        async with _get_db_session() as session:
            await log_audit(
                session,
                admin_tg_id=tg_id,
                action="login_attempt",
                ip_address=request.client.host if request.client else None,
            )
            await session.commit()

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
    except AuthError as exc:
        logger.warning("admin_auth_error", extra={"error": str(exc)})
        return HTMLResponse(
            f"<h1>Authentication failed</h1><p>{exc}</p>", status_code=403
        )
    except Exception:
        logger.exception("admin_callback_unexpected_error")
        return HTMLResponse(
            "<h1>Internal server error</h1><p>Please try again later.</p>",
            status_code=500,
        )


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


@router.get("/api/history")
async def api_history(request: Request):
    await _require_admin(request)
    params = request.query_params
    order_column = _parse_int(params.get("order[0][column]"), default=0)
    sort_field = params.get(f"columns[{order_column}][data]", "date")
    sort_dir = params.get("order[0][dir]", "desc")
    query = HistoryQuery(
        start=_parse_int(params.get("start"), default=0),
        length=_parse_int(params.get("length"), default=20),
        sort_field=_normalize_history_sort_field(sort_field),
        sort_dir="asc" if sort_dir == "asc" else "desc",
        filters=HistoryFilters(
            date_from=parse_date(params.get("filter_date_from")),
            date_to=parse_date(params.get("filter_date_to")),
            time_from=parse_time(params.get("filter_time_from")),
            time_to=parse_time(params.get("filter_time_to")),
            tg_id=str(params.get("filter_tg_id") or "").strip(),
            user_text=str(params.get("filter_user_text") or "").strip(),
            llm_answer_text=str(params.get("filter_llm_answer_text") or "").strip(),
            language=str(params.get("filter_language") or "").strip(),
            message_type=str(params.get("filter_message_type") or "").strip(),
            agent_action=str(params.get("filter_agent_action") or "").strip(),
            global_search=str(params.get("search[value]") or "").strip(),
        ),
    )
    async with _get_db_session() as session:
        page = await MessageHistoryRepository(session).fetch_page(query)
    return {
        "draw": _parse_int(params.get("draw"), default=0),
        "recordsTotal": page.total,
        "recordsFiltered": page.filtered,
        "data": [row.as_dict() for row in page.rows],
    }


@router.put("/api/settings/system-prompt")
async def api_save_system_prompt(request: Request):
    tg_id = await _require_admin(request)
    body = await request.json()
    text = str(body.get("text", ""))[:80000]
    async with _get_db_session() as session:
        old = await get_setting(session, "llm.system_prompt")
        await set_setting(session, "llm.system_prompt", {"text": text}, tg_id)
        await log_audit(
            session,
            admin_tg_id=tg_id,
            action="update_setting",
            setting_key="llm.system_prompt",
            old_value=old,
            new_value={"text": text},
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
            session,
            admin_tg_id=tg_id,
            action="update_setting",
            setting_key="bot.welcome_messages",
            old_value=old,
            new_value=value,
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
            session,
            admin_tg_id=tg_id,
            action="update_setting",
            setting_key="tts.prompts",
            old_value=old,
            new_value=value,
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
            session,
            admin_tg_id=tg_id,
            action="update_setting",
            setting_key="clinic.info",
            old_value=old,
            new_value={"text": text},
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
    return _render_page(
        "Системный промпт", "system-prompt", current, "text", single_field=True
    )


@router.get("/welcome-messages", response_class=HTMLResponse)
async def welcome_messages_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "bot.welcome_messages")
    return _render_page(
        "Первое сообщение",
        "welcome-messages",
        data,
        "welcome",
        single_field=False,
    )


@router.get("/tts-prompts", response_class=HTMLResponse)
async def tts_prompts_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "tts.prompts")
    return _render_page(
        "Промпты TTS",
        "tts-prompts",
        data,
        "tts",
        single_field=False,
    )


@router.get("/clinic-info", response_class=HTMLResponse)
async def clinic_info_page(request: Request):
    await _require_admin(request)
    async with _get_db_session() as session:
        data = await get_setting(session, "clinic.info")
    current = str(data.get("text", ""))
    return _render_page(
        "Справка о клинике", "clinic-info", current, "text", single_field=True
    )


# ── HTML rendering helpers ──────────────────────────────────────────


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    await _require_admin(request)
    return _render_history_page()


def _render_login_page() -> str:
    return _base_html(
        title="Вход",
        body="""
        <main class="content">
            <div class="card login-card">
                <h2>uz-stomatolog Admin</h2>
                <a href="/admin/auth/telegram/start" class="tg-btn">Войти через Telegram</a>
            </div>
        </main>
        """,
    )


def _render_history_page() -> str:
    body = """
    <main class="content content-history">
        <section class="history-panel">
            <div class="history-header">
                <h2>История</h2>
                <button type="button" id="clear-filters" class="btn-secondary">
                    Сбросить фильтры
                </button>
            </div>
            <table id="history-table" class="display compact stripe" style="width:100%">
                <thead>
                    <tr>
                        <th>Дата</th>
                        <th>Время</th>
                        <th>TG ID</th>
                        <th>Текст пользователя</th>
                        <th>Ответ LLM</th>
                        <th>Язык</th>
                        <th>Тип</th>
                        <th>Действие агента</th>
                    </tr>
                    <tr class="filters">
                        <th>
                            <input id="filter-date-from" type="date" aria-label="Дата с">
                            <input id="filter-date-to" type="date" aria-label="Дата по">
                        </th>
                        <th>
                            <input id="filter-time-from" type="time" aria-label="Время с">
                            <input id="filter-time-to" type="time" aria-label="Время по">
                        </th>
                        <th><input id="filter-tg-id" type="search" placeholder="TG ID"></th>
                        <th><input id="filter-user-text" type="search" placeholder="Поиск"></th>
                        <th><input id="filter-llm-answer" type="search" placeholder="Поиск"></th>
                        <th>
                            <select id="filter-language">
                                <option value="">Все</option>
                                <option value="ru">ru</option>
                                <option value="uz">uz</option>
                                <option value="en">en</option>
                            </select>
                        </th>
                        <th>
                            <select id="filter-message-type">
                                <option value="">Все</option>
                                <option value="text">text</option>
                                <option value="voice">voice</option>
                                <option value="callback">callback</option>
                                <option value="system">system</option>
                            </select>
                        </th>
                        <th>
                            <select id="filter-agent-action">
                                <option value="">Все</option>
                                <option value="Создана запись">Создана запись</option>
                                <option value="Отменена запись">Отменена запись</option>
                                <option value="Перенесена запись">Перенесена запись</option>
                                <option value="Эскалация админам">Эскалация админам</option>
                                <option value="Оповещение группы админов">Оповещение группы админов</option>
                            </select>
                        </th>
                    </tr>
                </thead>
            </table>
        </section>
    </main>

    <script>
    const reloadHistory = (() => {
        let timeoutId;
        return (table) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => table.ajax.reload(), 250);
        };
    })();

    $(function () {
        const table = $('#history-table').DataTable({
            serverSide: true,
            processing: true,
            searching: true,
            orderCellsTop: true,
            scrollX: true,
            pageLength: 20,
            lengthMenu: [[20, 50, 100], [20, 50, 100]],
            order: [[0, 'desc']],
            ajax: {
                url: '/admin/api/history',
                data: function (d) {
                    d.filter_date_from = $('#filter-date-from').val();
                    d.filter_date_to = $('#filter-date-to').val();
                    d.filter_time_from = $('#filter-time-from').val();
                    d.filter_time_to = $('#filter-time-to').val();
                    d.filter_tg_id = $('#filter-tg-id').val();
                    d.filter_user_text = $('#filter-user-text').val();
                    d.filter_llm_answer_text = $('#filter-llm-answer').val();
                    d.filter_language = $('#filter-language').val();
                    d.filter_message_type = $('#filter-message-type').val();
                    d.filter_agent_action = $('#filter-agent-action').val();
                }
            },
            columns: [
                {data: 'date'},
                {data: 'time'},
                {data: 'tg_id'},
                {data: 'user_text', className: 'history-text'},
                {data: 'llm_answer_text', className: 'history-text'},
                {data: 'language'},
                {data: 'message_type'},
                {data: 'agent_action', className: 'history-action'}
            ],
            language: {
                search: 'Общий поиск:',
                lengthMenu: 'Показывать _MENU_ строк',
                info: 'Строки _START_-_END_ из _TOTAL_',
                infoEmpty: 'Нет строк',
                infoFiltered: '(отфильтровано из _MAX_)',
                zeroRecords: 'Ничего не найдено',
                processing: 'Загрузка...',
                paginate: {
                    first: 'Первая',
                    last: 'Последняя',
                    next: 'Следующая',
                    previous: 'Предыдущая'
                }
            }
        });

        $('.filters input, .filters select').on('input change', function () {
            reloadHistory(table);
        });
        $('.filters input, .filters select').on('click keydown', function (event) {
            event.stopPropagation();
        });
        $('#clear-filters').on('click', function () {
            $('.filters input').val('');
            $('.filters select').val('');
            table.search('');
            table.ajax.reload();
        });
    });
    </script>
    """
    return _base_html(
        title="История",
        body=body,
        is_page=True,
        extra_head="""
        <link rel="stylesheet" href="https://cdn.datatables.net/1.13.8/css/jquery.dataTables.min.css">
        <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
        <script src="https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js"></script>
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
        fields_html = f'<div class="field-group field-fill">{_textarea_content("text", "Текст", escaped, "ta-large")}</div>'
        init_js = f'document.getElementById("field-text").value = "{escaped}";'
        card_class = "card card-fill"
        form_class = "form-fill"
        content_class = "content content-wide"
    else:
        labels = {"ru": "Русский", "uz": "Узбекский", "en": "Английский"}
        for lang, label in labels.items():
            val = str(data.get(lang, "")) if isinstance(data, dict) else ""
            escaped = _js_escape(val)
            fields_html += f'<div class="field-group">{_textarea_content(f"lang-{lang}", label, escaped, "ta-multi")}</div>'
            init_js += (
                f'document.getElementById("field-lang-{lang}").value = "{escaped}";'
            )
        card_class = "card"
        form_class = ""
        content_class = "content"

    body = f"""
    <main class="{content_class}">
        <div class="{card_class}">
            <h2>{title}</h2>
            <form id="settings-form" onsubmit="return saveSettings(event)"{(' class="' + form_class + '"' if form_class else "")}>
                {fields_html}
                <button type="submit" class="btn-save">Сохранить</button>
                <div id="msg" class="msg"></div>
            </form>
        </div>
    </main>

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
                msg.className = 'msg toast toast-success';
                msg.textContent = 'Сохранено';
            }} else {{
                msg.className = 'msg toast toast-error';
                msg.textContent = 'Ошибка: ' + resp.status;
            }}
        }} catch(err) {{
            msg.className = 'msg toast toast-error';
            msg.textContent = 'Ошибка сети';
        }}
    }}
    </script>
    """
    return _base_html(title=title, body=body, is_page=True)


def _textarea_content(id: str, label: str, value: str, css_class: str = "ta") -> str:
    return f"""
        <label for="field-{id}">{label}</label>
        <textarea id="field-{id}" class="{css_class}">{value}</textarea>"""


def _base_html(
    title: str = "",
    body: str = "",
    is_page: bool = False,
    extra_head: str = "",
) -> str:
    nav = ""
    if is_page:
        nav = """
        <nav>
            <span class="nav-brand">Admin</span>
            <a href="/admin/system-prompt">Системный промпт</a>
            <a href="/admin/welcome-messages">Первое сообщение</a>
            <a href="/admin/tts-prompts">Промпты TTS</a>
            <a href="/admin/clinic-info">Справка о клинике</a>
            <a href="/admin/history">История</a>
            <span class="nav-spacer"></span>
            <form action="/admin/auth/logout" method="POST">
                <button type="submit" class="btn-logout">Выйти</button>
            </form>
        </nav>
        """
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – uz-stomatolog Admin</title>
{extra_head}
<style>
  :root {{
    --bg: #f0f2f5;
    --card-bg: #fff;
    --text: #1a1a2e;
    --text-secondary: #6b7280;
    --border: #d1d5db;
    --accent: #2AABEE;
    --accent-hover: #229ED9;
    --success: #27ae60;
    --danger: #c0392b;
    --nav-bg: #1a1a2e;
    --radius: 12px;
    --radius-sm: 8px;
    --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06);
    --shadow-lg: 0 4px 16px rgba(0,0,0,0.12);
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    -webkit-font-smoothing: antialiased;
  }}

  nav {{
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
    align-items: center;
    background: var(--nav-bg);
    padding: 0 32px;
    height: 52px;
    flex-shrink: 0;
  }}
  nav a {{
    color: #c4c6d0;
    text-decoration: none;
    font-size: 13px;
    padding: 8px 14px;
    border-radius: 6px;
    transition: background 0.15s, color 0.15s;
  }}
  nav a:hover {{ background: rgba(255,255,255,0.08); color: #fff; }}
  .nav-brand {{
    color: #fff;
    font-weight: 700;
    font-size: 15px;
    margin-right: 16px;
    letter-spacing: -0.3px;
  }}
  .nav-spacer {{ flex: 1; }}
  .btn-logout {{
    background: transparent;
    color: #e88;
    border: 1px solid rgba(238,136,136,0.3);
    padding: 6px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    transition: background 0.15s;
  }}
  .btn-logout:hover {{ background: rgba(238,136,136,0.12); }}

  .content {{
    flex: 1;
    width: 100%;
    max-width: 800px;
    margin: 0 auto;
    padding: 32px 24px;
    display: flex;
    flex-direction: column;
  }}
  .content-wide {{ max-width: 86vw; }}

  .card {{
    background: var(--card-bg);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 36px 40px;
  }}
  .card-fill {{
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  .card h2 {{
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 28px;
    color: var(--text);
    letter-spacing: -0.3px;
  }}

  .login-card {{
    max-width: 400px;
    margin: auto;
    text-align: center;
    padding: 48px 40px;
  }}
  .tg-btn {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: var(--accent);
    color: #fff;
    padding: 12px 36px;
    border-radius: var(--radius-sm);
    text-decoration: none;
    font-size: 15px;
    font-weight: 600;
    transition: background 0.15s, transform 0.1s;
    gap: 8px;
  }}
  .tg-btn:hover {{ background: var(--accent-hover); transform: translateY(-1px); }}
  .tg-btn:active {{ transform: translateY(0); }}

  .form-fill {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }}

  .field-group {{
    margin-bottom: 20px;
  }}
  .field-group:last-of-type {{ margin-bottom: 20px; }}
  .field-fill {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
    margin-bottom: 20px;
  }}
  .field-group label {{
    display: block;
    font-weight: 600;
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}

  textarea {{
    width: 100%;
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-size: 14px;
    font-family: inherit;
    line-height: 1.65;
    resize: vertical;
    transition: border-color 0.15s, box-shadow 0.15s;
    color: var(--text);
    background: #fafbfc;
  }}
  textarea:focus {{
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(42,171,238,0.14);
    background: #fff;
  }}
  .ta-large {{
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    resize: vertical;
  }}
  .ta-multi {{
    min-height: 240px;
  }}

  .btn-save {{
    background: var(--success);
    color: #fff;
    border: none;
    padding: 11px 32px;
    border-radius: var(--radius-sm);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
    align-self: flex-start;
    flex-shrink: 0;
  }}
  .btn-save:hover {{ background: #219a52; transform: translateY(-1px); }}
  .btn-save:active {{ transform: translateY(0); }}

  .msg {{ margin-top: 14px; flex-shrink: 0; }}
  .toast {{
    padding: 10px 16px;
    border-radius: var(--radius-sm);
    font-size: 13px;
    font-weight: 500;
  }}
  .toast-success {{ background: #d1fae5; color: #065f46; }}
  .toast-error {{ background: #fee2e2; color: #991b1b; }}

  .content-history {{
    max-width: 96vw;
  }}
  .history-panel {{
    background: var(--card-bg);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 24px;
    overflow: hidden;
  }}
  .history-header {{
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: center;
    margin-bottom: 18px;
  }}
  .history-header h2 {{
    font-size: 20px;
    margin: 0;
  }}
  .btn-secondary {{
    border: 1px solid var(--border);
    background: #fff;
    color: var(--text);
    padding: 8px 14px;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-size: 13px;
  }}
  .btn-secondary:hover {{
    background: #f8fafc;
  }}
  #history-table th,
  #history-table td {{
    vertical-align: top;
  }}
  #history-table .filters th {{
    padding: 6px 8px;
  }}
  #history-table .filters input,
  #history-table .filters select {{
    width: 100%;
    min-width: 110px;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
  }}
  #history-table .filters input + input {{
    margin-top: 6px;
  }}
  #history-table .history-text {{
    min-width: 280px;
    max-width: 520px;
    white-space: pre-wrap;
  }}
  #history-table .history-action {{
    min-width: 220px;
  }}
  div.dataTables_wrapper {{
    font-size: 13px;
  }}

  @media (max-width: 640px) {{
    nav {{ padding: 0 16px; gap: 2px; }}
    nav a {{ font-size: 12px; padding: 6px 10px; }}
    .nav-brand {{ margin-right: 8px; }}
    .content {{ padding: 20px 12px; }}
    .content-wide {{ max-width: 100%; }}
    .card {{ padding: 24px 20px; }}
  }}
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


def _parse_int(value: str | None, *, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _normalize_history_sort_field(value: str):
    allowed = {
        "date",
        "time",
        "tg_id",
        "user_text",
        "llm_answer_text",
        "language",
        "message_type",
        "agent_action",
    }
    return value if value in allowed else "date"


def _random_hex(length: int) -> str:
    import secrets

    return secrets.token_hex(length)


def _get_db_session() -> AsyncSession:
    from app.db.session import async_session_factory

    return async_session_factory()
