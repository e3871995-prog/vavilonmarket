# Гайд по запуску Vavilon Market

## Что нужно подготовить

1. **Telegram бот** — создаётся в [@BotFather](https://t.me/BotFather):
   - `/newbot` → задать имя и username → скопировать токен.
   - Если токен уже есть: `/token` → пересоздать токен (старый перестанет работать, это нормально, если он засвечен в чате).
   - В настройках бота включи Mini App: `/setdomain` (для будущего домена), `/setmenubutton` — кнопка меню.
2. **CryptoBot для приёма крипты** — [@CryptoBot](https://t.me/CryptoBot):
   - В чате с ботом: `Crypto Pay` → `Create App` → задаёшь имя → копируешь **API token**.
3. **Канал для уведомлений** — `@vavilonmarketnews` (или любой другой):
   - Создай канал, добавь твоего бота админом с правом «Публикация сообщений» (в Telegram: настройки канала → Администраторы → Добавить администратора → выбрать бота).
   - Имя канала (например `@vavilonmarketnews`) пойдёт в env-переменную `NEWS_CHANNEL`.
4. **Твой Telegram user ID** — узнать у [@userinfobot](https://t.me/userinfobot).

## Локальный запуск (для теста)

```bash
git clone <repo>
cd vavilonmarket
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env
# открой .env и заполни:
#   BOT_TOKEN=<токен от BotFather>
#   ADMIN_USER_ID=<твой Telegram user id>
#   NEWS_CHANNEL=@vavilonmarketnews
#   CRYPTO_PAY_TOKEN=<токен от @CryptoBot>
python -m app
```

Бот сразу заработает в режиме long polling. Mini App будет доступен только если задать `PUBLIC_URL` (см. ниже).

## Деплой на бесплатный хостинг (Railway)

Railway — самый простой бесплатный вариант для Python с Docker.

1. Зарегайся на [railway.app](https://railway.app) (вход через GitHub — нужен аккаунт GitHub).
2. Залей этот код в свой репозиторий на GitHub (можно через web-интерфейс: New repository → Upload files).
3. В Railway: `New Project` → `Deploy from GitHub repo` → выбери репозиторий.
4. В разделе **Variables** добавь:
   - `BOT_TOKEN`
   - `ADMIN_USER_ID`
   - `NEWS_CHANNEL=@vavilonmarketnews`
   - `CRYPTO_PAY_TOKEN`
   - `PUBLIC_URL` — Railway даст URL вида `https://<name>.up.railway.app`. Заполни им после первого деплоя.
   - `PORT=8080`
   - `BOT_MODE=webhook`
5. В разделе **Settings** → **Generate Domain** → копируй URL и засунь в `PUBLIC_URL`.
6. Передеплой (Railway сделает это автоматически после изменения переменных).

После деплоя:
- Бот работает 24/7.
- Mini App доступен по `PUBLIC_URL`.
- Crypto Pay вебхук: добавь URL `https://<domain>/webhook/cryptobot` в [@CryptoBot](https://t.me/CryptoBot) → Crypto Pay → My Apps → выбрать приложение → Webhooks.
- Привяжи Mini App к боту: [@BotFather](https://t.me/BotFather) → `/mybots` → выбрать бота → `Bot Settings` → `Menu Button` → `Configure menu button` → задать `🛒 Магазин` и URL `https://<domain>/`.

## Деплой на Render (альтернатива Railway)

1. [render.com](https://render.com) → New → Web Service → Connect a repository.
2. Build Command: `pip install -e .`
3. Start Command: `python -m app`
4. Plan: **Free**.
5. В Environment добавь те же переменные, что и для Railway.

## Что в каждой переменной

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather. **Обязательная.** |
| `ADMIN_USER_ID` | Твой Telegram user id (число). Без него кнопки «Выдано/Отменить» в админ-канале работают только если ты единственный кто их видит. |
| `NEWS_CHANNEL` | Куда летят новые заказы и отзывы. Бот должен быть в нём админом. |
| `CRYPTO_PAY_TOKEN` | Токен Crypto Pay. Без него пополнения отключены. |
| `PUBLIC_URL` | URL хостинга (для Mini App и Telegram webhook). |
| `PORT` | Порт HTTP. По умолчанию 8080. |
| `BOT_MODE` | `auto` (default), `webhook` или `polling`. На хостинге выбирай `webhook`. |
| `WEBHOOK_SECRET` | Секрет для проверки `X-Telegram-Bot-Api-Secret-Token`. Если пусто — сгенерируется автоматически. |

## Mini App в @BotFather

После того как у тебя появится `PUBLIC_URL`:

1. `/mybots` → выбрать бота → `Bot Settings` → `Menu Button`.
2. `Configure menu button`.
3. Текст: `🛒 Магазин`. URL: `https://<твой-домен>/`.
4. Готово — у клиента в чате с ботом снизу появится синяя кнопка «🛒 Магазин», она открывает сайт прямо в Telegram.

## Безопасность

- Никогда не публикуй `BOT_TOKEN` и `CRYPTO_PAY_TOKEN` нигде, где их могут увидеть посторонние (чаты, скриншоты, GitHub).
- Если токен где-то засветился — пересоздай его в @BotFather (`/token`) или в Crypto Pay (revoke + create new app).
- SQLite в файле `data/vavilon.db` хранит все балансы и заказы — на Railway/Render бэкапь его периодически (или мигрируй на PostgreSQL, см. `DATABASE_URL`).
