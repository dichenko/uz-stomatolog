import asyncio
import logging
import tempfile
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class AudioValidationError(ValueError):
    pass


def create_temp_audio_path(*, suffix: str) -> Path:
    settings = get_settings()
    temp_dir = Path(settings.speech_temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix="dental-bot-",
        suffix=suffix,
        dir=temp_dir,
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def validate_file_size(file_path: str | Path, *, max_size_mb: int) -> None:
    path = Path(file_path)
    max_bytes = max_size_mb * 1024 * 1024
    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise AudioValidationError(
            f"Audio file is too large: {size_bytes} bytes, limit is {max_bytes} bytes"
        )


async def convert_to_wav(input_path: str | Path) -> Path:
    output_path = create_temp_audio_path(suffix=".wav")
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        await cleanup_temp_file(output_path, reason="failed_wav_conversion")
        error_text = stderr.decode("utf-8", errors="replace").strip()
        raise SpeechTempFileError(f"ffmpeg conversion failed: {error_text}")
    return output_path


async def cleanup_temp_file(file_path: str | Path | None, *, reason: str) -> None:
    if file_path is None:
        return

    path = Path(file_path)
    try:
        if path.exists():
            path.unlink()
            logger.info(
                "speech_temp_file_deleted",
                extra={"file_path": str(path), "reason": reason},
            )
        else:
            logger.info(
                "speech_temp_file_missing",
                extra={"file_path": str(path), "reason": reason},
            )
    except OSError:
        logger.exception(
            "speech_temp_file_delete_failed",
            extra={"file_path": str(path), "reason": reason},
        )


class SpeechTempFileError(RuntimeError):
    pass
