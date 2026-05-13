"""FastAPI web app — Telegram Mini App frontend + API."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets as _secrets
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from sqlalchemy import func, select as sql_select

from .catalog import (
    BRAWL_PACKS,
    CATEGORIES,
    CLASH_PACKS,
    DEPOSIT_MAX_RUB,
    DEPOSIT_MIN_RUB,
    DEPOSIT_PRESETS_RUB,
    ROBUX_MAX,
    ROBUX_MIN,
    ROBUX_STEP,
    STARS_MAX,
    STARS_MIN,
    STARS_STEP,
    format_rub,
)
from .config import settings
from .db import CryptoInvoice, SessionLocal, User, WebSession, get_or_create_user, init_db
from .payments import (
    CryptoPayClient,
    CryptoPayError,
    parse_webhook_body,
    verify_webhook_signature,
)
from .services import (
    REFERRAL_BONUS,
    REFERRAL_PURCHASE_THRESHOLD,
    list_orders,
    place_order,
    preview_purchase,
    referral_stats,
    save_review,
)
from .telegram_auth import WebAppAuthError, parse_init_data

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    polling_task = await _maybe_start_bot()
    try:
        yield
    finally:
        if polling_task is not None:
            polling_task.cancel()
            with contextlib.suppress(Exception):
                await polling_task


app = FastAPI(title="Vavilon Market", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth dependency — validates Telegram WebApp initData OR a browser session.
# ---------------------------------------------------------------------------
async def require_user(
    x_init_data: str = Header(default="", alias="X-Init-Data"),
    x_web_session: str = Header(default="", alias="X-Web-Session"),
) -> Any:
    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="bot not configured")

    # Telegram Mini App authentication via initData.
    if x_init_data:
        try:
            tg_user = parse_init_data(x_init_data, settings.bot_token)
        except WebAppAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        async with SessionLocal() as session:
            user = await get_or_create_user(
                session,
                tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                ref_code=tg_user.start_param,
            )
            await session.commit()
            await session.refresh(user)
            return user

    # Browser session authentication via /api/web/login_start → bot confirm.
    if x_web_session:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    sql_select(WebSession).where(WebSession.token == x_web_session)
                )
            ).scalar_one_or_none()
            if row is None or row.status != "confirmed" or row.user_id is None:
                raise HTTPException(status_code=401, detail="web session invalid")
            user = await session.get(User, row.user_id)
            if user is None:
                raise HTTPException(status_code=401, detail="user not found")
            await session.refresh(user)
            return user

    raise HTTPException(status_code=401, detail="open this page from the Telegram bot or sign in")


def _user_payload(user) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "balance": str(user.balance.quantize(Decimal('0.01'))),
        "balance_pretty": format_rub(user.balance),
        "ref_code": user.ref_code,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "categories": CATEGORIES,
            "brawl_packs": BRAWL_PACKS,
            "clash_packs": CLASH_PACKS,
            "deposit_presets": DEPOSIT_PRESETS_RUB,
            "deposit_min": int(DEPOSIT_MIN_RUB),
            "deposit_max": int(DEPOSIT_MAX_RUB),
            "robux_step": ROBUX_STEP,
            "stars_step": STARS_STEP,
            "robux_min": ROBUX_MIN,
            "robux_max": ROBUX_MAX,
            "stars_min": STARS_MIN,
            "stars_max": STARS_MAX,
            "referral_threshold": int(REFERRAL_PURCHASE_THRESHOLD),
            "referral_bonus": int(REFERRAL_BONUS),
            "news_channel": settings.news_channel,
        },
    )


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Web-login (browser \u2192 confirm in Telegram bot) flow.
# ---------------------------------------------------------------------------
def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    return (request.client.host if request.client else "") or ""


class LoginStartRequest(BaseModel):
    username: str


@app.post("/api/web/login_start")
async def api_web_login_start(req: LoginStartRequest, request: Request) -> dict[str, Any]:
    bot = _get_bot()
    if bot is None:
        raise HTTPException(status_code=503, detail="bot not running")
    raw = req.username.strip().lstrip("@")
    if len(raw) < 3 or len(raw) > 64 or not all(c.isalnum() or c == "_" for c in raw):
        raise HTTPException(status_code=400, detail="\u043d\u0435\u0432\u0430\u043b\u0438\u0434\u043d\u044b\u0439 username")
    uname = raw.lower()

    async with SessionLocal() as session:
        target = (
            await session.execute(
                sql_select(User).where(func.lower(User.username) == uname)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(
                status_code=404,
                detail="\u041d\u0435 \u043d\u0430\u0448\u0451\u043b. \u041d\u0430\u0436\u043c\u0438 /start \u0431\u043e\u0442\u0443 @Vavilon_Shop_Bot \u0432 Telegram \u0438 \u043f\u043e\u0432\u0442\u043e\u0440\u0438.",
            )

        token = _secrets.token_urlsafe(32)
        ip = _client_ip(request)
        ua = (request.headers.get("user-agent") or "")[:255]
        ws = WebSession(
            token=token,
            username=uname,
            user_id=target.id,
            ip=ip,
            user_agent=ua,
            status="pending",
        )
        session.add(ws)
        await session.commit()

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    short_token = token[:24]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="\u2705 \u042d\u0442\u043e \u044f, \u0440\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u044c", callback_data=f"weblogin:ok:{short_token}"),
                InlineKeyboardButton(text="\u26d4\ufe0f \u041d\u0435 \u044f", callback_data=f"weblogin:no:{short_token}"),
            ]
        ]
    )
    ua_short = (ua[:80] + "\u2026") if len(ua) > 80 else ua
    try:
        await bot.send_message(
            target.id,
            (
                "\ud83d\udd10 <b>\u0412\u0445\u043e\u0434 \u043d\u0430 \u0441\u0430\u0439\u0442</b>\n\n"
                f"\u041a\u0442\u043e-\u0442\u043e \u043f\u044b\u0442\u0430\u0435\u0442\u0441\u044f \u0432\u043e\u0439\u0442\u0438 \u0432 vavilonmarket.onrender.com\n"
                f"\u041f\u043e\u0434 \u0442\u0432\u043e\u0438\u043c \u044e\u0437\u0435\u0440\u043d\u0435\u0439\u043c\u043e\u043c <code>@{raw}</code>.\n\n"
                f"IP: <code>{ip or '\u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u0435\u043d'}</code>\n"
                f"Browser: <code>{ua_short or '\u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u0435\u043d'}</code>\n\n"
                "\u0415\u0441\u043b\u0438 \u044d\u0442\u043e \u0442\u044b \u2014 \u0440\u0430\u0437\u0440\u0435\u0448\u0438. \u0418\u043d\u0430\u0447\u0435 \u043e\u0442\u043a\u043b\u043e\u043d\u0438."
            ),
            reply_markup=kb,
        )
    except Exception:
        logger.exception("Failed to send login confirmation to user %s", target.id)
        raise HTTPException(
            status_code=502,
            detail="\u041d\u0435 \u0441\u043c\u043e\u0433 \u043d\u0430\u043f\u0438\u0441\u0430\u0442\u044c \u0432 Telegram. \u0421\u043d\u0430\u0447\u0430\u043b\u0430 /start \u0443 \u0431\u043e\u0442\u0430.",
        )

    return {"token": token, "ip": ip}


@app.get("/api/web/login_status")
async def api_web_login_status(token: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        row = (
            await session.execute(sql_select(WebSession).where(WebSession.token == token))
        ).scalar_one_or_none()
        if row is None:
            return {"status": "missing"}
        return {"status": row.status}


@app.post("/api/web/logout")
async def api_web_logout(x_web_session: str = Header(default="", alias="X-Web-Session")) -> dict[str, Any]:
    if not x_web_session:
        return {"ok": True}
    async with SessionLocal() as session:
        row = (
            await session.execute(sql_select(WebSession).where(WebSession.token == x_web_session))
        ).scalar_one_or_none()
        if row is not None:
            row.status = "expired"
            await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/me")
async def api_me(user=Depends(require_user)) -> dict[str, Any]:
    return {"user": _user_payload(user)}


@app.get("/api/catalog")
async def api_catalog() -> dict[str, Any]:
    return {
        "categories": [
            {"key": c.key, "title": c.title, "emoji": c.emoji, "description": c.description}
            for c in CATEGORIES
        ],
        "brawl": [
            {"sku": p.sku, "title": p.title, "qty": p.qty, "price": str(p.price), "price_pretty": format_rub(p.price)}
            for p in BRAWL_PACKS
        ],
        "clash": [
            {"sku": p.sku, "title": p.title, "qty": p.qty, "price": str(p.price), "price_pretty": format_rub(p.price)}
            for p in CLASH_PACKS
        ],
    }


class PreviewRequest(BaseModel):
    sku: str
    quantity: int = 1


@app.post("/api/preview")
async def api_preview(req: PreviewRequest) -> dict[str, Any]:
    preview = preview_purchase(req.sku, req.quantity)
    if preview is None:
        raise HTTPException(status_code=400, detail="invalid sku/quantity")
    return {
        "sku": preview.sku,
        "title": preview.title,
        "quantity": preview.quantity,
        "price": str(preview.price),
        "price_pretty": format_rub(preview.price),
    }


class BuyRequest(BaseModel):
    sku: str
    quantity: int = 1
    target: str


@app.post("/api/buy")
async def api_buy(req: BuyRequest, user=Depends(require_user)) -> dict[str, Any]:
    target = req.target.strip()
    if len(target) < 2 or len(target) > 200:
        raise HTTPException(status_code=400, detail="invalid delivery target")
    preview = preview_purchase(req.sku, req.quantity)
    if preview is None:
        raise HTTPException(status_code=400, detail="invalid sku/quantity")

    async with SessionLocal() as session:
        db_user = await get_or_create_user(
            session,
            user.id,
            username=user.username,
            first_name=user.first_name,
        )
        order = await place_order(session, db_user, preview, target=target)
        if order is None:
            await session.commit()
            short = (preview.price - db_user.balance).quantize(Decimal("0.01"))
            return JSONResponse(
                {"ok": False, "error": "insufficient_balance", "short": str(short)},
                status_code=402,
            )
        await session.commit()
        new_balance = db_user.balance
        order_id = order.id

    # Notify the admin channel.
    await _notify_order_via_bot(order_id, preview, target, user)

    return {
        "ok": True,
        "order_id": order_id,
        "balance": str(new_balance.quantize(Decimal("0.01"))),
        "balance_pretty": format_rub(new_balance),
    }


@app.get("/api/orders")
async def api_orders(user=Depends(require_user)) -> dict[str, Any]:
    async with SessionLocal() as session:
        orders = await list_orders(session, user.id, limit=50)
        return {
            "orders": [
                {
                    "id": o.id,
                    "title": o.title,
                    "sku": o.sku,
                    "quantity": o.quantity,
                    "price": str(o.price),
                    "price_pretty": format_rub(o.price),
                    "target": o.target,
                    "status": o.status,
                    "created_at": o.created_at.isoformat(),
                    "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
                }
                for o in orders
            ]
        }


@app.get("/api/referral")
async def api_referral(user=Depends(require_user)) -> dict[str, Any]:
    async with SessionLocal() as session:
        count, earned = await referral_stats(session, user.id)
    bot_username = os.environ.get("BOT_USERNAME", "")
    link = (
        f"https://t.me/{bot_username}?start=ref_{user.ref_code}"
        if bot_username
        else f"start=ref_{user.ref_code}"
    )
    return {
        "code": user.ref_code,
        "link": link,
        "referrals": count,
        "earned": str(earned),
        "earned_pretty": format_rub(earned),
        "threshold": str(REFERRAL_PURCHASE_THRESHOLD),
        "bonus": str(REFERRAL_BONUS),
    }


class DepositRequest(BaseModel):
    amount: Decimal


@app.post("/api/deposit")
async def api_deposit(req: DepositRequest, user=Depends(require_user)) -> dict[str, Any]:
    if not settings.crypto_pay_enabled:
        raise HTTPException(status_code=503, detail="crypto deposits not configured")
    amount = req.amount.quantize(Decimal("0.01"))
    if amount < DEPOSIT_MIN_RUB or amount > DEPOSIT_MAX_RUB:
        raise HTTPException(status_code=400, detail="amount out of range")
    client = CryptoPayClient()
    try:
        invoice = await client.create_invoice_rub(
            amount_rub=amount,
            user_id=user.id,
            description=f"Пополнение баланса Vavilon Market на {amount:.0f}₽",
        )
    except CryptoPayError as exc:
        logger.error("CryptoPay error: %s", exc)
        raise HTTPException(status_code=502, detail="payment provider error") from exc

    async with SessionLocal() as session:
        session.add(
            CryptoInvoice(
                invoice_id=invoice.invoice_id,
                user_id=user.id,
                amount_rub=amount,
                pay_url=invoice.pay_url,
                status="active",
            )
        )
        await session.commit()

    return {
        "invoice_id": invoice.invoice_id,
        "pay_url": invoice.pay_url,
        "amount": str(amount),
        "amount_pretty": format_rub(amount),
    }


@app.post("/api/review")
async def api_review(
    rating: int = Form(...),
    text: str = Form(...),
    photo: UploadFile | None = File(default=None),
    order_id: int | None = Form(default=None),
    user=Depends(require_user),
) -> dict[str, Any]:
    if not text.strip():
        raise HTTPException(status_code=400, detail="text required")
    photo_file_id = None
    bot = _get_bot()
    if photo is not None and bot is not None:
        try:
            from aiogram.types import BufferedInputFile

            content = await photo.read()
            sent = await bot.send_photo(
                chat_id=user.id,
                photo=BufferedInputFile(content, filename=photo.filename or "review.jpg"),
                caption="Скрин к твоему отзыву (черновик)",
            )
            if sent.photo:
                photo_file_id = sent.photo[-1].file_id
        except Exception:  # pragma: no cover
            logger.exception("Failed to upload review photo")
    async with SessionLocal() as session:
        review = await save_review(
            session,
            user.id,
            rating=rating,
            text=text,
            order_id=order_id,
            photo_file_id=photo_file_id,
        )
        await session.commit()
        review_id = review.id

    # Forward to the news channel.
    if bot is not None and settings.news_channel:
        import html as _html

        handle = (
            f"@{user.username}" if user.username else _html.escape(user.first_name or "клиент")
        )
        caption = (
            f"⭐ <b>Отзыв от {handle}</b>\n"
            f"Оценка: {'⭐' * max(1, min(5, rating))}\n\n"
            f"{_html.escape(text.strip()[:2000])}"
        )
        try:
            if photo_file_id:
                await bot.send_photo(settings.news_channel, photo=photo_file_id, caption=caption)
            else:
                await bot.send_message(settings.news_channel, caption)
            async with SessionLocal() as session:
                from .db import Review

                rev = await session.get(Review, review_id)
                if rev:
                    rev.forwarded = 1
                    await session.commit()
        except Exception:  # pragma: no cover
            logger.exception("Failed to forward review")
    return {"ok": True, "review_id": review_id}


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------
@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default="", alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, Any]:
    bot = _get_bot()
    dp = _get_dispatcher()
    if bot is None or dp is None:
        raise HTTPException(status_code=503, detail="bot not running")
    if settings.webhook_secret and not (
        x_telegram_bot_api_secret_token
        and x_telegram_bot_api_secret_token == settings.webhook_secret
    ):
        raise HTTPException(status_code=401, detail="bad secret token")
    body = await request.json()
    update = Update.model_validate(body, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


# ---------------------------------------------------------------------------
# CryptoBot webhook
# ---------------------------------------------------------------------------
@app.post("/webhook/cryptobot")
async def cryptobot_webhook(request: Request) -> dict[str, Any]:
    if not settings.crypto_pay_enabled:
        raise HTTPException(status_code=503, detail="crypto pay not configured")
    body = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not verify_webhook_signature(settings.crypto_pay_token, body, signature):
        raise HTTPException(status_code=401, detail="bad signature")
    data = parse_webhook_body(body)
    if data.get("update_type") != "invoice_paid":
        return {"ok": True, "ignored": True}
    invoice = data.get("payload") or {}
    bot = _get_bot()
    if bot is None:
        return {"ok": False, "error": "bot not running"}
    from .bot import _credit_invoice

    credited = await _credit_invoice(bot, invoice)
    return {"ok": True, "credited": bool(credited)}


# ---------------------------------------------------------------------------
# Bot instance shared with the bot module
# ---------------------------------------------------------------------------
_bot_singleton: Bot | None = None
_dp_singleton: Dispatcher | None = None


def set_bot(bot: Bot, dp: Dispatcher | None = None) -> None:
    global _bot_singleton, _dp_singleton
    _bot_singleton = bot
    _dp_singleton = dp


def _get_bot() -> Bot | None:
    return _bot_singleton


def _get_dispatcher() -> Dispatcher | None:
    return _dp_singleton


async def _maybe_start_bot() -> asyncio.Task | None:
    """Spin up the bot inside the FastAPI lifespan (used when launched via uvicorn).

    Returns the polling task if running in polling mode so it can be cancelled on shutdown.
    """
    if _get_bot() is not None:
        return None  # already started by main.py
    if not settings.bot_token:
        logger.warning("BOT_TOKEN not set — bot disabled, web only")
        return None
    from .bot import make_bot, make_dispatcher  # local import to avoid cycle

    bot = make_bot()
    dp = make_dispatcher()
    set_bot(bot, dp)
    try:
        me = await bot.get_me()
        os.environ["BOT_USERNAME"] = me.username or ""
        logger.info("Bot @%s ready (mode=%s)", me.username, settings.effective_bot_mode)
    except Exception:
        logger.exception("Failed to initialize bot")
        return None

    if settings.effective_bot_mode == "webhook":
        if not settings.webhook_secret:
            settings.webhook_secret = _secrets.token_urlsafe(32)
        url = settings.telegram_webhook_url
        if not url:
            logger.error("Webhook mode requested but PUBLIC_URL is empty")
            return None
        try:
            await bot.set_webhook(
                url=url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=False,
                allowed_updates=dp.resolve_used_update_types(),
            )
            logger.info("Webhook set to %s", url)
        except Exception:
            logger.exception("Failed to set webhook")
        return None
    else:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            logger.exception("Failed to delete webhook")
        return asyncio.create_task(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            name="bot-polling",
        )


async def _notify_order_via_bot(order_id: int, preview, target: str, user) -> None:
    bot = _get_bot()
    if bot is None:
        return
    from .bot import _notify_admin_order

    class _U:
        def __init__(self, u):
            self.id = u.id
            self.username = u.username
            self.first_name = u.first_name

    await _notify_admin_order(bot, order_id, preview, target, _U(user))
