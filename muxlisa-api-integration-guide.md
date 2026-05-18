# Muxlisa API Integration Guide

## Goal

Integrate Muxlisa API for:

1. Speech-to-Text — converting user voice messages into text.
2. Text-to-Speech — converting bot text responses into `.wav` audio files.

Muxlisa API must be used only from the backend. Never expose the API key or call Muxlisa directly from frontend, Telegram Mini App, browser, or mobile client.

---

# Environment variables

Use the following environment variables:

```env
# Muxlisa
MUXLISA_BASE_URL=https://service.muxlisa.uz
MUXLISA_API_KEY=your_api_key_here

# Optional request settings
MUXLISA_STT_TIMEOUT_MS=60000
MUXLISA_TTS_TIMEOUT_MS=60000
MUXLISA_MAX_AUDIO_SIZE_MB=5
MUXLISA_MAX_AUDIO_DURATION_SEC=60
```

Important:

- `MUXLISA_BASE_URL` should contain only the service origin: `https://service.muxlisa.uz`
- Do not put `/api/v2/stt` or `/api/v2/tts` into `MUXLISA_BASE_URL`.
- API endpoints should be built in code:

```ts
const STT_URL = `${MUXLISA_BASE_URL}/api/v2/stt`;
const TTS_URL = `${MUXLISA_BASE_URL}/api/v2/tts`;
```

Required `.env.example` entry:

```env
MUXLISA_BASE_URL=https://service.muxlisa.uz
MUXLISA_API_KEY=
MUXLISA_STT_TIMEOUT_MS=60000
MUXLISA_TTS_TIMEOUT_MS=60000
MUXLISA_MAX_AUDIO_SIZE_MB=5
MUXLISA_MAX_AUDIO_DURATION_SEC=60
```

---

# API endpoints

## 1. Speech-to-Text

Endpoint:

```txt
POST https://service.muxlisa.uz/api/v2/stt
```

Content type:

```txt
multipart/form-data
```

Headers:

```txt
x-api-key: <MUXLISA_API_KEY>
```

Body:

| Field | Type | Required | Description |
|---|---|---|---|
| audio | file | yes | Audio file for transcription |

Supported formats:

```txt
mpeg, x-wav, vnd.wav, wav, wave, ogg, flac, x-m4a, aac,
mp4, webm, 3gpp, 3gpp2, x-ms-wma, amr
```

Recommended format:

```txt
wav
```

Limits:

```txt
Max file size: 5 MB
Max duration: 60 seconds
```

---

## 2. Text-to-Speech

Endpoint:

```txt
POST https://service.muxlisa.uz/api/v2/tts
```

Content type:

```txt
application/json
```

Headers:

```txt
x-api-key: <MUXLISA_API_KEY>
Content-Type: application/json
```

Body:

```json
{
  "text": "YOUR_TEXT",
  "speaker": 1
}
```

Fields:

| Field | Type | Required | Description |
|---|---|---|---|
| text | string | yes | Text to convert to speech. Max 512 characters. |
| speaker | number | no | 0 = female voice, 1 = male voice. Default: 1 |

Important:

- Successful TTS response is binary `.wav` audio.
- Do not parse successful TTS response as JSON.
- Save it as a binary buffer/file.

---

# Recommended architecture

Do not call Muxlisa directly inside Telegram webhook handling.

Correct flow:

```txt
Telegram webhook
  ↓
Save incoming message/update to PostgreSQL
  ↓
Create background job
  ↓
Worker downloads Telegram voice file
  ↓
Worker validates/converts audio
  ↓
Worker calls Muxlisa STT
  ↓
Worker stores recognized text
  ↓
Worker sends text to OpenAI
  ↓
Worker sends final response to user
```

The Telegram webhook handler must stay fast and return `200 OK` quickly.

---

# Audio processing rules

Telegram voice messages usually arrive as OGG/OPUS.

Muxlisa supports `ogg`, but the recommended format is `wav`.

Implementation strategy:

1. Download Telegram voice file.
2. Check file duration.
3. If duration > 60 seconds, reject politely.
4. Convert audio to WAV before sending to Muxlisa.
5. Use predictable WAV settings:

