# OpenAI STT/TTS Integration Guide

## Goal

Integrate OpenAI audio APIs for:

1. Speech-to-Text (STT) — converting user voice messages into text.
2. Text-to-Speech (TTS) — converting bot text responses into spoken audio.

This integration must run only on the backend/worker side. Never expose `OPENAI_API_KEY` to frontend, Telegram Mini App, browser, or mobile client.

---

# Recommended models

## Speech-to-Text

Use this model by default:

```env
OPENAI_STT_MODEL=gpt-4o-transcribe
```

Reason:

- It is the best default option for high-quality transcription.
- It supports Russian transcription.
- It supports `prompt`, which can improve recognition of domain-specific words, names, acronyms, and mixed Russian/Uzbek terms.

Use this cheaper/faster fallback only if cost or latency becomes more important than quality:

```env
OPENAI_STT_MODEL=gpt-4o-mini-transcribe
```

Use this only for special cases:

```env
OPENAI_STT_MODEL=whisper-1
```

Use `whisper-1` if you specifically need:

- `srt`
- `vtt`
- `verbose_json`
- word-level timestamps

Do not use `gpt-4o-transcribe-diarize` for normal Telegram voice messages. Use it only for multi-speaker audio where speaker separation is required.

---

## Text-to-Speech

Use this model by default:

```env
OPENAI_TTS_MODEL=gpt-4o-mini-tts
```

Recommended voices to test first:

```env
OPENAI_TTS_VOICE=marin
OPENAI_TTS_FALLBACK_VOICE=cedar
```

Why:

- `gpt-4o-mini-tts` is the current recommended OpenAI TTS model.
- `marin` and `cedar` are recommended by OpenAI for best quality.
- Russian is supported, but OpenAI voices are still optimized primarily for English, so quality must be checked on real Russian phrases.

Important product decision:

Do not hardcode one voice forever. Add the voice to environment variables so we can switch between `marin`, `cedar`, `coral`, `nova`, etc. without changing code.

Recommended MVP choice:

```env
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=marin
OPENAI_TTS_RESPONSE_FORMAT=mp3
```

If the bot must send Telegram “voice message” bubbles instead of regular audio files, generate audio and convert it to Telegram-compatible OGG/Opus with `ffmpeg`.

---

# Environment variables

Use the following variables:

```env
# OpenAI
OPENAI_API_KEY=your_openai_api_key_here

# Optional. Usually not needed because the OpenAI SDK already knows the official API URL.
# Use only for proxies, custom gateways, or OpenAI-compatible endpoints.
OPENAI_BASE_URL=https://api.openai.com/v1

# Speech-to-Text
OPENAI_STT_MODEL=gpt-4o-transcribe
OPENAI_STT_LANGUAGE=ru
OPENAI_STT_RESPONSE_FORMAT=json
OPENAI_STT_TIMEOUT_MS=60000
OPENAI_STT_MAX_AUDIO_SIZE_MB=25
OPENAI_STT_PROMPT=Это голосовое сообщение пользователя на русском языке. Возможны слова, связанные со стоматологией, записью к врачу, клиникой, услугами, ценами, Telegram-ботом и Узбекистаном. Сохраняй естественную пунктуацию.

# Text-to-Speech
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=marin
OPENAI_TTS_FALLBACK_VOICE=cedar
OPENAI_TTS_RESPONSE_FORMAT=mp3
OPENAI_TTS_TIMEOUT_MS=60000
OPENAI_TTS_MAX_CHARS=4096
OPENAI_TTS_SPEED=1.0
OPENAI_TTS_INSTRUCTIONS=Говори естественно на русском языке. Интонация спокойная, дружелюбная и уверенная. Не звучать как диктор рекламы. Чётко произносить медицинские термины и названия услуг.
```

Required `.env.example` entry:

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1

