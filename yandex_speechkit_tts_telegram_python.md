# Yandex SpeechKit TTS → голосовые сообщения Telegram на русском языке

Инструкция для программиста по подключению генерации русских голосовых сообщений в Python Telegram-боте через Yandex SpeechKit.

---

## 1. Цель

Нужно добавить в Python Telegram-бота генерацию русских голосовых сообщений через **Yandex SpeechKit**.

Рекомендуемая схема:

```text
текст ответа бота → Yandex SpeechKit API v1 → .ogg / opus → Telegram sendVoice
```

Для Telegram на первом этапе рекомендуется использовать **SpeechKit API v1**, а не API v3, потому что API v1 умеет сразу отдавать аудио в формате `oggopus`.

Telegram `sendVoice` нормально принимает голосовые сообщения в формате `.ogg`, закодированные OPUS. Поэтому можно обойтись без FFmpeg и без дополнительной конвертации.

---

## 2. Что завести в Yandex Cloud

### 2.1. Создать сервисный аккаунт

В Yandex Cloud нужно:

1. Создать или выбрать облако.
2. Создать или выбрать каталог.
3. Создать сервисный аккаунт, например:

```text
speechkit-sa
```

4. Выдать сервисному аккаунту роль:

```text
ai.speechkit-tts.user
```

Эта роль нужна для синтеза речи через SpeechKit TTS.

---

### 2.2. Создать API-ключ

Для сервисного аккаунта нужно создать **API-ключ**.

В запросах к SpeechKit API ключ передаётся так:

```http
Authorization: Api-Key <API_KEY>
```

При использовании API-ключа сервисного аккаунта `folderId` можно не передавать: SpeechKit использует каталог, к которому относится сервисный аккаунт.

---

## 3. Переменные окружения

Добавить в `.env`:

```env
YANDEX_SPEECHKIT_API_KEY=your_api_key_here

YANDEX_TTS_VOICE=marina
YANDEX_TTS_EMOTION=friendly
YANDEX_TTS_SPEED=0.95
YANDEX_TTS_FORMAT=oggopus

TTS_ENABLED=true
SEND_VOICE_REPLIES=true
```

### Рекомендуемый стартовый голос

Для стоматологического бота на русском языке начать с:

```env
YANDEX_TTS_VOICE=marina
YANDEX_TTS_EMOTION=friendly
YANDEX_TTS_SPEED=0.95
```

Также стоит протестировать:

```text
alena + good
jane + good
marina + friendly
ermil + good
zahar + good
```

Важно: не каждое амплуа доступно для каждого голоса. Если указать неподдерживаемое амплуа, SpeechKit вернёт ошибку.

---

## 4. Установка зависимостей

Минимально:

```bash
pip install requests python-dotenv
```

Если бот на `aiogram`:

```bash
pip install aiogram
```

---

## 5. Сервис синтеза речи

Создать файл:

```text
app/services/yandex_tts.py
```

Код:

```python
import os
import re
from dataclasses import dataclass

import requests


YANDEX_TTS_ENDPOINT = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"


class TTSProviderError(Exception):
    pass


@dataclass
class YandexTTSConfig:
    api_key: str
    voice: str = "marina"
    emotion: str = "friendly"
    speed: str = "0.95"
    audio_format: str = "oggopus"


class YandexSpeechKitTTS:
    def __init__(self, config: YandexTTSConfig):
        if not config.api_key:
            raise ValueError("YANDEX_SPEECHKIT_API_KEY is required")

        self.config = config

    @classmethod
    def from_env(cls) -> "YandexSpeechKitTTS":
        return cls(
            YandexTTSConfig(
                api_key=os.getenv("YANDEX_SPEECHKIT_API_KEY", ""),
                voice=os.getenv("YANDEX_TTS_VOICE", "marina"),
                emotion=os.getenv("YANDEX_TTS_EMOTION", "friendly"),
                speed=os.getenv("YANDEX_TTS_SPEED", "0.95"),
                audio_format=os.getenv("YANDEX_TTS_FORMAT", "oggopus"),
            )
        )

    def synthesize_ogg(self, text: str) -> bytes:
        prepared_text = prepare_text_for_tts(text)

        if not prepared_text:
            raise ValueError("Empty text for TTS")

        # SpeechKit API v1 принимает текст до 5000 символов.
        if len(prepared_text) > 5000:
            prepared_text = prepared_text[:5000]

        payload = {
            "text": prepared_text,
            "lang": "ru-RU",
            "voice": self.config.voice,
            "emotion": self.config.emotion,
            "speed": self.config.speed,
            "format": self.config.audio_format,
        }

        headers = {
            "Authorization": f"Api-Key {self.config.api_key}",
        }

        response = requests.post(
            YANDEX_TTS_ENDPOINT,
            headers=headers,
            data=payload,
            timeout=(5, 60),
        )

        if response.status_code != 200:
            raise TTSProviderError(
                f"Yandex SpeechKit TTS failed: "
                f"status={response.status_code}, body={response.text[:1000]}"
            )

        return response.content


def prepare_text_for_tts(text: str) -> str:
    """
    Подготовка текста для озвучки:
    - убираем Markdown;
    - убираем ссылки;
    - заменяем спецсимволы;
    - делаем текст более естественным для произнесения.
    """

    text = text.strip()

    # Markdown-ссылки: [текст](url) -> текст
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Голые URL
    text = re.sub(r"https?://\S+", "", text)

    # Markdown-разметка
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")

    # Маркеры списков часто плохо звучат в TTS
    text = text.replace("•", ". ")
    text = text.replace("-", " ")

    # Частые символы
    text = text.replace("₽", " рублей")
    text = text.replace("$", " долларов")
    text = text.replace("%", " процентов")

    # Схлопываем пробелы
    text = re.sub(r"\s+", " ", text)

    return text.strip()
```

