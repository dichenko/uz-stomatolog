import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import FSInputFile, Message
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_reader import get_system_prompt, get_tts_prompt
from app.agent import run_agent
from app.config import get_settings
from app.db.models import Conversation, User
from app.db.models import Message as DbMessage
from app.db.repositories import MessageRepository
from app.graph import GraphResult
from app.graph.state import InputType
from app.services.admin_notify import notify_dev_admin
from app.speech import create_speech_providers
from app.speech.base import SpeechProviderError
from app.speech.temp_files import (
    AudioValidationError,
    cleanup_temp_file,
    create_temp_audio_path,
    validate_file_size,
)
from app.telegram.keyboards import (
    booking_slots_keyboard,
    cancel_appointments_keyboard,
    contact_request_keyboard,
    language_keyboard,
    reschedule_appointments_keyboard,
)
from app.telegram.persistence import save_outgoing_message
from app.telegram.texts import normalize_language, text

router = Router(name="messages")
logger = logging.getLogger(__name__)


@router.message(F.text)
async def fallback_text_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    db_incoming_message: DbMessage,
    trace_id: str,
) -> None:
    if db_user.preferred_language is None:
        response_text = text("language_required")
        sent = await message.answer(response_text, reply_markup=language_keyboard())
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=response_text,
            language=None,
            trace_id=trace_id,
            raw_payload={"reply_markup": "language_keyboard"},
        )
        return

    language = normalize_language(db_user.preferred_language)
    graph_result = await _run_graph_for_message(
        db_session=db_session,
        db_user=db_user,
        db_conversation=db_conversation,
        db_incoming_message=db_incoming_message,
        message=message,
        trace_id=trace_id,
        input_text=message.text or "",
        input_type="text",
        language=language,
    )
    response_text = graph_result.final_response_text
    if not isinstance(response_text, str) or not response_text.strip():
        response_text = "Извините, произошла ошибка. Попробуйте ещё раз."
    reply_markup = _reply_markup_for_graph_result(graph_result, language)
    sent = await message.answer(response_text, reply_markup=reply_markup)
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
        raw_payload={
            "intent": graph_result.intent,
            "safety_status": graph_result.safety_status,
            **graph_result.metadata,
        },
    )


@router.message(F.contact)
async def contact_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    db_incoming_message: DbMessage,
    trace_id: str,
) -> None:
    if db_user.preferred_language is None:
        response_text = text("language_required")
        sent = await message.answer(response_text, reply_markup=language_keyboard())
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=response_text,
            language=None,
            trace_id=trace_id,
            raw_payload={"reply_markup": "language_keyboard"},
        )
        return

    language = normalize_language(db_user.preferred_language)
    phone_number = message.contact.phone_number if message.contact else ""
    graph_result = await _run_graph_for_message(
        db_session=db_session,
        db_user=db_user,
        db_conversation=db_conversation,
        db_incoming_message=db_incoming_message,
        message=message,
        trace_id=trace_id,
        input_text=phone_number,
        input_type="text",
        language=language,
    )
    response_text = graph_result.final_response_text
    if not isinstance(response_text, str) or not response_text.strip():
        response_text = "Извините, произошла ошибка. Попробуйте ещё раз."
    reply_markup = _reply_markup_for_graph_result(graph_result, language)
    sent = await message.answer(
        response_text,
        reply_markup=reply_markup,
    )
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=graph_result.final_response_text,
        language=language,
        trace_id=trace_id,
        raw_payload={
            "input_type": "contact",
            "intent": graph_result.intent,
            "safety_status": graph_result.safety_status,
            **graph_result.metadata,
        },
    )


