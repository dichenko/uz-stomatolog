# Задача: улучшить качество русской TTS-озвучки через Yandex SpeechKit

## Контекст

В проекте уже используется Yandex SpeechKit API v1 для генерации голосовых сообщений в Telegram.

Текущий голос звучит слишком медленно и неэмоционально. Нужно вынести настройки голоса в ENV и убедиться, что бот использует их при каждом запросе к Yandex SpeechKit.

ENV-переменные добавит заказчик:

```env
YANDEX_TTS_VOICE=alena
YANDEX_TTS_EMOTION=good
YANDEX_TTS_SPEED=1.15
YANDEX_TTS_FORMAT=oggopus
```

## Что нужно сделать

### 1. Обновить TTS-конфигурацию

В коде сервиса Yandex SpeechKit TTS нужно читать параметры из ENV:

```python
YANDEX_TTS_VOICE
YANDEX_TTS_EMOTION
YANDEX_TTS_SPEED
YANDEX_TTS_FORMAT
```

Если переменные не заданы, использовать значения по умолчанию:

```python
voice = "alena"
emotion = "good"
speed = "1.15"
audio_format = "oggopus"
```

---

### 2. Обновить payload запроса к Yandex SpeechKit

В запросе к API:

```text
https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize
```

должны передаваться параметры:

```python
payload = {
    "text": prepared_text,
    "lang": "ru-RU",
    "voice": voice,
    "emotion": emotion,
    "speed": speed,
    "format": audio_format,
}
```

Важно:

- `lang` всегда `ru-RU`;
- `format` должен быть `oggopus`, чтобы Telegram мог отправлять результат как voice-сообщение без FFmpeg;
- `speed` передавать строкой или числом, но значение должно попадать в API как `1.15`.

---

### 3. Добавить логирование текущих TTS-настроек

При старте приложения или при первом TTS-запросе залогировать:

```text
Yandex TTS config:
voice=alena
emotion=good
speed=1.15
format=oggopus
```

API-ключ логировать нельзя.

---

### 4. Проверить обработку ошибок

Если Yandex SpeechKit возвращает ошибку, нужно логировать:

- HTTP status code;
- response body;
- voice;
- emotion;
- speed;
- format.

Не логировать:

- `YANDEX_SPEECHKIT_API_KEY`;
- полный текст пользователя, если в проекте есть требования к приватности.

Пример:

```python
logger.error(
    "Yandex TTS failed: status=%s, body=%s, voice=%s, emotion=%s, speed=%s, format=%s",
    response.status_code,
    response.text[:1000],
    voice,
    emotion,
    speed,
    audio_format,
)
```

---

### 5. Проверить, что Telegram получает именно voice

Результат от SpeechKit должен отправляться в Telegram через `send_voice`, а не как обычный audio-файл.

Для `aiogram` пример:

```python
from aiogram.types import BufferedInputFile

await bot.send_voice(
    chat_id=chat_id,
    voice=BufferedInputFile(audio_bytes, filename="voice.ogg"),
)
```

---

### 6. Подготовить текст перед озвучкой

Перед отправкой в Yandex SpeechKit текст нужно очищать от мусора:

- markdown-разметки;
- ссылок;
- эмодзи;
- длинных списков;
- технических символов.

Минимальная функция подготовки:

```python
import re

def prepare_text_for_tts(text: str) -> str:
    text = text.strip()

    # Markdown links: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # URLs
    text = re.sub(r"https?://\S+", "", text)

    # Markdown formatting
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")

    # Symbols
    text = text.replace("₽", " рублей")
    text = text.replace("$", " долларов")
    text = text.replace("%", " процентов")

    # Lists/bullets
    text = text.replace("•", ". ")

    # Spaces
    text = re.sub(r"\s+", " ", text)

    return text.strip()
```

---

### 7. Добавить тестовую команду или скрипт

Нужно добавить простой локальный тест, чтобы можно было быстро проверить разные голоса без запуска всего бота.

Файл:

```text
scripts/test_yandex_tts.py
```

Пример запуска:

```bash
python scripts/test_yandex_tts.py
```

Скрипт должен:

1. взять настройки из ENV;
2. отправить тестовый русский текст в Yandex SpeechKit;
3. сохранить файл:

```text
output/test_voice.ogg
```

Тестовый текст:

```text
Здравствуйте! Я помогу вам записаться на приём. Подскажите, пожалуйста, какой день и время вам удобны?
```

---

## Критерии готовности

Задача считается выполненной, если:

- бот читает `YANDEX_TTS_VOICE`, `YANDEX_TTS_EMOTION`, `YANDEX_TTS_SPEED`, `YANDEX_TTS_FORMAT` из ENV;
- при отсутствии ENV используются дефолты `alena`, `good`, `1.15`, `oggopus`;
- Yandex SpeechKit получает эти параметры в каждом TTS-запросе;
- Telegram отправляет результат именно как voice-сообщение;
- есть логирование TTS-настроек без API-ключа;
- есть тестовый скрипт, который создаёт `output/test_voice.ogg`;
- если TTS падает, бот не падает целиком, а продолжает работать и отправляет хотя бы текстовый ответ.
