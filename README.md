# Vavilon Market

Telegram бот + Mini App для продажи цифровых товаров:

- **Robux** — 100 = 80₽, шаг 100, до 10000
- **Telegram Stars** — 100 = 150₽, шаг 100, до 10000
- **Brawl Stars гемы** — паки x2 от цены официального магазина
- **Clash Royale гемы** — паки x2 от цены официального магазина

## Возможности

- Балaнс пользователя + пополнение через @CryptoBot (Visa/MC/SBP/крипта)
- Реферальная система — каждый юзер получает код, +10₽ за каждого друга купившего от 100₽
- История заказов
- Отзывы со скриншотом → автоматический форвард в `@vavilonmarketnews`
- Mini App в Telegram + полнофункциональный бот

## Архитектура

- **Бот**: aiogram 3.x в режиме webhook (через FastAPI endpoint)
- **Web/API**: FastAPI + Jinja2 + Telegram WebApp init_data авторизация
- **БД**: SQLite через async SQLAlchemy
- **Платежи**: Crypto Pay API (@CryptoBot)
- Один процесс (uvicorn) держит и web и бота

## Запуск

См. [SETUP.md](SETUP.md).

## Переменные окружения

| Имя | Назначение |
|---|---|
| `BOT_TOKEN` | Токен от @BotFather (обязателен) |
| `ADMIN_USER_ID` | Telegram user id админа (опционально — первый юзер становится админом автоматом) |
| `NEWS_CHANNEL` | Канал для заказов и отзывов |
| `CRYPTO_PAY_TOKEN` | Токен Crypto Pay |
| `PUBLIC_URL` | Внешний URL (для Mini App и webhook) |
| `BOT_MODE` | `webhook` (для продакшна) или `polling` |
| `PORT` | Внутренний порт (HF Spaces = 7860) |
