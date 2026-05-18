from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import ClinicKnowledgeRepository
from app.telegram.texts import Language, normalize_language

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "clinic_knowledge"


async def load_clinic_knowledge_if_empty(
    session: AsyncSession,
    knowledge_dir: Path = KNOWLEDGE_DIR,
) -> int:
    repository = ClinicKnowledgeRepository(session)
    if await repository.count() > 0:
        return 0

    loaded = 0
    for language in ("ru", "uz", "en"):
        path = knowledge_dir / f"{language}.md"
        content = path.read_text(encoding="utf-8")
        await repository.create(language=language, content=content)
        loaded += 1
    return loaded


async def get_clinic_knowledge(
    session: AsyncSession,
    language: str | None,
) -> str:
    normalized_language: Language = normalize_language(language)
    repository = ClinicKnowledgeRepository(session)
    knowledge = await repository.get_active_by_language(normalized_language)
    if knowledge is not None:
        return knowledge.content

    fallback = await repository.get_active_by_language("ru")
    return fallback.content if fallback is not None else ""
