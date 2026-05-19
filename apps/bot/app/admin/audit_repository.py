import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAuditLog

logger = logging.getLogger(__name__)


async def log_audit(
    session: AsyncSession,
    *,
    admin_tg_id: str,
    action: str,
    setting_key: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    entry = AdminAuditLog(
        admin_tg_id=admin_tg_id,
        action=action,
        setting_key=setting_key,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    await session.flush()
