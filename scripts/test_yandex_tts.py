import asyncio
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT_DIR / "apps" / "bot"
sys.path.insert(0, str(BOT_DIR))

from app.config import get_settings  # noqa: E402
from app.speech.temp_files import cleanup_temp_file  # noqa: E402
from app.speech.yandex_provider import YandexSpeechKitProvider  # noqa: E402


TEST_TEXT = (
    "\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435! "
    "\u042f \u043f\u043e\u043c\u043e\u0433\u0443 \u0432\u0430\u043c "
    "\u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f "
    "\u043d\u0430 \u043f\u0440\u0438\u0451\u043c. "
    "\u041f\u043e\u0434\u0441\u043a\u0430\u0436\u0438\u0442\u0435, "
    "\u043f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, "
    "\u043a\u0430\u043a\u043e\u0439 \u0434\u0435\u043d\u044c "
    "\u0438 \u0432\u0440\u0435\u043c\u044f \u0432\u0430\u043c "
    "\u0443\u0434\u043e\u0431\u043d\u044b?"
)


async def main() -> None:
    settings = get_settings()
    provider = YandexSpeechKitProvider(settings)
    result = await provider.synthesize(TEST_TEXT, "ru")

    output_dir = ROOT_DIR / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "test_voice.ogg"
    shutil.copyfile(result.file_path, output_path)
    await cleanup_temp_file(result.file_path, reason="test_yandex_tts_cleanup")

    print(f"Saved: {output_path}")
    print(
        "Config: "
        f"voice={settings.yandex_tts_voice}, "
        f"emotion={settings.yandex_tts_emotion}, "
        f"speed={settings.yandex_tts_speed}, "
        f"format={settings.yandex_tts_format}"
    )


if __name__ == "__main__":
    asyncio.run(main())