OPENAI_STT_MODEL=gpt-4o-transcribe
OPENAI_STT_LANGUAGE=ru
OPENAI_STT_RESPONSE_FORMAT=json
OPENAI_STT_TIMEOUT_MS=60000
OPENAI_STT_MAX_AUDIO_SIZE_MB=25
OPENAI_STT_PROMPT=

OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=marin
OPENAI_TTS_FALLBACK_VOICE=cedar
OPENAI_TTS_RESPONSE_FORMAT=mp3
OPENAI_TTS_TIMEOUT_MS=60000
OPENAI_TTS_MAX_CHARS=4096
OPENAI_TTS_SPEED=1.0
OPENAI_TTS_INSTRUCTIONS=
```

Important:

- `OPENAI_API_KEY` is required.
- `OPENAI_BASE_URL` is optional. Do not require it unless the project uses a proxy or OpenAI-compatible gateway.
- Do not commit real `.env` to GitHub.
- Add `.env` to `.gitignore`.

---

# API endpoints

When using the official OpenAI Node SDK, do not manually build URLs.

The SDK methods are:

```ts
openai.audio.transcriptions.create(...)
openai.audio.speech.create(...)
```

Raw HTTP endpoints, for reference:

```txt
POST https://api.openai.com/v1/audio/transcriptions
POST https://api.openai.com/v1/audio/speech
```

---

# OpenAI package

Install the official SDK:

```bash
npm install openai
```

Create a single OpenAI client factory:

```ts
import OpenAI from 'openai';

export function createOpenAIClient() {
  const apiKey = process.env.OPENAI_API_KEY;

  if (!apiKey) {
    throw new Error('OPENAI_API_KEY is required');
  }

  const baseURL = process.env.OPENAI_BASE_URL || undefined;

  return new OpenAI({
    apiKey,
    baseURL,
    timeout: 60_000,
    maxRetries: 0,
  });
}
```

Do not enable SDK retries blindly. Use application-level retries so we can log and control retries consistently across Muxlisa, OpenAI, Telegram, and other providers.

---

# Recommended architecture

Do not call OpenAI STT/TTS directly inside Telegram webhook handling.

Correct flow:

```txt
Telegram webhook
  ↓
Save incoming update/message to PostgreSQL
  ↓
Create background job
  ↓
Worker downloads Telegram audio
  ↓
Worker validates/converts audio if needed
  ↓
Worker calls OpenAI STT
  ↓
Worker stores recognized text
  ↓
Worker sends text to LLM/business logic
  ↓
Worker optionally calls OpenAI TTS
  ↓
Worker sends text/audio response to user
```

The webhook handler must return `200 OK` quickly.

---

# Provider interface

The business logic must not depend directly on OpenAI methods.

Create a provider interface that can later support both OpenAI and Muxlisa:

```ts
export type SttProviderName = 'openai' | 'muxlisa';

export interface SpeechToTextResult {
  text: string;
  language?: string;
  durationSec?: number;
  provider: SttProviderName;
  model: string;
  raw?: unknown;
}

export interface TextToSpeechResult {
  audioBuffer: Buffer;
  mimeType: string;
  format: 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm';
  provider: 'openai' | 'muxlisa';
  model: string;
  voice?: string;
}

export interface SpeechProvider {
  transcribe(input: {
    filePath: string;
    language?: string;
    prompt?: string;
  }): Promise<SpeechToTextResult>;