@router.message(F.voice)
async def voice_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    db_incoming_message: DbMessage,
    trace_id: str,
) -> None:
    if db_user.preferred_language is None:
        response_text = text("language_required")
        sent = await message.answer(response_text, reply_markup=language_keyboard())
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=response_text,
            language=None,
            trace_id=trace_id,
            raw_payload={"reply_markup": "language_keyboard"},
        )
        return

    settings = get_settings()
    language = normalize_language(db_user.preferred_language)
    if message.voice is None:
        return

    if message.voice.duration > settings.aisha_stt_max_audio_duration_sec:
        await _send_and_save_text(
            message=message,
            db_session=db_session,
            db_user=db_user,
            db_conversation=db_conversation,
            trace_id=trace_id,
            response_text=text("voice_too_long", language),
            language=language,
            raw_payload={"voice_duration_sec": message.voice.duration},
        )
        return

    input_path = create_temp_audio_path(suffix=".ogg")
    output_path: str | None = None
    tts_original_path: str | None = None
    try:
        telegram_file = await message.bot.get_file(message.voice.file_id)
        if telegram_file.file_path is None:
            raise SpeechProviderError("Telegram voice file path is empty")
        await message.bot.download_file(telegram_file.file_path, destination=input_path)
        max_size_mb = (
            settings.aisha_stt_max_audio_size_mb
            if language == "uz"
            else settings.openai_stt_max_audio_size_mb
        )
        validate_file_size(input_path, max_size_mb=max_size_mb)

        providers = create_speech_providers(settings)
        stt_result = await providers.stt_for_language(language).transcribe(
            str(input_path),
            language,
        )
        if not stt_result.text:
            raise SpeechProviderError("Speech provider returned empty transcription")

        logger.info(
            "voice_transcription",
            extra={
                "trace_id": trace_id,
                "language": language,
                "provider": stt_result.provider,
                "text": stt_result.text,
            },
        )

        transcribed_message = await MessageRepository(db_session).save_message(
            user_id=db_user.id,
            conversation_id=db_conversation.id,
            telegram_message_id=message.message_id,
            direction="in",
            message_type="voice",
            language=language,
            text=stt_result.text,
            raw_payload={
                "transcribed": True,
                "stt_provider": stt_result.provider,
                "stt_model": stt_result.model,
            },
            trace_id=trace_id,
        )

        graph_result = await _run_graph_for_message(
            db_session=db_session,
            db_user=db_user,
            db_conversation=db_conversation,
            db_incoming_message=transcribed_message or db_incoming_message,
            message=message,
            trace_id=trace_id,
            input_text=stt_result.text,
            input_type="voice",
            language=language,
        )
        response_text = graph_result.final_response_text
        if not isinstance(response_text, str) or not response_text.strip():
            response_text = "Извините, произошла ошибка. Попробуйте ещё раз."
        reply_markup = _reply_markup_for_graph_result(graph_result, language)
        sent = await message.answer(response_text, reply_markup=reply_markup)
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=response_text,
            language=language,
            trace_id=trace_id,
            raw_payload={
                "input_type": "voice",
                "intent": graph_result.intent,
                "safety_status": graph_result.safety_status,
                "stt_provider": stt_result.provider,
                "stt_model": stt_result.model,
                **graph_result.metadata,
            },
        )

        try:
            tts_instructions = ""
            try:
                tts_instructions = await get_tts_prompt(db_session, language)
            except Exception:
                pass

            tts_result = await providers.tts_for_language(language).synthesize(
                response_text,
                language,
                instructions=tts_instructions.strip() or None,
            )
            tts_original_path = tts_result.file_path
            voice_path = await _ensure_ogg(tts_original_path)
            output_path = str(voice_path)
            audio = FSInputFile(output_path, filename="voice.ogg")
            sent_audio = await message.answer_voice(audio)
            await save_outgoing_message(
                session=db_session,
                user=db_user,
                conversation=db_conversation,
                telegram_message_id=sent_audio.message_id,
                text=response_text,
                language=language,
                trace_id=trace_id,
                message_type="voice",
                raw_payload={
                    "tts_provider": tts_result.provider,
                    "tts_model": tts_result.model,
                    "tts_format": tts_result.format,
                    "tts_mime_type": tts_result.mime_type,
                    "voice": tts_result.voice,
                },
            )
        except Exception:
            logger.exception(
                "voice_tts_generation_failed",
                extra={"trace_id": trace_id, "language": language},
            )
            await notify_dev_admin(
                bot=message.bot,
                error="Voice TTS generation failed",
                trace_id=trace_id,
                user_info=f"user={db_user.telegram_user_id}",
            )
            await _send_and_save_text(
                message=message,
                db_session=db_session,
                db_user=db_user,
                db_conversation=db_conversation,
                trace_id=trace_id,
                response_text=text("voice_tts_failed", language),
                language=language,
                raw_payload={"input_type": "voice", "tts_failed": True},
            )

    except AudioValidationError:
        await _send_and_save_text(
            message=message,
            db_session=db_session,
            db_user=db_user,
            db_conversation=db_conversation,
            trace_id=trace_id,
            response_text=text("voice_too_large", language),
            language=language,
            raw_payload={"input_type": "voice"},
        )
    except Exception:
        logger.exception(
            "voice_transcription_failed",
            extra={"trace_id": trace_id, "language": language},
        )
        await notify_dev_admin(
            bot=message.bot,
            error="Voice transcription failed",
            trace_id=trace_id,
            user_info=f"user={db_user.telegram_user_id}",
        )
        await _send_and_save_text(
            message=message,
            db_session=db_session,
            db_user=db_user,
            db_conversation=db_conversation,
            trace_id=trace_id,
            response_text=text("voice_transcription_failed", language),
            language=language,
            raw_payload={"input_type": "voice"},
        )
    finally:
        await cleanup_temp_file(input_path, reason="telegram_voice_input_cleanup")
        await cleanup_temp_file(output_path, reason="telegram_voice_output_cleanup")
        await cleanup_temp_file(
            tts_original_path, reason="telegram_tts_original_cleanup"
        )