---

## 6. Отправка голосового сообщения в Telegram

### Вариант A. Если бот на aiogram 3

```python
from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.services.yandex_tts import YandexSpeechKitTTS


tts = YandexSpeechKitTTS.from_env()


async def send_voice_reply(bot: Bot, chat_id: int, text: str) -> None:
    audio_bytes = tts.synthesize_ogg(text)

    voice_file = BufferedInputFile(
        audio_bytes,
        filename="voice.ogg",
    )

    await bot.send_voice(
        chat_id=chat_id,
        voice=voice_file,
    )
```

---

### Вариант B. Через прямой Telegram Bot API

```python
import requests


def send_telegram_voice(
    bot_token: str,
    chat_id: int | str,
    audio_bytes: bytes,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendVoice"

    files = {
        "voice": ("voice.ogg", audio_bytes, "audio/ogg"),
    }

    data = {
        "chat_id": chat_id,
    }

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=(5, 60),
    )

    response.raise_for_status()
    return response.json()
```

---

## 7. Встраивание в обработчик сообщения

Пример для `aiogram 3`.

```python
import os

from aiogram import Router, Bot
from aiogram.types import Message, BufferedInputFile

from app.services.yandex_tts import YandexSpeechKitTTS


router = Router()
tts = YandexSpeechKitTTS.from_env()


@router.message()
async def handle_message(message: Message, bot: Bot):
    user_text = message.text or ""

    # Здесь должна быть обычная LLM-логика проекта.
    answer_text = await generate_ai_answer(user_text)

    # Текстовый ответ отправляем всегда.
    await message.answer(answer_text)

    tts_enabled = os.getenv("TTS_ENABLED", "false").lower() == "true"
    send_voice = os.getenv("SEND_VOICE_REPLIES", "false").lower() == "true"

    if tts_enabled and send_voice:
        try:
            audio_bytes = tts.synthesize_ogg(answer_text)

            await bot.send_voice(
                chat_id=message.chat.id,
                voice=BufferedInputFile(audio_bytes, filename="voice.ogg"),
            )

        except Exception as exc:
            # Если TTS упал, бот не должен падать целиком.
            # Текстовый ответ уже отправлен, поэтому просто логируем ошибку.
            print(f"TTS error: {exc}")
```

---

## 8. Рекомендуемая продакшен-логика

Рекомендуемое поведение бота:

```text
1. Бот всегда отправляет текстовый ответ.
2. Если пользователь прислал голосовое сообщение — бот отвечает текстом + голосом.
3. Если пользователь пишет текстом — бот отвечает только текстом, если в настройках не включено "всегда отвечать голосом".
4. Если TTS упал — бот не падает, просто остаётся текстовый ответ.
5. Повторяющиеся шаблонные фразы кэшируются.
```

Почему так:

- текстовый ответ остаётся основным и надёжным каналом;
- голос — дополнительное удобство;
- падение Yandex SpeechKit не должно ломать весь диалог;
- кэш снижает стоимость и ускоряет ответы.

---

## 9. Кэширование голосовых файлов

Повторяющиеся фразы лучше кэшировать.

Например:

```text
Здравствуйте! Я виртуальный ассистент клиники.
Спасибо, я передам ваш вопрос администратору.
Выберите, пожалуйста, удобное время для записи.
```

Создать файл:

```text
app/services/cached_yandex_tts.py
```

Код:

