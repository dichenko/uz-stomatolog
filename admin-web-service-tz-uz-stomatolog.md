# ТЗ для Codex: админка `admin-uz-stomatolog.liven8n.site` в монорепо бота

## 1. Краткое описание задачи

Нужно поднять веб-сервис админки в том же монорепозитории, где находится Telegram-бот стоматологии.

Админка должна быть доступна по адресу:

```text
https://admin-uz-stomatolog.liven8n.site
```

Назначение админки — дать владельцу проекта возможность редактировать рабочие тексты и промпты бота без деплоя и без изменения кода.

Авторизация в админке — через Telegram Login / Telegram OpenID Connect.

Доступ разрешён только Telegram-пользователям, чьи `tg_id` перечислены в `.env`:

```env
TELEGRAM_ADMIN_IDS=111111111,222222222
```

Все админы имеют одинаковые права.

---

## 2. Важные продуктовые требования

### 2.1. Хранение данных

Все редактируемые тексты и промпты хранить в PostgreSQL.

Не хранить эти значения в `.env`, JSON-файлах внутри репозитория или статических файлах frontend-а.

Причина: админ должен менять тексты через интерфейс, а бот должен использовать новые значения сразу, без redeploy.

---

### 2.2. Мгновенное применение изменений

Бот должен читать актуальные значения из PostgreSQL при каждом обращении к соответствующему сценарию.

Требование:

- если админ изменил системный промпт, следующий LLM-вызов должен использовать новый промпт;
- если админ изменил приветственное сообщение, следующий пользователь после выбора языка должен получить новый текст;
- если админ изменил справочную информацию о клинике, следующий LLM-вызов должен использовать новую справку;
- если админ изменил TTS-промпт, следующий TTS-вызов должен использовать новое значение.

Кэширование в памяти для этих настроек в MVP не использовать.

---

### 2.3. Поведение при пустой базе / пустых значениях

Если в БД значение отсутствует или пустое:

- в админке показать пустое поле;
- при сохранении пустого значения сохранять пустую строку, а не `null`;
- бот не должен падать;
- для Telegram-сообщений бот не должен пытаться отправить пустое сообщение.

Правила для бота:

| Настройка | Если значение пустое |
|---|---|
| Системный промпт | Использовать пустой пользовательский системный промпт, но оставить технические runtime-инструкции приложения |
| Первое сообщение после выбора языка | Не отправлять пустое сообщение |
| TTS-промпт | Не передавать prompt/instructions в TTS-провайдер, если API это допускает |
| Справочная информация о клинике | Не добавлять блок справки в LLM-контекст |

---

## 3. Языки

Поддерживаемые языковые коды:

```text
ru
uz
en
```

В админке поля должны быть явно подписаны:

```text
Русский
Узбекский
Английский
```

Для узбекского языка в MVP использовать код:

```text
uz
```

Рекомендация по письменности:

- основная рекомендация для Узбекистана — узбекский на латинице;
- кириллица всё ещё широко встречается, особенно у старшей аудитории;
- для MVP не делать отдельную настройку `uz_latn` / `uz_cyrl`;
- в админке дать одно поле «Узбекский»;
- администратор сам вводит текст в той письменности, которую считает нужной;
- автоматическую транслитерацию не делать.

Для LLM-ответов при `language = "uz"` использовать узбекский язык. Если отдельная настройка письменности не добавлена, по умолчанию использовать современную узбекскую латиницу.

---

## 4. Ключевое требование по LLM-языку

Системный промпт в админке будет написан на русском языке.

Это нормально.

Но пользователь должен получать ответ на том языке, который он выбрал при входе в бота.

Поэтому в коде нельзя полагаться только на текст системного промпта из админки.

Нужно добавить неизменяемую runtime-инструкцию приложения, которая всегда передаётся в LLM вместе с пользовательским системным промптом:

