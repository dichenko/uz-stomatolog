from app.speech.base import SpeechToTextResult, TextToSpeechResult
from app.speech.temp_files import create_temp_audio_path


class MockSpeechProvider:
    def __init__(self, transcription: str = "Mock transcription") -> None:
        self.transcription = transcription

    async def transcribe(self, file_path: str, language: str) -> SpeechToTextResult:
        return SpeechToTextResult(
            text=self.transcription,
            provider="mock",
            model="mock-stt",
            language=language,
            raw={"file_path": file_path},
        )

    async def synthesize(self, text: str, language: str) -> TextToSpeechResult:
        output_path = create_temp_audio_path(suffix=".mp3")
        output_path.write_bytes(b"mock-audio")
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type="audio/mpeg",
            format="mp3",
            provider="mock",
            model="mock-tts",
            voice="mock",
        )