  synthesize(input: {
    text: string;
    voice?: string;
    instructions?: string;
    responseFormat?: 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm';
    speed?: number;
  }): Promise<TextToSpeechResult>;
}
```

Implementation class:

```ts
export class OpenAISpeechProvider implements SpeechProvider {
  // Uses OPENAI_API_KEY, OPENAI_STT_MODEL, OPENAI_TTS_MODEL, etc.
}
```

---

# STT implementation

## Supported input formats

OpenAI transcription accepts common audio formats such as:

```txt
flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm
```

For Telegram voice messages, the original format is often OGG/Opus. OpenAI can accept `ogg`, so conversion is not always required.

Recommended MVP behavior:

1. Download Telegram voice/audio file.
2. Check file size.
3. If file size > `OPENAI_STT_MAX_AUDIO_SIZE_MB`, reject or compress/split.
4. Send the original file to OpenAI STT.
5. If transcription fails due to format, convert to WAV and retry once.

Optional conversion command:

```bash
ffmpeg -i input.ogg -ar 16000 -ac 1 -c:a pcm_s16le output.wav
```

---

## TypeScript STT example

```ts
import fs from 'node:fs';
import path from 'node:path';
import { createOpenAIClient } from './openaiClient';

export async function transcribeWithOpenAI(input: {
  filePath: string;
  language?: string;
  prompt?: string;
}) {
  const openai = createOpenAIClient();

  const model = process.env.OPENAI_STT_MODEL || 'gpt-4o-transcribe';
  const language = input.language || process.env.OPENAI_STT_LANGUAGE || 'ru';
  const prompt = input.prompt || process.env.OPENAI_STT_PROMPT || undefined;

  const result = await openai.audio.transcriptions.create({
    file: fs.createReadStream(input.filePath),
    model,
    language,
    prompt,
    response_format: 'json',
  });

  return {
    text: result.text?.trim() || '',
    language,
    provider: 'openai' as const,
    model,
    raw: result,
  };
}
```

Important:

- For `gpt-4o-transcribe` and `gpt-4o-mini-transcribe`, use `response_format: 'json'`.
- Use `language: 'ru'` when the expected input is Russian. This can improve accuracy and latency.
- Use `prompt` for domain context and recurring terms.
- Store raw response for debugging, but do not expose it to users.

---

# STT prompt recommendations

Use a short prompt in the same language as the audio.

Good default Russian STT prompt:

```txt
Это голосовое сообщение пользователя на русском языке. Возможны слова, связанные со стоматологией, записью к врачу, клиникой, услугами, ценами, Telegram-ботом, Узбекистаном, Ташкентом, русско-узбекской речью и именами людей. Сохраняй естественную пунктуацию.
```

If the bot is used for another domain, move domain vocabulary into config.

Examples of domain words that should be added later:

```txt
имплантация, брекеты, элайнеры, терапевт, ортодонт, хирург, кариес,
пульпит, чистка, консультация, снимок, КТ, рентген, Ташкент
```

---

# TTS implementation

## TTS input limits

OpenAI speech generation input has a maximum length of 4096 characters.

Rules:

1. If text length <= `OPENAI_TTS_MAX_CHARS`, synthesize directly.
2. If text is longer, split it into chunks.
3. Prefer splitting by paragraphs and sentences.
4. Never cut inside a word.
5. For MVP, if the response is too long, send text and synthesize only a short summary.

Recommended product behavior for Telegram:

```txt
Short answers:
- send text
- optionally send generated audio

Long answers:
- send text only
- or synthesize a short voice summary
```

Do not generate 5–10 voice messages for one long answer unless this is a deliberate UX decision.

---

## TypeScript TTS example

```ts
import { createOpenAIClient } from './openaiClient';