```text
Пользователь выбрал язык: <ru|uz|en>.
Отвечай пользователю строго на выбранном языке.
Если выбран ru — отвечай на русском.
Если выбран uz — отвечай на узбекском.
Если выбран en — отвечай на английском.
Не меняй язык ответа, если пользователь явно не попросил изменить язык.
```

Эта runtime-инструкция не редактируется через админку и должна быть частью кода.

Итоговая сборка LLM-контекста должна быть примерно такой:

```text
[System message #1: технические неизменяемые правила приложения]
[System message #2: системный промпт из БД, написанный админом]
[System message #3: выбранный язык пользователя и требование отвечать на этом языке]
[System message #4: справочная информация о клинике из БД, если она не пустая]
[User message: сообщение пользователя]
```

---

## 5. Страницы админки

Админка должна иметь 4 страницы.

### 5.1. Страница 1 — «Системный промпт»

URL:

```text
/system-prompt
```

Назначение:

редактирование общего системного промпта для основного LLM-агента.

UI:

- заголовок: `Системный промпт`;
- большое текстовое поле / textarea;
- в textarea загружается текущее значение из БД;
- кнопка `Сохранить`;
- после успешного сохранения показать уведомление `Сохранено`;
- при ошибке показать понятную ошибку.

Требования:

- промпт один общий для всех языков;
- промпт может быть написан на русском;
- язык ответа пользователю определяется не этим промптом, а выбранным языком пользователя и runtime-инструкцией из раздела 4;
- кнопка «Проверить промпт» в MVP не нужна.

---

### 5.2. Страница 2 — «Первое сообщение после выбора языка»

URL:

```text
/welcome-messages
```

Назначение:

редактирование первого сообщения, которое бот отправляет пользователю после выбора языка.

UI:

три текстовых поля / textarea:

```text
Русский
Узбекский
Английский
```

Каждое поле соответствует языковому коду:

| Поле в UI | Код |
|---|---|
| Русский | `ru` |
| Узбекский | `uz` |
| Английский | `en` |

Кнопка:

```text
Сохранить
```

Поведение бота:

- если пользователь выбрал `ru`, отправить текст из `welcome_messages.ru`;
- если пользователь выбрал `uz`, отправить текст из `welcome_messages.uz`;
- если пользователь выбрал `en`, отправить текст из `welcome_messages.en`;
- если нужное поле пустое, не отправлять пустое сообщение.

---

### 5.3. Страница 3 — «Промпты для TTS»

URL:

```text
/tts-prompts
```

Назначение:

редактирование текстовых инструкций / промптов для TTS-моделей.

UI:

три текстовых поля / textarea:

```text
Русский
Узбекский
Английский
```

Каждое поле соответствует языковому коду:

| Поле в UI | Код |
|---|---|
| Русский | `ru` |
| Узбекский | `uz` |
| Английский | `en` |

Кнопка:

```text
Сохранить
```

Требования:

- в MVP не добавлять отдельные настройки голоса, скорости, модели или формата аудио;
- хранить только TTS-промпты / инструкции по языкам;
- если используемый TTS-провайдер поддерживает параметр `instructions` / `prompt`, передавать туда соответствующее значение;
- если конкретный TTS-провайдер не поддерживает prompt/instructions, сохранить структуру в БД и оставить интеграционную точку в коде;
- если TTS-промпт для языка пустой, не передавать пустую инструкцию в провайдер.

---

### 5.4. Страница 4 — «Справочная информация о клинике»

URL:

```text
/clinic-info
```

Назначение:

редактирование справочной информации о клинике, которую LLM использует при ответах пользователю.

UI:

- заголовок: `Справочная информация о клинике`;
- большое текстовое поле / textarea;
- в textarea загружается текущее значение из БД;
- кнопка `Сохранить`;
- после успешного сохранения показать уведомление `Сохранено`;
- при ошибке показать понятную ошибку.

Требования:

- в MVP справка — одно большое текстовое поле;
- не разбивать на разделы в UI;
- Markdown можно сохранять как обычный текст;
- не делать HTML-preview в MVP;
- при формировании LLM-контекста добавлять справку как отдельный блок;
- если справка написана на русском, LLM всё равно должен отвечать пользователю на выбранном языке.

---

## 6. Навигация и UI

### 6.1. Базовый layout

Админка должна иметь простой layout:

- боковое меню или верхнее меню;
- отображение текущего Telegram-пользователя;
- кнопка `Выйти`.

Меню:

```text
Системный промпт
Первое сообщение
Промпты TTS
Справка о клинике
```

### 6.2. UX при сохранении

После сохранения:

- показать toast/alert `Сохранено`;
- обновить состояние формы;
- не делать редирект.

При ошибке:

- показать текст ошибки;
- не терять введённые данные.

### 6.3. Несохранённые изменения

Для MVP можно не делать защиту от случайного закрытия страницы.

---

## 7. Авторизация через Telegram

### 7.1. Что использовать

Использовать официальный Telegram Login / Telegram OpenID Connect.

Официальная документация:

```text
https://core.telegram.org/bots/telegram-login
```

Telegram поддерживает OpenID Connect и Authorization Code Flow с PKCE.

Рекомендуемый для этой админки вариант:

```text
Backend-controlled OIDC Authorization Code Flow + PKCE
```

Почему:

- `Client Secret` остаётся только на backend;
- frontend не принимает решение о доступе;
- backend сам получает и проверяет `id_token`;
- проще защитить админку как обычный web-сервис.

---

### 7.2. Настройки в BotFather

В BotFather открыть:

```text
/mybots → нужный бот → Bot Settings → Web Login
```

Добавить Allowed URLs:

```text
https://admin-uz-stomatolog.liven8n.site
```

И callback URL:

```text
https://admin-uz-stomatolog.liven8n.site/auth/telegram/callback
```

BotFather выдаст:

```text
Client ID
Client Secret
```

Их нужно положить в `.env`.

Важно:

- настройка Web Login не ломает основную работу Telegram-бота;
- она не меняет webhook;
- не меняет команды;
- не меняет обработку сообщений;
- не мешает основной пользовательской логике бота.

---

### 7.3. Переменные окружения для авторизации

Добавить в `.env` сервиса админки:

```env
ADMIN_BASE_URL=https://admin-uz-stomatolog.liven8n.site

TELEGRAM_OIDC_CLIENT_ID=replace_with_client_id_from_botfather
TELEGRAM_OIDC_CLIENT_SECRET=replace_with_client_secret_from_botfather
TELEGRAM_OIDC_REDIRECT_URI=https://admin-uz-stomatolog.liven8n.site/auth/telegram/callback

TELEGRAM_ADMIN_IDS=111111111,222222222

SESSION_SECRET=replace_with_long_random_secret
SESSION_COOKIE_NAME=uz_stomatolog_admin_session
SESSION_COOKIE_MAX_AGE_DAYS=30
```

Также нужен доступ к PostgreSQL:

```env
DATABASE_URL=postgresql://user:password@postgres:5432/dbname
```

---

### 7.4. Auth routes

Реализовать routes:

```text
GET  /login
GET  /auth/telegram/start
GET  /auth/telegram/callback
POST /auth/logout
GET  /api/admin/me
```

#### GET `/login`

Показывает страницу входа.

На странице есть кнопка:

```text
Войти через Telegram
```

Кнопка ведёт на:

```text
/auth/telegram/start
```

#### GET `/auth/telegram/start`

Backend должен:

1. Сгенерировать `state`.
2. Сгенерировать `code_verifier`.
3. Сформировать `code_challenge` через SHA-256 + Base64URL.
4. Сохранить `state` и `code_verifier` в server-side session или защищённой HttpOnly cookie.
5. Сделать redirect на Telegram Authorization endpoint.