```txt
Format: wav
Codec: PCM signed 16-bit little-endian
Sample rate: 16000 Hz
Channels: mono
```

Example ffmpeg command:

```bash
ffmpeg -i input.ogg -ar 16000 -ac 1 -c:a pcm_s16le output.wav
```

After conversion, check file size. It must be <= 5 MB.

For 60 seconds of 16 kHz mono WAV, the size should be around 2 MB, so this is safe.

---

# TypeScript interface

Create provider interface:

```ts
export interface SpeechToTextResult {
  text: string;
  raw?: unknown;
}

export interface TextToSpeechResult {
  audioBuffer: Buffer;
  mimeType: 'audio/wav';
}

export interface SpeechProvider {
  transcribe(input: {
    filePath: string;
    filename?: string;
    mimeType?: string;
  }): Promise<SpeechToTextResult>;

  synthesize(input: {
    text: string;
    speaker?: 0 | 1;
  }): Promise<TextToSpeechResult>;
}
```

Create implementation:

```ts
export class MuxlisaSpeechProvider implements SpeechProvider {
  // uses MUXLISA_BASE_URL and MUXLISA_API_KEY
}
```

Business logic must depend on `SpeechProvider`, not directly on Muxlisa URLs. This will allow replacing Muxlisa or adding another provider later.

---

# STT implementation details

Use Node.js 20+ native `fetch`, `FormData`, and `Blob`, or a stable multipart library.

Example:

```ts
import { readFile } from 'node:fs/promises';

export async function transcribeWithMuxlisa(params: {
  baseUrl: string;
  apiKey: string;
  filePath: string;
  filename?: string;
  mimeType?: string;
  timeoutMs?: number;
}) {
  const {
    baseUrl,
    apiKey,
    filePath,
    filename = 'audio.wav',
    mimeType = 'audio/wav',
    timeoutMs = 60000,
  } = params;

  const audioBuffer = await readFile(filePath);

  const formData = new FormData();
  formData.append(
    'audio',
    new Blob([audioBuffer], { type: mimeType }),
    filename,
  );

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${baseUrl}/api/v2/stt`, {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
      },
      body: formData,
      signal: controller.signal,
    });

    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
      let errorPayload: unknown = null;

      if (contentType.includes('application/json')) {
        errorPayload = await response.json().catch(() => null);
      } else {
        errorPayload = await response.text().catch(() => null);
      }

      throw new Error(
        `Muxlisa STT failed: status=${response.status}, body=${JSON.stringify(errorPayload)}`,
      );
    }

    const result = await response.json();

    return result;
  } finally {
    clearTimeout(timeout);
  }
}
```

After receiving the result, normalize it into internal format:

```ts
return {
  text: result.text ?? result.transcription ?? '',
  raw: result,
};
```

If the exact response field from Muxlisa is known after testing, replace this with strict parsing.

---

# TTS implementation details

Muxlisa TTS returns binary WAV data on success.

Example:

```ts
export async function synthesizeWithMuxlisa(params: {
  baseUrl: string;
  apiKey: string;
  text: string;
  speaker?: 0 | 1;
  timeoutMs?: number;
}) {
  const {
    baseUrl,
    apiKey,
    text,
    speaker = 1,
    timeoutMs = 60000,
  } = params;

  if (text.length > 512) {
    throw new Error('Muxlisa TTS text limit exceeded: max 512 characters');
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${baseUrl}/api/v2/tts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
      },
      body: JSON.stringify({
        text,
        speaker,
      }),
      signal: controller.signal,
    });

    const contentType = response.headers.get('content-type') || '';

    if (!response.ok) {
      let errorPayload: unknown = null;

      if (contentType.includes('application/json')) {
        errorPayload = await response.json().catch(() => null);
      } else {
        errorPayload = await response.text().catch(() => null);
      }

      throw new Error(
        `Muxlisa TTS failed: status=${response.status}, body=${JSON.stringify(errorPayload)}`,
      );
    }

    const arrayBuffer = await response.arrayBuffer();

    return {
      audioBuffer: Buffer.from(arrayBuffer),
      mimeType: 'audio/wav' as const,
    };
  } finally {
    clearTimeout(timeout);
  }
}
```

---

# Text length handling for TTS

Muxlisa TTS accepts maximum 512 characters.

If bot response is longer:

1. Split text into chunks up to 512 characters.
2. Prefer splitting by sentence boundaries.
3. Generate separate WAV files for each chunk.
4. Either:
   - send several voice/audio messages to the user, or
   - concatenate WAV files server-side before sending.

For MVP, simplest acceptable behavior:

```txt
If response text > 512 characters:
- send text answer as normal Telegram text;
- optionally synthesize only the first 512 characters.
```

---

# Error handling

Muxlisa documented statuses:

| Status | Meaning | Required behavior |
|---|---|---|
| 200 | Success | Continue processing |
| 400 | Bad request | Do not retry. Log details. Notify user politely. |
| 402 | Payment required | Do not retry. Notify admin. User sees temporary service error. |
| 429 | Too many requests | Retry with backoff. |
| 5XX | Server error | Retry with backoff. |

Recommended retry policy:

```txt
Retry only for:
- 429
- 500
- 502
- 503
- 504
- network timeout