```python
import hashlib
from pathlib import Path

from app.services.yandex_tts import YandexSpeechKitTTS, prepare_text_for_tts


CACHE_DIR = Path("data/tts_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CachedYandexSpeechKitTTS(YandexSpeechKitTTS):
    def synthesize_ogg_cached(self, text: str) -> bytes:
        prepared_text = prepare_text_for_tts(text)

        cache_key = (
            f"{self.config.voice}:"
            f"{self.config.emotion}:"
            f"{self.config.speed}:"
            f"{prepared_text}"
        )

        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        cache_path = CACHE_DIR / f"{digest}.ogg"

        if cache_path.exists():
            return cache_path.read_bytes()

        audio_bytes = self.synthesize_ogg(prepared_text)
        cache_path.write_bytes(audio_bytes)

        return audio_bytes
```

Использование:

```python
from app.services.cached_yandex_tts import CachedYandexSpeechKitTTS


tts = CachedYandexSpeechKitTTS.from_env()

audio_bytes = tts.synthesize_ogg_cached(answer_text)
```

---

## 10. Подготовка текста для качественной русской озвучки

Нельзя бездумно озвучивать тот же текст, который отправляется пользователю в чат.

Плохой вариант для TTS:

```text
Здравствуйте! 😊 Вы можете записаться на приём по ссылке: https://...
```

Лучше для TTS:

```text
Здравствуйте! Я помогу вам записаться на приём. Напишите, пожалуйста, какой день и время вам удобны.
```

Рекомендации:

1. Убирать Markdown.
2. Убирать ссылки.
3. Убирать эмодзи.
4. Делать короткие предложения.
5. Не озвучивать длинные списки.
6. Не озвучивать технические данные без необходимости.
7. Для голосового ответа желательно генерировать отдельный короткий текст, а не озвучивать полный текстовый ответ.

---

## 11. Отдельный текст для голосового ответа

Лучший вариант архитектуры:

```text
LLM генерирует:
1. text_answer — полный текстовый ответ для Telegram.
2. voice_answer — короткую версию для озвучки.
```

Пример структуры:

```json
{
  "text_answer": "Здравствуйте! Я могу помочь вам записаться на приём, уточнить стоимость услуг или передать вопрос администратору. Напишите, пожалуйста, что вас интересует.",
  "voice_answer": "Здравствуйте! Я помогу вам записаться на приём или уточнить стоимость услуг. Напишите, пожалуйста, что вас интересует."
}
```

В Telegram:

```text
text_answer → message.answer(...)
voice_answer → Yandex SpeechKit → sendVoice(...)
```

Так голос звучит естественнее, а пользователь не слушает длинное полотно текста.

---

## 12. Настройка ударений и пауз

Если SpeechKit неправильно произносит слово, можно подсказывать ударение.

Например:

```text
з+амок
зам+ок
```

Знак `+` ставится перед ударной гласной.

Для паузы можно использовать дефис:

```text
Здравствуйте - я помогу вам записаться на приём.
```

Это нужно использовать точечно, только для слов, где SpeechKit ошибается.

---

## 13. Ограничения и риски

### 13.1. Региональная доступность

Нужно проверить доступность SpeechKit для конкретного аккаунта Yandex Cloud и региона.

В документации Yandex для API v1 указано, что функциональность доступна в регионе Россия. Это может быть важным ограничением для оплаты, аккаунта и юридического использования.

---

### 13.2. Ограничение длины текста

SpeechKit API v1 принимает текст до 5000 символов.

На практике для голосового сообщения лучше использовать намного меньше:

```text
до 300–700 символов
```

Иначе голосовое сообщение становится слишком длинным и неудобным.

---

### 13.3. Ошибки TTS не должны ломать бота

Если Yandex SpeechKit недоступен, закончилась квота, неверный ключ или сервис вернул ошибку, бот всё равно должен отправить текстовый ответ.

Правильная логика:

```text
try:
    сгенерировать голос
    отправить voice
except:
    залогировать ошибку
    не ломать диалог
```

---

## 14. Что логировать

Минимально логировать:

```text
tts_provider = yandex_speechkit
tts_voice
tts_emotion
tts_speed
tts_text_length
tts_success
tts_error
tts_duration_ms
```

Не стоит логировать полный текст пользователя, если в проекте есть требования к приватности.

---

## 15. Тестовые фразы для проверки качества

Перед внедрением нужно руками прослушать несколько фраз.

```text
Здравствуйте! Я виртуальный ассистент клиники. Помогу записаться на приём, уточнить стоимость услуг или передать вопрос администратору.

Пожалуйста, напишите, что вас интересует: лечение, имплантация, чистка, ортодонтия или запись к врачу.

Вы записаны на завтра, 21 мая, в 14:30. Если планы изменятся, пожалуйста, предупредите нас заранее.

Стоимость консультации зависит от специалиста и вида услуги. Я могу передать ваш вопрос администратору, чтобы он уточнил цену.

Пожалуйста, напишите ваше имя и номер телефона. Администратор свяжется с вами для подтверждения записи.
```