Authorization endpoint:

```text
https://oauth.telegram.org/auth
```

Параметры:

```text
client_id=<TELEGRAM_OIDC_CLIENT_ID>
redirect_uri=<TELEGRAM_OIDC_REDIRECT_URI>
response_type=code
scope=openid profile
state=<RANDOM_STATE>
code_challenge=<PKCE_CODE_CHALLENGE>
code_challenge_method=S256
```

Не запрашивать `phone`, если он не нужен.

#### GET `/auth/telegram/callback`

Telegram вернёт пользователя на:

```text
/auth/telegram/callback?code=...&state=...
```

Backend должен:

1. Проверить `state`.
2. Взять сохранённый `code_verifier`.
3. Обменять `code` на token.
4. Получить `id_token`.
5. Проверить `id_token`.
6. Достать Telegram ID.
7. Проверить Telegram ID по `TELEGRAM_ADMIN_IDS`.
8. Если ID разрешён — создать admin session.
9. Если ID запрещён — вернуть `403 Forbidden` или показать страницу отказа.
10. После успешного входа сделать redirect на `/`.

Token endpoint:

```text
https://oauth.telegram.org/token
```

Запрос на token endpoint должен идти server-side.

---

### 7.5. Проверка `id_token`

Нельзя доверять данным из `id_token`, пока он не проверен.

Проверить обязательно:

1. подпись JWT через Telegram JWKS;
2. `iss`;
3. `aud`;
4. `exp`.

JWKS endpoint:

```text
https://oauth.telegram.org/.well-known/jwks.json
```

Ожидаемый issuer:

```text
https://oauth.telegram.org
```

`aud` должен совпадать с:

```text
TELEGRAM_OIDC_CLIENT_ID
```

Telegram ID брать только из проверенного payload:

```ts
const tgId = String(payload.id);
```

---

### 7.6. Проверка админа

Функция:

```ts
export function getAdminTelegramIds(): Set<string> {
  return new Set(
    (process.env.TELEGRAM_ADMIN_IDS ?? "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean)
  );
}

export function isAdminTelegramId(tgId: string): boolean {
  return getAdminTelegramIds().has(String(tgId));
}
```

Если `tgId` не найден:

```text
403 Forbidden
```

Сессию не создавать.

---

### 7.7. Session cookie

После успешного входа создать server-side session.

В сессии хранить:

```ts
{
  tgId: string;
  username?: string;
  name?: string;
  picture?: string;
  role: "admin";
  loginAt: string;
}
```

Cookie:

```text
HttpOnly
Secure
SameSite=Lax
```

Срок жизни:

```text
30 days
```

В production:

```text
Secure=true
```

---

### 7.8. Защита routes

Все страницы админки и все API админки должны быть защищены.

Защитить:

```text
/
/system-prompt
/welcome-messages
/tts-prompts
/clinic-info
/api/admin/*
```

Не защищать:

```text
/login
/auth/telegram/start
/auth/telegram/callback
```

Middleware должен:

1. Проверить наличие admin session.
2. Проверить `role === "admin"`.
3. Повторно проверить, что `session.tgId` всё ещё есть в `TELEGRAM_ADMIN_IDS`.

Это важно: если удалить `tg_id` из `.env` и перезапустить сервис, старые сессии удалённого админа должны перестать работать.

---

## 8. PostgreSQL schema

### 8.1. Таблица настроек

Создать универсальную таблицу:

```sql
CREATE TABLE IF NOT EXISTS admin_settings (
  key TEXT PRIMARY KEY,
  value JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by_tg_id TEXT NULL
);
```

Начальные ключи:

```text
llm.system_prompt
bot.welcome_messages
tts.prompts
clinic.info
```

Начальные значения:

```sql
INSERT INTO admin_settings (key, value)
VALUES
  ('llm.system_prompt', '{"text": ""}'::jsonb),
  ('bot.welcome_messages', '{"ru": "", "uz": "", "en": ""}'::jsonb),
  ('tts.prompts', '{"ru": "", "uz": "", "en": ""}'::jsonb),
  ('clinic.info', '{"text": ""}'::jsonb)
ON CONFLICT (key) DO NOTHING;
```

---

### 8.2. Таблица аудита

Создать таблицу аудита изменений:

```sql
CREATE TABLE IF NOT EXISTS admin_audit_log (
  id BIGSERIAL PRIMARY KEY,
  admin_tg_id TEXT NOT NULL,
  action TEXT NOT NULL,
  setting_key TEXT NULL,
  old_value JSONB NULL,
  new_value JSONB NULL,
  ip_address TEXT NULL,
  user_agent TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Логировать:

- успешный вход;
- отказ во входе;
- logout;
- изменение каждой настройки.

---

## 9. API админки

Все endpoints ниже должны быть защищены admin middleware.

### 9.1. Получить текущего админа

```text
GET /api/admin/me
```

Response:

```json
{
  "tgId": "111111111",
  "username": "admin",
  "name": "Admin Name",
  "picture": "https://...",
  "role": "admin"
}
```

---

### 9.2. Получить все настройки

```text
GET /api/admin/settings
```

Response:

```json
{
  "systemPrompt": {
    "text": ""
  },
  "welcomeMessages": {
    "ru": "",
    "uz": "",
    "en": ""
  },
  "ttsPrompts": {
    "ru": "",
    "uz": "",
    "en": ""
  },
  "clinicInfo": {
    "text": ""
  }
}
```

---

### 9.3. Сохранить системный промпт

```text
PUT /api/admin/settings/system-prompt
```

Request:

```json
{
  "text": "..."
}
```

Validation:

- `text` должен быть строкой;
- максимальная длина: 80000 символов.

---

### 9.4. Сохранить первое сообщение

```text
PUT /api/admin/settings/welcome-messages
```

Request:

```json
{
  "ru": "...",
  "uz": "...",
  "en": "..."
}
```

Validation:

- все поля должны быть строками;
- допустимы пустые строки;
- максимальная длина каждого поля: 10000 символов.

---

### 9.5. Сохранить TTS-промпты

```text
PUT /api/admin/settings/tts-prompts
```

Request:

```json
{
  "ru": "...",
  "uz": "...",
  "en": "..."
}
```

Validation:

- все поля должны быть строками;
- допустимы пустые строки;
- максимальная длина каждого поля: 20000 символов.

---

### 9.6. Сохранить справочную информацию о клинике

```text
PUT /api/admin/settings/clinic-info
```

Request:

```json
{
  "text": "..."
}
```

Validation:

- `text` должен быть строкой;
- максимальная длина: 200000 символов.

---

## 10. Интеграция бота с настройками из БД

Нужно добавить в код бота слой чтения настроек из PostgreSQL.

Рекомендуемый интерфейс:

```ts
export type SupportedLanguage = "ru" | "uz" | "en";

export async function getSystemPrompt(): Promise<string>;

export async function getWelcomeMessage(language: SupportedLanguage): Promise<string>;

export async function getTtsPrompt(language: SupportedLanguage): Promise<string>;

export async function getClinicInfo(): Promise<string>;

export async function getBotRuntimeSettings(): Promise<{
  systemPrompt: string;
  clinicInfo: string;
  welcomeMessages: Record<SupportedLanguage, string>;
  ttsPrompts: Record<SupportedLanguage, string>;
}>;
```

Требования:

- читать из БД при каждом обращении к нужной настройке;
- если записи нет, вернуть пустую строку;
- если JSON повреждён или не содержит нужного поля, вернуть пустую строку и залогировать ошибку;
- не падать из-за пустой настройки.

---

### 10.1. Где использовать `getWelcomeMessage`

После того как пользователь выбрал язык:

```ts
const welcomeMessage = await getWelcomeMessage(user.language);