export async function synthesizeWithOpenAI(input: {
  text: string;
  voice?: string;
  instructions?: string;
  responseFormat?: 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm';
  speed?: number;
}) {
  const openai = createOpenAIClient();

  const model = process.env.OPENAI_TTS_MODEL || 'gpt-4o-mini-tts';
  const maxChars = Number(process.env.OPENAI_TTS_MAX_CHARS || 4096);

  if (input.text.length > maxChars) {
    throw new Error(`OpenAI TTS input is too long: ${input.text.length}/${maxChars}`);
  }

  const voice = input.voice || process.env.OPENAI_TTS_VOICE || 'marin';
  const responseFormat =
    input.responseFormat ||
    (process.env.OPENAI_TTS_RESPONSE_FORMAT as 'mp3' | 'opus' | 'aac' | 'flac' | 'wav' | 'pcm') ||
    'mp3';

  const speed = input.speed ?? Number(process.env.OPENAI_TTS_SPEED || 1.0);

  const response = await openai.audio.speech.create({
    model,
    voice,
    input: input.text,
    instructions:
      input.instructions ||
      process.env.OPENAI_TTS_INSTRUCTIONS ||
      'Говори естественно на русском языке. Интонация спокойная, дружелюбная и уверенная.',
    response_format: responseFormat,
    speed,
  });

  const audioBuffer = Buffer.from(await response.arrayBuffer());

  return {
    audioBuffer,
    mimeType: getMimeType(responseFormat),
    format: responseFormat,
    provider: 'openai' as const,
    model,
    voice,
  };
}

function getMimeType(format: string): string {
  switch (format) {
    case 'mp3':
      return 'audio/mpeg';
    case 'opus':
      return 'audio/opus';
    case 'aac':
      return 'audio/aac';
    case 'flac':
      return 'audio/flac';
    case 'wav':
      return 'audio/wav';
    case 'pcm':
      return 'audio/pcm';
    default:
      return 'application/octet-stream';
  }
}
```

---

# Voice quality testing for Russian

Because Russian voice quality is important, implement a small internal test script.

Create:

```txt
scripts/test-openai-tts-voices.ts
```

The script must generate samples for these voices:

```txt
marin
cedar
coral
nova
shimmer
onyx
```

Use this Russian test phrase:

```txt
Здравствуйте! Я помогу вам выбрать удобное время для записи к стоматологу. Расскажите, пожалуйста, что вас беспокоит: зубная боль, консультация, чистка, имплантация или брекеты?
```

Also test a phrase with numbers and medical terms:

```txt
Стоимость консультации — 150 000 сум. Ортодонт принимает во вторник с 10:30 до 18:00. Если нужно, я могу записать вас на компьютерную томографию.
```

The script must save files like:

```txt
tmp/tts-openai/marin.mp3
tmp/tts-openai/cedar.mp3
tmp/tts-openai/coral.mp3
```

After manual listening, set the best voice in production `.env`:

```env
OPENAI_TTS_VOICE=marin
```

---

# Telegram delivery strategy

## Option A — simplest MVP

Generate MP3 and send it as Telegram audio/document:

```env
OPENAI_TTS_RESPONSE_FORMAT=mp3
```

Pros:

- Simple.
- Good compatibility.
- No extra conversion.

Cons:

- Looks like an audio file, not a native Telegram voice bubble.

## Option B — voice message UX

Generate audio, convert to OGG/Opus, then send as Telegram voice.

Recommended flow:

```txt
OpenAI TTS mp3/wav
  ↓
ffmpeg conversion to ogg/opus
  ↓
