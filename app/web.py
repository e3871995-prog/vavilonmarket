"""FastAPI web app — Telegram Mini App frontend + API."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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
from .db import CryptoInvoice, SessionLocal, get_or_create_user, init_db
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
    yield


app = FastAPI(title="Vavilon Market", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Auth dependency — validates Telegram WebApp initData passed via header.
# ---------------------------------------------------------------------------
async def require_user(x_init_data: str = Header(default="", alias="X-Init-Data")) -> Any:
    if not settings.bot_token:
        raise HTTPException(status_code=500, detail="bot not configured")
    if not x_init_data:
        raise HTTPException(status_code=401, detail="open this page from the Telegram bot")
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
        # Detach from session before returning
        await session.refresh(user)
        return user


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
        "index.html",
        {
            "request": request,
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


def set_bot(bot: Bot) -> None:
    global _bot_singleton
    _bot_singleton = bot


def _get_bot() -> Bot | None:
    return _bot_singleton


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