Do not retry:
- 400
- 401
- 402
- 403
- unsupported format
- file too large
- duration too long
```

Backoff example:

```txt
Attempt 1: immediately
Attempt 2: after 2 seconds
Attempt 3: after 5 seconds
```

Maximum attempts: 3.

---

# Logging rules

Log:

```txt
- provider: muxlisa
- operation: stt / tts
- request duration
- status code
- file size
- audio duration
- Telegram user id
- internal message id / job id
```

Never log:

```txt
- MUXLISA_API_KEY
- full binary audio content
- sensitive personal data unless required
```

For debugging, it is acceptable to store:

```txt
- original audio file path
- converted wav file path
- recognized text
- raw Muxlisa JSON response for STT
```

But raw files should have retention policy.

---

# Suggested database fields

For voice messages:

```sql
telegram_file_id
telegram_file_unique_id
original_audio_path
converted_audio_path
audio_duration_sec
audio_size_bytes
stt_provider
stt_status
stt_text
stt_raw_response
stt_error
stt_started_at
stt_finished_at
```

For TTS:

```sql
tts_provider
tts_status
tts_text
tts_speaker
tts_audio_path
tts_error
tts_started_at
tts_finished_at
```

---

# User-facing errors

If audio is too long:

```txt
Голосовое сообщение слишком длинное. Пожалуйста, отправьте сообщение до 60 секунд.
```

If audio file is too large:

```txt
Файл слишком большой. Пожалуйста, отправьте голосовое сообщение короче или в другом формате.
```

If Muxlisa is temporarily unavailable:

```txt
Сейчас не получилось распознать голосовое сообщение. Пожалуйста, попробуйте ещё раз чуть позже.
```

If payment/quota issue occurs:

```txt
Сервис распознавания временно недоступен. Я уже передал информацию администратору.
```

---

# Security requirements

- Muxlisa API key must be stored only in environment variables.
- Do not commit real API key to GitHub.
- Add `.env` to `.gitignore`.
- Add safe placeholder to `.env.example`.
- API key must never be sent to frontend.
- API key must never be printed in logs.
- Muxlisa integration must run only on backend/worker side.

---

# Acceptance criteria

Implementation is complete when:

1. `.env.example` contains Muxlisa variables.
2. Backend reads `MUXLISA_BASE_URL` and `MUXLISA_API_KEY`.
3. Voice messages are processed through worker, not directly inside webhook.
4. Telegram OGG voice can be converted to WAV.
5. Audio duration limit of 60 seconds is enforced.
6. File size limit of 5 MB is enforced.
7. STT request is sent to `/api/v2/stt` as `multipart/form-data`.
8. TTS request is sent to `/api/v2/tts` as `application/json`.
9. TTS success response is handled as binary WAV.
10. 400, 402, 429, and 5XX errors are handled separately.
11. Retry is implemented only for 429, 5XX, and network timeout.
12. API key is not exposed in logs or client code.
13. Muxlisa logic is isolated behind a provider/interface.
14. Basic tests or mocks exist for successful STT, successful TTS, and error responses.
