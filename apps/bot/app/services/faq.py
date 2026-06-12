import logging
import re
from dataclasses import dataclass

from langsmith import traceable
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_reader import get_clinic_info, get_system_prompt
from app.db.models import Conversation, User
from app.services.llm_context import (
    LlmContext,
    build_llm_context,
    build_openai_context_messages,
)
from app.services.text_llm import ChatMessage, complete_text
from app.telegram.texts import Language, normalize_language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaqAnswer:
    text: str
    answered: bool
    source: str


SECTION_TITLES: dict[Language, dict[str, str]] = {
    "ru": {
        "schedule": "График",
        "prices": "Услуги и цены",
        "doctors": "Врачи",
        "address": "Адрес",
        "contacts": "Контакты",
        "booking": "Запись",
    },
    "uz": {
        "schedule": "Ish vaqti",
        "prices": "Xizmatlar va narxlar",
        "doctors": "Shifokorlar",
        "address": "Manzil",
        "contacts": "Aloqa",
        "booking": "Qabulga yozilish",
    },
    "en": {
        "schedule": "Schedule",
        "prices": "Services and prices",
        "doctors": "Doctors",
        "address": "Address",
        "contacts": "Contacts",
        "booking": "Booking",
    },
}

UNKNOWN_ANSWERS: dict[Language, str] = {
    "ru": (
        "В базе знаний нет точной информации по этому вопросу. "
        "Оставьте номер телефона, и администратор свяжется с вами."
    ),
    "uz": (
        "Bu savol bo'yicha bilimlar bazasida aniq ma'lumot yo'q. "
        "Telefon raqamingizni qoldiring, administrator siz bilan bog'lanadi."
    ),
    "en": (
        "The knowledge base does not contain exact information about this. "
        "Please leave your phone number and an administrator will contact you."
    ),
}

MEDICAL_REFUSALS: dict[Language, str] = {
    "ru": (
        "Я не могу давать медицинские рекомендации, ставить диагноз или советовать "
        "лекарства. Могу помочь записаться на консультацию к стоматологу."
    ),
    "uz": (
        "Men tibbiy maslahat, tashxis yoki dori tavsiya qila olmayman. "
        "Stomatolog konsultatsiyasiga yozilishda yordam bera olaman."
    ),
    "en": (
        "I cannot provide medical advice, diagnose, or recommend medicines. "
        "I can help book a dentist consultation."
    ),
}

KEYWORDS: dict[str, tuple[str, ...]] = {
    "schedule": (
        "график",
        "режим",
        "работ",
        "время",
        "soat",
        "ish",
        "vaqt",
        "schedule",
        "open",
        "hours",
    ),
    "prices": (
        "цен",
        "стоим",
        "прайс",
        "сум",
        "narx",
        "qancha",
        "price",
        "cost",
    ),
    "doctors": (
        "врач",
        "доктор",
        "хирург",
        "терапевт",
        "shifokor",
        "jarroh",
        "doctor",
        "surgeon",
        "therapist",
    ),
    "address": (
        "адрес",
        "где",
        "локац",
        "manzil",
        "qayer",
        "address",
        "where",
        "location",
    ),
    "contacts": (
        "контакт",
        "телефон",
        "связ",
        "aloqa",
        "telefon",
        "contact",
        "phone",
        "call",
    ),
    "booking": (
        "запис",
        "прием",
        "приём",
        "бронь",
        "yozil",
        "qabul",
        "book",
        "appointment",
    ),
}

MEDICAL_KEYWORDS = (
    "что выпить",
    "какое лекарство",
    "антибиотик",
    "обезбол",
    "диагноз",
    "лечить дома",
    "og'riq",
    "dori",
    "antibiotik",
    "painkiller",
    "medicine",
    "antibiotic",
    "diagnose",
)


