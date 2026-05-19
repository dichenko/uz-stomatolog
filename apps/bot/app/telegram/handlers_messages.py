import logging

from aiogram import F, Router
from aiogram.types import FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Conversation, User
from app.db.models import Message as DbMessage
from app.db.repositories import MessageRepository
from app.graph import GraphResult, run_bot_graph
from app.graph.state import InputType
from app.speech import create_speech_providers
from app.speech.base import SpeechProviderError
from app.speech.temp_files import (
    AudioValidationError,
    cleanup_temp_file,
    create_temp_audio_path,
    validate_file_size,
)
from app.telegram.keyboards import language_keyboard
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
    sent = await message.answer(response_text)
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

    if message.voice.duration > settings.muxlisa_max_audio_duration_sec:
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
    try:
        telegram_file = await message.bot.get_file(message.voice.file_id)
        if telegram_file.file_path is None:
            raise SpeechProviderError("Telegram voice file path is empty")
        await message.bot.download_file(telegram_file.file_path, destination=input_path)
        max_size_mb = (
            settings.muxlisa_max_audio_size_mb
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
        response_text = (
            f"{graph_result.final_response_text}\n\n"
            f"{text('voice_ai_disclosure', language)}"
        )
        sent = await message.answer(response_text)
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
            tts_result = await providers.tts_for_language(language).synthesize(
                graph_result.final_response_text,
                language,
            )
            output_path = tts_result.file_path
            audio = FSInputFile(output_path)
            sent_audio = await message.answer_audio(audio)
            await save_outgoing_message(
                session=db_session,
                user=db_user,
                conversation=db_conversation,
                telegram_message_id=sent_audio.message_id,
                text=graph_result.final_response_text,
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
    telegram_user = message.from_user
    return await run_bot_graph(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        trace_id=trace_id,
        telegram_chat_id=message.chat.id,
        input_text=input_text,
        input_type=input_type,
        preferred_language=normalize_language(language),
        telegram_profile=telegram_user.model_dump(mode="json") if telegram_user else {},
        input_message_id=db_incoming_message.id,
        admin_bot=message.bot,
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