if (welcomeMessage.trim()) {
  await bot.sendMessage(chatId, welcomeMessage);
}
```

---

### 10.2. Где использовать `getSystemPrompt` и `getClinicInfo`

Перед каждым LLM-вызовом:

```ts
const systemPrompt = await getSystemPrompt();
const clinicInfo = await getClinicInfo();

const messages = [
  {
    role: "system",
    content: STATIC_TECHNICAL_SYSTEM_RULES,
  },
  {
    role: "system",
    content: systemPrompt,
  },
  {
    role: "system",
    content: buildLanguageInstruction(user.language),
  },
];

if (clinicInfo.trim()) {
  messages.push({
    role: "system",
    content: `Справочная информация о клинике:\n\n${clinicInfo}`,
  });
}

messages.push({
  role: "user",
  content: userMessage,
});
```

---

### 10.3. Где использовать `getTtsPrompt`

Перед TTS-вызовом:

```ts
const ttsPrompt = await getTtsPrompt(user.language);

await generateSpeech({
  text,
  language: user.language,
  instructions: ttsPrompt.trim() ? ttsPrompt : undefined,
});
```

Если текущий TTS-провайдер не поддерживает `instructions`, оставить параметр и адаптерный слой, но не ломать генерацию.

---

## 11. Рекомендуемая структура монорепозитория

Адаптировать под текущую структуру проекта.

Если текущая структура не противоречит, использовать:

```text
apps/
  bot/
    src/
      settings/
        runtimeSettings.ts

  admin-web/
    src/
      server/
        auth/
          telegramOidc.ts
          adminAccess.ts
          session.ts
        db/
          pool.ts
          settingsRepository.ts
          auditRepository.ts
        middleware/
          requireAdminSession.ts
          csrf.ts
        routes/
          auth.routes.ts
          admin.routes.ts
      client/
        pages/
          LoginPage.tsx
          SystemPromptPage.tsx
          WelcomeMessagesPage.tsx
          TtsPromptsPage.tsx
          ClinicInfoPage.tsx
        components/
          AdminLayout.tsx
          TextAreaEditor.tsx
          SaveButton.tsx

packages/
  shared/
    src/
      languages.ts
      settingsKeys.ts
```

Если в проекте уже есть общий backend, можно не создавать отдельный `admin-api`, а встроить API админки в существующий server app.

Главное требование:

```text
Админка должна быть в том же монорепо, деплоиться вместе с проектом и иметь доступ к той же PostgreSQL.
```

---

## 12. Docker / deploy

Добавить сервис админки в `docker-compose.yml`.

Пример:

```yaml
services:
  admin-web:
    build:
      context: .
      dockerfile: apps/admin-web/Dockerfile
    environment:
      NODE_ENV: production
      PORT: 3000
      ADMIN_BASE_URL: https://admin-uz-stomatolog.liven8n.site
      DATABASE_URL: ${DATABASE_URL}
      TELEGRAM_OIDC_CLIENT_ID: ${TELEGRAM_OIDC_CLIENT_ID}
      TELEGRAM_OIDC_CLIENT_SECRET: ${TELEGRAM_OIDC_CLIENT_SECRET}
      TELEGRAM_OIDC_REDIRECT_URI: https://admin-uz-stomatolog.liven8n.site/auth/telegram/callback
      TELEGRAM_ADMIN_IDS: ${TELEGRAM_ADMIN_IDS}
      SESSION_SECRET: ${SESSION_SECRET}
      SESSION_COOKIE_NAME: uz_stomatolog_admin_session
      SESSION_COOKIE_MAX_AGE_DAYS: 30
    expose:
      - "3000"
    restart: unless-stopped