Проверять по критериям:

```text
1. Правильные ударения.
2. Естественная русская интонация.
3. Не слишком быстрая речь.
4. Не слишком роботизированный голос.
5. Хорошее произношение дат, времени, адресов и медицинских слов.
6. Отсутствие странных пауз.
7. Приятный тон для стоматологической клиники.
```

---

## 16. Минимальный тестовый скрипт

Создать файл:

```text
scripts/test_yandex_tts.py
```

Код:

```python
from pathlib import Path

from dotenv import load_dotenv

from app.services.yandex_tts import YandexSpeechKitTTS


load_dotenv()


def main():
    tts = YandexSpeechKitTTS.from_env()

    text = (
        "Здравствуйте! Я виртуальный ассистент клиники. "
        "Помогу записаться на приём, уточнить стоимость услуг "
        "или передать вопрос администратору."
    )

    audio_bytes = tts.synthesize_ogg(text)

    output_path = Path("test_voice.ogg")
    output_path.write_bytes(audio_bytes)

    print(f"Saved: {output_path.resolve()}")


if __name__ == "__main__":
    main()
```

Запуск:

```bash
python scripts/test_yandex_tts.py
```

Ожидаемый результат:

```text
В корне проекта появится файл test_voice.ogg.
Его можно открыть и прослушать.
```

---

## 17. Минимальный тест отправки в Telegram

Создать файл:

```text
scripts/test_send_voice.py
```

Код:

```python
import os

from dotenv import load_dotenv
import requests

from app.services.yandex_tts import YandexSpeechKitTTS


load_dotenv()


def send_telegram_voice(bot_token: str, chat_id: str, audio_bytes: bytes) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendVoice"

    files = {
        "voice": ("voice.ogg", audio_bytes, "audio/ogg"),
    }

    data = {
        "chat_id": chat_id,
    }

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=(5, 60),
    )

    response.raise_for_status()
    return response.json()


def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TEST_TELEGRAM_CHAT_ID", "")

    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    if not chat_id:
        raise ValueError("TEST_TELEGRAM_CHAT_ID is required")

    tts = YandexSpeechKitTTS.from_env()

    text = (
        "Здравствуйте! Это тестовое голосовое сообщение "
        "от виртуального ассистента стоматологической клиники."
    )

    audio_bytes = tts.synthesize_ogg(text)

    result = send_telegram_voice(
        bot_token=bot_token,
        chat_id=chat_id,
        audio_bytes=audio_bytes,
    )

    print(result)


if __name__ == "__main__":
    main()
```

Добавить в `.env`:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TEST_TELEGRAM_CHAT_ID=your_chat_id
```

Запуск:

```bash
python scripts/test_send_voice.py
```

---

## 18. Когда переходить на SpeechKit API v3

На первом этапе лучше использовать API v1.

Переходить на API v3 есть смысл, если понадобится:

```text
1. Более тонкая настройка синтеза.
2. Streaming-синтез.
3. Генерация голоса по мере генерации LLM-ответа.
4. Настройки pitch, loudness normalization и другие расширенные параметры.
```

Но для MVP Telegram-бота:

```text
SpeechKit API v1 → format=oggopus → Telegram sendVoice
```

Это проще и быстрее.

---

## 19. Acceptance Criteria

Задача считается выполненной, если:

```text
1. В проект добавлен сервис YandexSpeechKitTTS.
2. API-ключ берётся из env.
3. Голос, emotion, speed и format берутся из env.
4. Текст перед отправкой в TTS очищается от Markdown, ссылок и лишних символов.
5. SpeechKit генерирует .ogg/opus audio bytes.
6. Telegram-бот умеет отправлять результат через sendVoice.
7. Если TTS падает, основной текстовый ответ всё равно отправляется.
8. Ошибка TTS логируется.
9. Есть тестовый скрипт генерации test_voice.ogg.
10. Есть тестовый скрипт отправки voice в Telegram.
11. Повторяющиеся голосовые ответы можно кэшировать.
```

---

## 20. Полезные ссылки

Официальная документация Yandex SpeechKit TTS API v1:

```text
https://yandex.cloud/ru-kz/docs/speechkit/tts/request
```

Голоса Yandex SpeechKit:

```text
https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices
```

Аутентификация в Yandex SpeechKit:

```text
https://aistudio.yandex.ru/docs/ru/speechkit/concepts/auth
```

Telegram Bot API `sendVoice`:

```text
https://core.telegram.org/bots/api#sendvoice
```