Telegram sendVoice
```

Example:

```bash
ffmpeg -i input.mp3 -c:a libopus -b:a 32k -vbr on output.ogg
```

Use this if voice UX is important.

---

# Error handling

Handle these cases explicitly:

| Case | Behavior |
|---|---|
| Invalid API key / 401 | Do not retry. Notify admin. |
| Permission / 403 | Do not retry. Notify admin. |
| Bad request / 400 | Do not retry. Log request metadata. |
| File too large | Do not call OpenAI. Ask user to send a shorter message. |
| TTS text too long | Split text or send text-only fallback. |
| Rate limit / 429 | Retry with backoff. |
| Timeout | Retry with backoff. |
| 5XX | Retry with backoff. |

Recommended retry policy:

```txt
Attempt 1: immediately
Attempt 2: after 2 seconds
Attempt 3: after 5 seconds
```

Retry only for:

```txt
429
500
502
503
504
network timeout
temporary connection errors
```

Do not retry for:

```txt
400
401
403
file too large
unsupported file type
TTS input too long
```

---

# Logging rules

Log:

```txt
- provider: openai
- operation: stt / tts
- model
- voice
- response format
- request duration
- status code if available
- file size
- audio duration if known
- Telegram user id
- internal message id
- job id
```

Never log:

```txt
- OPENAI_API_KEY
- full audio binary content
- sensitive personal data unless required for debugging
```

Allowed for debugging:

```txt
- recognized text
- normalized user text
- path to stored audio file
- provider raw response without API keys
```

Apply retention policy to audio files.

---

# Suggested database fields

For STT:

```sql
stt_provider
stt_model
stt_language
stt_status
stt_text
stt_prompt
stt_raw_response
stt_error
stt_audio_path
stt_audio_size_bytes
stt_audio_duration_sec
stt_started_at
stt_finished_at
```

For TTS:

```sql
tts_provider
tts_model
tts_voice
tts_response_format
tts_status
tts_text
tts_instructions
tts_audio_path
tts_audio_size_bytes
tts_error
tts_started_at
tts_finished_at
```

---

# User-facing errors

If audio is too large:

```txt
Голосовое сообщение слишком большое. Пожалуйста, отправьте сообщение короче.
```

If audio cannot be recognized:

```txt
Не получилось распознать голосовое сообщение. Пожалуйста, попробуйте ещё раз или напишите текстом.
```

If OpenAI is temporarily unavailable:

```txt
Сейчас голосовая функция временно недоступна. Пожалуйста, попробуйте ещё раз чуть позже.
```

If TTS fails but text answer is available:

```txt
Я подготовил ответ текстом, но сейчас не смог озвучить его голосом.
```

---

# AI voice disclosure

The product must clearly disclose to users that generated voice responses are AI-generated.

Add one of these to onboarding, settings, or first audio response:

```txt
Голосовые ответы в этом боте сгенерированы искусственным интеллектом.
```

or:

```txt
Обратите внимание: голосовые сообщения бота озвучиваются ИИ, это не голос реального человека.
```

Do not hide this information.

---

# Security requirements

- Keep `OPENAI_API_KEY` only in backend/worker environment variables.
- Never expose the key to frontend.
- Never commit the key to GitHub.
- Keep `.env` in `.gitignore`.
- Add only placeholders to `.env.example`.
- Avoid logging raw request headers.
- Avoid logging complete user medical data unless strictly necessary.
- Treat audio messages as user data and apply retention policy.

---

# Acceptance criteria

Implementation is complete when:

1. `.env.example` contains OpenAI STT/TTS variables.
2. `OPENAI_API_KEY` is read only on backend/worker side.
3. OpenAI logic is isolated behind a provider/interface.
4. Telegram webhook does not call OpenAI directly.
5. Voice messages are processed by a background worker.
6. STT uses `gpt-4o-transcribe` by default.
7. STT sends `language: 'ru'` when Russian is expected.
8. STT supports prompt/context from env.
9. STT file size limit is enforced before API call.
10. TTS uses `gpt-4o-mini-tts` by default.
11. TTS voice is configurable through env.
12. TTS response format is configurable through env.
13. TTS checks the 4096-character input limit.
14. Long TTS text has a text-only or chunking fallback.
15. 400, 401, 403, 429, timeout, and 5XX errors are handled separately.
16. Retries are implemented only for rate limits, timeouts, and temporary server/network errors.
17. API key is never logged.
18. A local voice test script exists for comparing Russian speech quality across voices.
19. Generated audio can be sent to Telegram.
20. Users are clearly informed that voice responses are AI-generated.

---

# Sources for developer verification

Official OpenAI docs:

- Speech to text guide: https://developers.openai.com/api/docs/guides/speech-to-text
- Text to speech guide: https://developers.openai.com/api/docs/guides/text-to-speech
- Create transcription API reference: https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create
- Create speech API reference: https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create