```

Если PostgreSQL уже есть в compose, подключить `admin-web` к той же сети.

---

## 13. Caddy

На VPS уже используется Caddy.

Добавить host block:

```caddyfile
admin-uz-stomatolog.liven8n.site {
    reverse_proxy admin-web:3000
}
```

Если Caddy запущен не в той же Docker-сети, использовать доступный upstream по текущей инфраструктуре проекта, например:

```caddyfile
admin-uz-stomatolog.liven8n.site {
    reverse_proxy 127.0.0.1:3000
}
```

Выбрать вариант, соответствующий текущему deployment setup.

---

## 14. Безопасность

Обязательные требования:

1. Не принимать `tg_id` с frontend.
2. Не хранить список админов во frontend.
3. Не отдавать `TELEGRAM_OIDC_CLIENT_SECRET` во frontend.
4. Проверять `id_token` только на backend.
5. Проверять подпись JWT через JWKS.
6. Проверять `iss`, `aud`, `exp`.
7. Проверять `state` в OIDC flow.
8. Использовать PKCE.
9. Все admin API защищать session middleware.
10. Все write endpoints защищать от CSRF.

### 14.1. CSRF

Так как используется cookie-based session, для `PUT` / `POST` endpoints добавить CSRF-защиту.

Варианты:

- double-submit CSRF token;
- server-side CSRF token в session;
- готовый middleware, если он совместим со стеком проекта.

Минимум:

- `SameSite=Lax`;
- `HttpOnly`;
- `Secure`;
- CSRF token для write-запросов.

### 14.2. Body limits

Ограничить размер JSON body.

Рекомендация:

```text
1 MB
```

### 14.3. Headers

Не ставить header:

```text
Cross-Origin-Opener-Policy: same-origin
```

если используется Telegram Login JS popup.

Если используется backend OIDC redirect flow, это ограничение не критично.

---

## 15. Логирование

Логировать события:

```text
admin_login_success
admin_login_forbidden
admin_logout
admin_setting_updated
admin_auth_error
admin_settings_read_error
```

Пример:

```json
{
  "event": "admin_setting_updated",
  "adminTgId": "111111111",
  "settingKey": "llm.system_prompt",
  "createdAt": "2026-05-19T00:00:00.000Z"
}
```

Не логировать:

- `id_token`;
- `access_token`;
- `client_secret`;
- session cookie;
- полный текст промпта, если он может содержать чувствительные данные.

Полные старые и новые значения можно хранить в `admin_audit_log`, но не писать в обычные stdout-логи контейнера.

---

## 16. Что не входит в MVP

Не делать в первой версии:

- роли и права;
- визуальный редактор;
- Markdown preview;
- историю изменений в UI;
- кнопку «Проверить промпт»;
- автогенерацию промптов;
- настройку TTS voice/speed/model;
- отдельную письменность `uz_latn` / `uz_cyrl`;
- автоматическую транслитерацию узбекского;
- загрузку файлов;
- RAG по документам;
- мультиязычную справку отдельными полями.

---

## 17. Acceptance Criteria

### 17.1. Деплой

- [ ] Админка доступна по `https://admin-uz-stomatolog.liven8n.site`.
- [ ] Сервис админки находится в том же монорепо, что и бот.
- [ ] Сервис админки добавлен в Docker/deploy-конфигурацию.
- [ ] Caddy проксирует домен админки на сервис админки.
- [ ] Админка подключена к PostgreSQL.

### 17.2. Авторизация

- [ ] Неавторизованный пользователь видит страницу login.
- [ ] Вход работает через Telegram Login / Telegram OIDC.
- [ ] `Client Secret` не попадает во frontend.
- [ ] Backend проверяет `id_token` через JWKS.
- [ ] Backend проверяет `iss`, `aud`, `exp`.
- [ ] Доступ получают только `tg_id` из `TELEGRAM_ADMIN_IDS`.
- [ ] Пользователь не из `TELEGRAM_ADMIN_IDS` получает отказ.
- [ ] Logout работает.
- [ ] После удаления `tg_id` из `.env` и перезапуска сервиса старый пользователь теряет доступ.

