from urllib.parse import parse_qs, urlparse

import pytest

from app.admin.one_time_links import (
    AdminOneTimeLinkError,
    consume_admin_one_time_login_token,
    create_admin_one_time_login_link,
    validate_admin_one_time_login_token,
)
from app.config import Settings
from app.db.models import User


async def test_admin_one_time_login_token_is_single_use(session):
    settings = Settings(
        app_base_url="https://bot.example.com",
        session_secret="test-session-secret",
    )
    user = User(
        telegram_user_id=12345,
        telegram_username="admin",
        telegram_first_name="Admin",
        telegram_last_name="User",
    )
    session.add(user)
    await session.flush()

    link = await create_admin_one_time_login_link(
        session,
        user=user,
        app_base_url=settings.app_base_url,
        settings=settings,
    )
    token = parse_qs(urlparse(link).query)["token"][0]

    preview_payload = await validate_admin_one_time_login_token(
        session,
        token=token,
        settings=settings,
    )
    assert preview_payload["tg_id"] == "12345"

    second_preview_payload = await validate_admin_one_time_login_token(
        session,
        token=token,
        settings=settings,
    )
    assert second_preview_payload["tg_id"] == "12345"

    payload = await consume_admin_one_time_login_token(
        session,
        token=token,
        settings=settings,
    )

    assert payload == {
        "tg_id": "12345",
        "username": "admin",
        "name": "Admin User",
    }

    with pytest.raises(AdminOneTimeLinkError):
        await validate_admin_one_time_login_token(
            session,
            token=token,
            settings=settings,
        )

    with pytest.raises(AdminOneTimeLinkError):
        await consume_admin_one_time_login_token(
            session,
            token=token,
            settings=settings,
        )