@traceable(name="generate_admin_faq_answer")
async def generate_admin_faq_answer(
    *,
    question: str,
    language: str | None,
    knowledge: str,
    session: AsyncSession | None = None,
    user: User | None = None,
    conversation: Conversation | None = None,
    input_message_id: int | None = None,
) -> FaqAnswer:
    normalized_language = normalize_language(language)
    llm_context: LlmContext | None = None

    if session is not None and user is not None and conversation is not None:
        try:
            llm_context = await build_llm_context(
                session=session,
                user=user,
                conversation=conversation,
                exclude_message_id=input_message_id,
            )
        except Exception:
            logger.exception("llm_context_build_failed")

    if llm_context is not None and llm_context.clinic_info.strip():
        knowledge = llm_context.clinic_info.strip()
    elif session is not None:
        try:
            clinic_info = await get_clinic_info(session)
        except Exception:
            logger.exception("admin_get_clinic_info_failed")
            clinic_info = ""
        if clinic_info.strip():
            knowledge = clinic_info.strip()

    if _is_medical_advice_request(question):
        return FaqAnswer(
            text=MEDICAL_REFUSALS[normalized_language],
            answered=True,
            source="safety_rules",
        )

    llm_answer = await _try_openai_answer(
        question=question,
        language=normalized_language,
        knowledge=knowledge,
        session=session,
        llm_context=llm_context,
        telegram_user_id=user.telegram_user_id if user is not None else None,
        input_message_id=input_message_id,
    )
    if llm_answer is not None:
        return FaqAnswer(text=llm_answer, answered=True, source="llm")

    topic = _detect_topic(question)
    if topic is None:
        return FaqAnswer(
            text=UNKNOWN_ANSWERS[normalized_language],
            answered=False,
            source="fallback",
        )

    section = _extract_section(knowledge, SECTION_TITLES[normalized_language][topic])
    if section is None:
        return FaqAnswer(
            text=UNKNOWN_ANSWERS[normalized_language],
            answered=False,
            source="fallback",
        )

    return FaqAnswer(
        text=_format_section_answer(section, normalized_language),
        answered=True,
        source="knowledge_base",
    )


async def _try_openai_answer(
    *,
    question: str,
    language: Language,
    knowledge: str,
    session: AsyncSession | None = None,
    llm_context: LlmContext | None = None,
    telegram_user_id: int | None = None,
    input_message_id: int | None = None,
) -> str | None:
    system_prompt = ""
    if session is not None:
        try:
            system_prompt = await get_system_prompt(session)
        except Exception:
            logger.exception("admin_get_system_prompt_failed")

    messages: list[ChatMessage] = [
        {
            "role": "system",
            "content": (
                "You are a dental clinic administrative assistant. "
                "Answer only using the provided knowledge base. "
                "If the answer is absent, "
                "say that an administrator will clarify. "
                "Never provide medical advice."
            ),
        },
    ]

    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})

    messages.append(
        {
            "role": "system",
            "content": _build_language_instruction(language),
        }
    )

    messages.extend(build_openai_context_messages(llm_context))

    messages.append(
        {
            "role": "user",
            "content": f"Knowledge base:\n{knowledge}\n\nQuestion:\n{question}",
        }
    )

    try:
        return await complete_text(
            temperature=0,
            messages=messages,
            session=session,
            request_id=str(input_message_id) if input_message_id else None,
            telegram_user_id=telegram_user_id,
        )
    except Exception:
        logger.exception("faq_llm_generation_failed")
        return None


@traceable(name="detect_topic")
def _detect_topic(question: str) -> str | None:
    normalized = question.casefold()
    for topic, keywords in KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return topic
    return None


def _is_medical_advice_request(question: str) -> bool:
    normalized = question.casefold()
    return any(keyword in normalized for keyword in MEDICAL_KEYWORDS)


@traceable(name="extract_section")
def _extract_section(knowledge: str, title: str) -> str | None:
    pattern = rf"^##\s+{re.escape(title)}\s*$"
    match = re.search(pattern, knowledge, flags=re.MULTILINE)
    if match is None:
        return None

    start = match.end()
    next_match = re.search(r"^##\s+", knowledge[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(knowledge)
    section = knowledge[start:end].strip()
    return section or None


def _format_section_answer(section: str, language: Language) -> str:
    normalized = re.sub(r"\n{3,}", "\n\n", section).strip()
    if language == "ru":
        return normalized
    if language == "uz":
        return normalized
    return normalized


LANGUAGE_NAMES: dict[Language, str] = {
    "ru": "русском",
    "uz": "узбекском",
    "en": "английском",
}


def _build_language_instruction(language: Language) -> str:
    return (
        f"Пользователь выбрал язык: {language}. "
        f"Отвечай пользователю строго на выбранном языке. "
        f"Если выбран ru — отвечай на русском. "
        f"Если выбран uz — отвечай на узбекском. "
        f"Если выбран en — отвечай на английском. "
        "Не меняй язык ответа, если пользователь явно не попросил изменить язык."
    )
