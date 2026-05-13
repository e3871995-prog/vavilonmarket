# Vavilon Market

Магазин цифровых товаров (Robux, Telegram Stars, Brawl Stars, Clash Royale) — Telegram-бот + Mini App в одном процессе.

## Что внутри

- **Telegram-бот** (aiogram 3) — каталог, баланс, пополнение через CryptoBot, заказы, реферальная программа, отзывы.
- **Mini App** (FastAPI + Jinja + vanilla JS) — открывается из бота кнопкой "🌐 Открыть магазин", авторизуется по Telegram WebApp `initData`.
- **SQLite** (через SQLAlchemy + aiosqlite) — общая база у бота и сайта.

## Цены

- Robux — 80₽ за 100 (крутилка по 100).
- Telegram Stars — 150₽ за 100 (крутилка по 100).
- Brawl Stars / Clash Royale — фиксированные паки, цена = `официальная цена × 2`.

## Реферальная программа

- Каждый юзер получает 8-символьный реферальный код.
- Когда приглашённый делает покупку ≥ 100₽, рефереру капает +10₽ на баланс.

## Доставка

После оформления заказ улетает админу в канал (`NEWS_CHANNEL`). Админ присылает товар вручную (звёзды — как подарок по @username; робуксы/гемы — через свои аккаунты) и нажимает «Выдано» — клиент получает уведомление.

## Запуск локально

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
cp .env.example .env
# заполни BOT_TOKEN, ADMIN_USER_ID, CRYPTO_PAY_TOKEN, PUBLIC_URL
python -m app
```

## Деплой на Fly.io

Бот и веб-сервер запускаются одной командой `python -m app`. На Fly это нужно прокинуть как процесс с открытым портом `8080`.

```bash
fly launch --no-deploy
fly secrets set BOT_TOKEN=... CRYPTO_PAY_TOKEN=... ADMIN_USER_ID=... NEWS_CHANNEL=@vavilonmarketnews PUBLIC_URL=https://<app>.fly.dev
fly deploy
```

Crypto Pay webhook: `https://<app>.fly.dev/webhook/cryptobot` — добавь его в @CryptoBot → Crypto Pay → My Apps → Webhooks.

## Структура

```
app/
  config.py        — настройки (BOT_TOKEN, CRYPTO_PAY_TOKEN, …)
  db.py            — модели SQLAlchemy
  catalog.py       — цены и SKU
  services.py      — бизнес-логика (покупки, рефералка, отзывы)
  payments.py      — клиент CryptoBot Crypto Pay
  telegram_auth.py — валидация WebApp initData
  bot.py           — Telegram-бот
  web.py           — FastAPI
  main.py          — entrypoint (бот + веб в одном процессе)
  templates/index.html
  static/style.css
  static/app.js
```
