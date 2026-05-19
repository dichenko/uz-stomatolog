import logging
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import get_settings
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


async def generate_admin_faq_answer(
    *,
    question: str,
    language: str | None,
    knowledge: str,
) -> FaqAnswer:
    normalized_language = normalize_language(language)
    if _is_medical_advice_request(question):
        return FaqAnswer(
            text=MEDICAL_REFUSALS[normalized_language],
            answered=True,
            source="safety_rules",
        )

    openai_answer = await _try_openai_answer(
        question=question,
        language=normalized_language,
        knowledge=knowledge,
    )
    if openai_answer is not None:
        return FaqAnswer(text=openai_answer, answered=True, source="openai")

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
) -> str | None:
    settings = get_settings()
    if settings.openai_api_key is None:
        return None

    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        return None

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=settings.openai_text_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a dental clinic administrative assistant. "
                        "Answer only using the provided knowledge base. "
                        "If the answer is absent, "
                        "say that an administrator will clarify. "
                        "Never provide medical advice. "
                        f"Answer in {language}."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Knowledge base:\n{knowledge}\n\nQuestion:\n{question}",
                },
            ],
        )
        answer = response.choices[0].message.content
        return answer.strip() if answer else None
    except Exception:
        logger.exception("openai_faq_generation_failed")
        return None


def _detect_topic(question: str) -> str | None:
    normalized = question.casefold()
    for topic, keywords in KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return topic
    return None


def _is_medical_advice_request(question: str) -> bool:
    normalized = question.casefold()
    return any(keyword in normalized for keyword in MEDICAL_KEYWORDS)


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
