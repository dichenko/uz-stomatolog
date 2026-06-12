from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.telegram.handlers_callbacks import router as callbacks_router
from app.telegram.handlers_messages import router as messages_router
from app.telegram.handlers_start import router as start_router
from app.telegram.persistence import PersistenceMiddleware
from app.telegram.typing_action import TypingActionMiddleware


def create_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dispatcher = Dispatcher()
    persistence = PersistenceMiddleware(session_factory)
    dispatcher.message.middleware(TypingActionMiddleware())
    dispatcher.message.middleware(persistence)
    dispatcher.callback_query.middleware(persistence)
    dispatcher.include_router(start_router)
    dispatcher.include_router(callbacks_router)
    dispatcher.include_router(messages_router)
    return dispatcher