async def _run_graph_for_message(
    *,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    db_incoming_message: DbMessage,
    message: Message,
    trace_id: str,
    input_text: str,
    input_type: InputType,
    language: str,
) -> GraphResult:
    try:
        config = {
            "configurable": {
                "session": db_session,
                "user": db_user,
                "conversation": db_conversation,
                "language": language,
                "admin_bot": message.bot,
                "thread_id": str(message.chat.id),
            }
        }

        chat_history = None
        recent = await MessageRepository(db_session).get_recent_for_conversation(
            conversation_id=db_conversation.id,
            limit=15,
            exclude_message_id=db_incoming_message.id,
        )
        if recent:
            chat_history = []
            for m in recent:
                if not m.text:
                    continue
                if m.direction == "in":
                    chat_history.append(HumanMessage(content=m.text))
                elif m.direction == "out":
                    chat_history.append(AIMessage(content=m.text))
            if chat_history:
                logger.info(
                    "injecting_chat_history",
                    extra={"trace_id": trace_id, "history_messages": len(chat_history)},
                )

        response_text = await run_agent(
            input_text=input_text,
            config=config,
            chat_history=chat_history,
            system_prompt=await get_system_prompt(db_session),
        )
        logger.info(
            "agent_execution",
            extra={
                "trace_id": trace_id,
                "input": input_text,
                "output": response_text,
            },
        )
        return GraphResult(
            final_response_text=response_text,
            intent=None,
            safety_status=None,
            should_generate_voice=input_type == "voice",
            should_escalate=False,
            proposed_slots=[],
            metadata={},
        )
    except Exception:
        logger.exception("agent_failed", extra={"trace_id": trace_id})
        user_info = (
            f"user={db_user.telegram_user_id}"
            f" (@{getattr(db_user, 'telegram_username', '-') or '-'})"
        )
        await notify_dev_admin(
            bot=message.bot,
            error="Agent execution failed",
            trace_id=trace_id,
            user_info=user_info,
        )
        return GraphResult(
            final_response_text="Извините, произошла ошибка. Попробуйте ещё раз.",
            intent=None,
            safety_status=None,
            should_generate_voice=input_type == "voice",
            should_escalate=False,
            proposed_slots=[],
            metadata={},
        )


async def _send_and_save_text(
    *,
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
    response_text: str,
    language: str | None,
    raw_payload: dict,
) -> None:
    sent = await message.answer(response_text)
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
        raw_payload=raw_payload,
    )


def _reply_markup_for_graph_result(graph_result: GraphResult, language: str):
    missing_fields = graph_result.metadata.get("missing_fields") or []
    active_appointments = graph_result.metadata.get("active_appointments") or []
    if graph_result.proposed_slots:
        return booking_slots_keyboard(graph_result.proposed_slots)
    if graph_result.intent == "cancel_appointment" and active_appointments:
        return cancel_appointments_keyboard(active_appointments)
    if graph_result.intent == "reschedule_appointment" and active_appointments:
        return reschedule_appointments_keyboard(active_appointments)
    if graph_result.intent == "book_appointment" and "phone" in missing_fields:
        return contact_request_keyboard(language)
    return None


async def _ensure_ogg(file_path: str) -> Path:
    path = Path(file_path)
    if path.suffix.casefold() in (".ogg", ".opus"):
        return path

    output_path = Path(str(path) + ".ogg")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-c:a",
        "libopus",
        "-b:a",
        "16k",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        error_text = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg ogg conversion failed: {error_text}")
    return output_path
