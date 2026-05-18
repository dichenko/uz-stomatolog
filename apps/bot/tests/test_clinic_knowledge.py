from app.services.clinic_knowledge import (
    get_clinic_knowledge,
    load_clinic_knowledge_if_empty,
)
from app.services.faq import generate_admin_faq_answer


async def test_load_clinic_knowledge_if_empty_loads_three_languages(session):
    loaded_count = await load_clinic_knowledge_if_empty(session)
    second_loaded_count = await load_clinic_knowledge_if_empty(session)

    ru_knowledge = await get_clinic_knowledge(session, "ru")
    uz_knowledge = await get_clinic_knowledge(session, "uz")
    en_knowledge = await get_clinic_knowledge(session, "en")

    assert loaded_count == 3
    assert second_loaded_count == 0
    assert "09:00" in ru_knowledge
    assert "09:00" in uz_knowledge
    assert "09:00" in en_knowledge


async def test_faq_answers_schedule_from_knowledge_base(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    await load_clinic_knowledge_if_empty(session)
    knowledge = await get_clinic_knowledge(session, "ru")
    answer = await generate_admin_faq_answer(
        question="Какой у вас график работы?",
        language="ru",
        knowledge=knowledge,
    )

    assert answer.answered is True
    assert answer.source == "knowledge_base"
    assert "09:00" in answer.text
    assert "21:00" in answer.text


async def test_faq_answers_prices_from_knowledge_base(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    await load_clinic_knowledge_if_empty(session)
    knowledge = await get_clinic_knowledge(session, "en")
    answer = await generate_admin_faq_answer(
        question="How much does cleaning cost?",
        language="en",
        knowledge=knowledge,
    )

    assert answer.answered is True
    assert "350,000 UZS" in answer.text


async def test_faq_unknown_question_does_not_hallucinate(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    await load_clinic_knowledge_if_empty(session)
    knowledge = await get_clinic_knowledge(session, "uz")
    answer = await generate_admin_faq_answer(
        question="Do you sell toothbrush subscriptions?",
        language="uz",
        knowledge=knowledge,
    )

    assert answer.answered is False
    assert answer.source == "fallback"
    assert "aniq ma'lumot yo'q" in answer.text


async def test_faq_refuses_medical_advice(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    answer = await generate_admin_faq_answer(
        question="У меня болит зуб, что выпить?",
        language="ru",
        knowledge="",
    )

    assert answer.answered is True
    assert answer.source == "safety_rules"
    assert "лекарства" in answer.text