### 17.3. Страницы

- [ ] Есть страница «Системный промпт».
- [ ] Есть страница «Первое сообщение».
- [ ] Есть страница «Промпты TTS».
- [ ] Есть страница «Справка о клинике».
- [ ] Все страницы загружают текущие данные из PostgreSQL.
- [ ] Все страницы сохраняют изменения в PostgreSQL.
- [ ] После сохранения видно уведомление `Сохранено`.
- [ ] Ошибки сохранения показываются пользователю.

### 17.4. Интеграция с ботом

- [ ] Бот читает системный промпт из БД при каждом LLM-вызове.
- [ ] Бот читает справочную информацию о клинике из БД при каждом LLM-вызове.
- [ ] Бот читает welcome message из БД после выбора языка.
- [ ] Бот читает TTS prompt из БД перед TTS-вызовом.
- [ ] Изменения применяются без redeploy.
- [ ] Пустые значения не ломают бота.
- [ ] Бот не отправляет пустые Telegram-сообщения.
- [ ] Пользователь получает LLM-ответ на выбранном языке, даже если системный промпт написан на русском.

### 17.5. Аудит и безопасность

- [ ] Изменения настроек пишутся в `admin_audit_log`.
- [ ] Успешные входы пишутся в аудит.
- [ ] Запрещённые входы пишутся в аудит.
- [ ] Write endpoints защищены от CSRF.
- [ ] Секреты и токены не пишутся в логи.
- [ ] Все `/api/admin/*` защищены middleware.

---

## 18. Справка для разработчика: Telegram Login / OIDC

Официальная документация:

```text
https://core.telegram.org/bots/telegram-login
```

Telegram OIDC endpoints:

```text
Discovery:
https://oauth.telegram.org/.well-known/openid-configuration

Authorization:
https://oauth.telegram.org/auth

Token:
https://oauth.telegram.org/token

JWKS:
https://oauth.telegram.org/.well-known/jwks.json
```

Telegram BotFather setup:

```text
@BotFather → /mybots → нужный бот → Bot Settings → Web Login
```

Allowed URLs для этого проекта:

```text
https://admin-uz-stomatolog.liven8n.site
https://admin-uz-stomatolog.liven8n.site/auth/telegram/callback
```

OIDC client config:

```text
Client ID: from BotFather
Client Secret: from BotFather
Response Type: code
PKCE: S256
Scopes: openid profile
```

Проверка `id_token`:

```text
1. Fetch JWKS from https://oauth.telegram.org/.well-known/jwks.json
2. Verify JWT signature
3. Verify iss === https://oauth.telegram.org
4. Verify aud === TELEGRAM_OIDC_CLIENT_ID
5. Verify exp is not expired
6. Read Telegram user id from verified payload
7. Check this id against TELEGRAM_ADMIN_IDS
```

---

## 19. Финальная инструкция для Codex

Implement the admin web service inside the existing monorepo.

The implementation must follow the existing repository conventions where possible. If the repository already has a preferred server framework, database layer, migration tool, logger, or Docker pattern, reuse it.

Main goal:

```text
Create a secure admin panel at https://admin-uz-stomatolog.liven8n.site
that allows Telegram-authorized admins to edit bot prompts and clinic info stored in PostgreSQL.
```

Do not hardcode prompts in the source code. Do not require redeploy after editing prompts. The bot must read the current values from PostgreSQL when it needs them.

Use Telegram OIDC Authorization Code Flow with PKCE for authentication. Only Telegram IDs from `TELEGRAM_ADMIN_IDS` are allowed.

Implement database migrations, admin API routes, UI pages, bot-side settings reader, Docker/deploy integration, Caddy instructions, audit logging, and acceptance tests or manual verification notes.
