"""Telegram bot — main UI lives here.

We deliberately use one "send a new bubble + delete previous" pattern instead of
editing the prior message. The previous version of this bot tried to call
`message.edit_text` after sending photos, which is invalid on photo messages and
caused the product buttons to silently fail.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from .catalog import (
    BRAWL_PACKS,
    CATEGORIES,
    DEPOSIT_MAX_RUB,
    DEPOSIT_MIN_RUB,
    DEPOSIT_PRESETS_RUB,
    ROBUX_MAX,
    ROBUX_MIN,
    ROBUX_STEP,
    STARS_MAX,
    STARS_MIN,
    STARS_STEP,
    category_packs,
    format_rub,
    get_pack,
    robux_price,
    stars_price,
)
from .config import settings
from .db import CryptoInvoice, Order, SessionLocal, User, get_or_create_user
from .payments import CryptoPayClient, CryptoPayError
from .services import (
    REFERRAL_BONUS,
    REFERRAL_PURCHASE_THRESHOLD,
    credit_balance,
    list_orders,
    place_order,
    preview_purchase,
    referral_stats,
    save_review,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FSM states
# ---------------------------------------------------------------------------
class BuyFlow(StatesGroup):
    waiting_for_qty = State()       # robux / stars custom quantity
    waiting_for_target = State()    # username / game tag


class TopUpFlow(StatesGroup):
    waiting_for_amount = State()


class ReviewFlow(StatesGroup):
    waiting_for_rating = State()
    waiting_for_text = State()
    waiting_for_photo = State()


# ---------------------------------------------------------------------------
# Reply keyboards
# ---------------------------------------------------------------------------
def main_menu_kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🛒 Каталог"), KeyboardButton(text="💰 Баланс")],
        [KeyboardButton(text="📦 Мои заказы"), KeyboardButton(text="👥 Рефералка")],
        [KeyboardButton(text="⭐ Оставить отзыв"), KeyboardButton(text="ℹ️ Помощь")],
    ]
    if settings.webapp_url:
        rows.insert(
            0,
            [
                KeyboardButton(
                    text="🌐 Открыть магазин",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ],
        )
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------
def catalog_root_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for cat in CATEGORIES:
        rows.append(
            [InlineKeyboardButton(text=f"{cat.emoji} {cat.title}", callback_data=f"cat:{cat.key}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def linear_kb(prefix: str, qty: int, step: int, lo: int, hi: int) -> InlineKeyboardMarkup:
    """Spinner for robux/stars."""
    qty = max(lo, min(hi, qty))
    rows = [
        [
            InlineKeyboardButton(text="−", callback_data=f"{prefix}:dec:{qty}"),
            InlineKeyboardButton(text=f"{qty}", callback_data=f"{prefix}:noop"),
            InlineKeyboardButton(text="+", callback_data=f"{prefix}:inc:{qty}"),
        ],
        [
            InlineKeyboardButton(text=f"−{step * 5}", callback_data=f"{prefix}:dec5:{qty}"),
            InlineKeyboardButton(text="✏️ Ввести", callback_data=f"{prefix}:custom"),
            InlineKeyboardButton(text=f"+{step * 5}", callback_data=f"{prefix}:inc5:{qty}"),
        ],
        [InlineKeyboardButton(text="✅ Купить", callback_data=f"{prefix}:buy:{qty}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="cat:root")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def packs_kb(category: str) -> InlineKeyboardMarkup:
    packs = category_packs(category)
    rows: list[list[InlineKeyboardButton]] = []
    for pack in packs:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{pack.title} — {format_rub(pack.price)}",
                    callback_data=f"pack:{pack.sku}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="cat:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pack_card_kb(sku: str) -> InlineKeyboardMarkup:
    pack = get_pack(sku)
    if pack is None:
        return InlineKeyboardMarkup(inline_keyboard=[])
    category = "brawl" if pack in BRAWL_PACKS else "clash"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Купить", callback_data=f"pack:buy:{sku}")],
            [InlineKeyboardButton(text="⬅️ К наборам", callback_data=f"cat:{category}")],
        ]
    )


def deposit_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for amount in DEPOSIT_PRESETS_RUB:
        row.append(InlineKeyboardButton(text=f"{amount}₽", callback_data=f"dep:{amount}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="✏️ Своя сумма", callback_data="dep:custom")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bal:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(target: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=target)]]
    )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def hello_text(user: User) -> str:
    name = html.escape(user.first_name or "друг")
    return (
        f"Привет, {name}! 👋\n\n"
        "Это <b>Vavilon Market</b> — Robux, Brawl Stars, Clash Royale и Telegram Stars.\n\n"
        f"Баланс: <b>{format_rub(user.balance)}</b>\n"
        f"Реф-код: <code>{user.ref_code}</code>"
    )


def linear_text(kind: str, qty: int) -> str:
    if kind == "robux":
        return (
            f"<b>🟢 Robux</b>\n\n"
            f"100 робуксов = 80₽\n\n"
            f"Количество: <b>{qty}</b>\n"
            f"К оплате: <b>{format_rub(robux_price(qty))}</b>"
        )
    return (
        f"<b>⭐ Telegram Stars</b>\n\n"
        f"100 звёзд = 150₽\n\n"
        f"Количество: <b>{qty}</b>\n"
        f"К оплате: <b>{format_rub(stars_price(qty))}</b>"
    )


def pack_card_text(sku: str) -> str:
    pack = get_pack(sku)
    if pack is None:
        return "Товар не найден."
    return (
        f"<b>{html.escape(pack.title)}</b>\n\n"
        f"Цена: <b>{format_rub(pack.price)}</b>\n"
        "Оплата с баланса. После заказа админ зачислит валюту вручную."
    )


# ---------------------------------------------------------------------------
# Helpers — answer/edit pattern that works on photo and text messages alike.
# ---------------------------------------------------------------------------
async def safe_edit_or_send(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit current message if it's plain text, otherwise delete and re-send."""
    try:
        if message.text is not None:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        await message.edit_caption(caption=text, reply_markup=reply_markup)
    except TelegramBadRequest:
        with contextlib.suppress(Exception):
            await message.delete()
        await message.answer(text, reply_markup=reply_markup)


# ---------------------------------------------------------------------------
# Router and handlers
# ---------------------------------------------------------------------------
router = Router(name=__name__)


@router.message(CommandStart())
async def cmd_start(message: Message, command: Any) -> None:
    if message.from_user is None:
        return
    ref_code = None
    args = (command.args or "").strip() if command else ""
    if args.lower().startswith("ref_"):
        ref_code = args[4:].upper() or None
    elif args:
        ref_code = args.upper()

    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            ref_code=ref_code,
        )
        await session.commit()
        text = hello_text(user)

    await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("menu"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_menu(message: Message) -> None:
    text = (
        "<b>Команды</b>\n"
        "/start — главное меню\n"
        "/catalog — товары\n"
        "/balance — баланс и пополнение\n"
        "/orders — мои заказы\n"
        "/referral — реферальная программа\n"
        "/review — оставить отзыв\n\n"
        "Все товары оплачиваются с внутреннего баланса. Чтобы купить — пополни баланс "
        "криптой и выбери товар."
    )
    await message.answer(text, reply_markup=main_menu_kb())


# ---- Catalog --------------------------------------------------------------
@router.message(Command("catalog"))
@router.message(F.text == "🛒 Каталог")
async def cmd_catalog(message: Message) -> None:
    await message.answer("Выбери категорию:", reply_markup=catalog_root_kb())


@router.callback_query(F.data == "cat:root")
async def cb_cat_root(callback: CallbackQuery) -> None:
    await safe_edit_or_send(callback.message, "Выбери категорию:", reply_markup=catalog_root_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery, state: FSMContext) -> None:
    _, key = callback.data.split(":", 1)
    if key == "robux":
        await state.clear()
        qty = ROBUX_MIN
        await safe_edit_or_send(
            callback.message,
            linear_text("robux", qty),
            reply_markup=linear_kb("robux", qty, ROBUX_STEP, ROBUX_MIN, ROBUX_MAX),
        )
    elif key == "stars":
        await state.clear()
        qty = STARS_MIN
        await safe_edit_or_send(
            callback.message,
            linear_text("stars", qty),
            reply_markup=linear_kb("stars", qty, STARS_STEP, STARS_MIN, STARS_MAX),
        )
    elif key == "brawl":
        await safe_edit_or_send(
            callback.message,
            "<b>🎮 Brawl Stars</b>\nВыбери набор:",
            reply_markup=packs_kb("brawl"),
        )
    elif key == "clash":
        await safe_edit_or_send(
            callback.message,
            "<b>👑 Clash Royale</b>\nВыбери набор:",
            reply_markup=packs_kb("clash"),
        )
    else:
        await callback.answer("Категория не найдена", show_alert=True)
        return
    await callback.answer()


# ---- Linear (robux / stars) spinner --------------------------------------
def _spinner_params(kind: str) -> tuple[int, int, int]:
    if kind == "robux":
        return ROBUX_STEP, ROBUX_MIN, ROBUX_MAX
    return STARS_STEP, STARS_MIN, STARS_MAX


@router.callback_query(F.data.regexp(r"^(robux|stars):(inc|dec|inc5|dec5|noop|custom|buy)(?::(\d+))?$"))
async def cb_linear(callback: CallbackQuery, state: FSMContext) -> None:
    kind, op, *rest = callback.data.split(":")
    step, lo, hi = _spinner_params(kind)
    qty_raw = rest[0] if rest else str(lo)
    try:
        qty = int(qty_raw)
    except ValueError:
        qty = lo

    if op == "noop":
        await callback.answer()
        return

    if op == "custom":
        await state.set_state(BuyFlow.waiting_for_qty)
        await state.update_data(kind=kind)
        await callback.message.answer(
            f"Сколько {'робуксов' if kind == 'robux' else 'звёзд'} тебе нужно? "
            f"Введи число, кратное {step} (от {lo} до {hi})."
        )
        await callback.answer()
        return

    if op == "inc":
        qty = min(hi, qty + step)
    elif op == "dec":
        qty = max(lo, qty - step)
    elif op == "inc5":
        qty = min(hi, qty + step * 5)
    elif op == "dec5":
        qty = max(lo, qty - step * 5)
    elif op == "buy":
        await _start_purchase(callback, state, kind, qty)
        return

    await safe_edit_or_send(
        callback.message,
        linear_text(kind, qty),
        reply_markup=linear_kb(kind, qty, step, lo, hi),
    )
    await callback.answer()


@router.message(StateFilter(BuyFlow.waiting_for_qty))
async def on_custom_qty(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    kind = data.get("kind", "robux")
    step, lo, hi = _spinner_params(kind)
    if message.text is None or not message.text.strip().isdigit():
        await message.answer("Нужно целое число. Попробуй ещё раз.")
        return
    qty = int(message.text.strip())
    if qty < lo or qty > hi:
        await message.answer(f"Введи число от {lo} до {hi}.")
        return
    qty = (qty // step) * step
    if qty <= 0:
        qty = lo
    await state.clear()
    await message.answer(
        linear_text(kind, qty),
        reply_markup=linear_kb(kind, qty, step, lo, hi),
    )


# ---- Packs (Brawl / Clash) -----------------------------------------------
@router.callback_query(F.data.startswith("pack:buy:"))
async def cb_pack_buy(callback: CallbackQuery, state: FSMContext) -> None:
    sku = callback.data.split(":", 2)[2]
    await _start_purchase(callback, state, sku, 1)


@router.callback_query(F.data.startswith("pack:"))
async def cb_pack(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 2)
    if len(parts) < 2:
        await callback.answer()
        return
    sku = parts[1]
    pack = get_pack(sku)
    if pack is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await safe_edit_or_send(
        callback.message, pack_card_text(sku), reply_markup=pack_card_kb(sku)
    )
    await callback.answer()


# ---- Purchase flow --------------------------------------------------------
async def _start_purchase(
    callback: CallbackQuery, state: FSMContext, sku_or_kind: str, qty: int
) -> None:
    preview = preview_purchase(sku_or_kind, qty)
    if preview is None:
        await callback.answer("Не получилось рассчитать заказ", show_alert=True)
        return
    if callback.from_user is None:
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
        )
        if user.balance < preview.price:
            short = preview.price - user.balance
            await session.commit()
            await callback.message.answer(
                f"Не хватает <b>{format_rub(short)}</b> на балансе.\n"
                f"Пополни баланс — /balance.",
            )
            await callback.answer()
            return
        await session.commit()

    await state.set_state(BuyFlow.waiting_for_target)
    await state.update_data(sku=preview.sku, qty=preview.quantity, price=str(preview.price), title=preview.title)
    hint = _target_prompt(preview.sku)
    await callback.message.answer(
        f"<b>{html.escape(preview.title)}</b>\nК оплате: <b>{format_rub(preview.price)}</b>\n\n{hint}"
    )
    await callback.answer()


def _target_prompt(sku: str) -> str:
    if sku == "robux":
        return "Пришли <b>username Roblox</b> (логин, без @)."
    if sku == "stars":
        return "Пришли <b>@username</b> того, кому отправить звёзды."
    if sku.startswith("brawl_") or sku.startswith("clash_"):
        return "Пришли свой <b>игровой тег</b> (например, #2YGQRJ9V) и при необходимости почту аккаунта Supercell ID."
    return "Пришли данные для доставки."


@router.message(StateFilter(BuyFlow.waiting_for_target))
async def on_target(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    sku = data.get("sku")
    qty = int(data.get("qty", 0))
    if not sku or message.from_user is None:
        await state.clear()
        return
    target = (message.text or "").strip()
    if len(target) < 2 or len(target) > 200:
        await message.answer("Введи корректные данные доставки.")
        return

    preview = preview_purchase(sku, qty)
    if preview is None:
        await state.clear()
        await message.answer("Не удалось оформить заказ. Попробуй ещё раз: /catalog")
        return

    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        order = await place_order(session, user, preview, target=target)
        if order is None:
            await session.commit()
            await state.clear()
            await message.answer("Не хватает баланса. Пополни: /balance")
            return
        await session.commit()
        new_balance = user.balance
        order_id = order.id

    await state.clear()
    await message.answer(
        f"Заказ <b>#{order_id}</b> принят ✅\n"
        f"Товар: {html.escape(preview.title)}\n"
        f"Списано: {format_rub(preview.price)}\n"
        f"Остаток баланса: <b>{format_rub(new_balance)}</b>\n\n"
        "Доставка обычно занимает до 30 минут. Когда выполнено — придёт уведомление."
    )
    await _notify_admin_order(bot, order_id, preview, target, message.from_user)


async def _notify_admin_order(bot: Bot, order_id: int, preview, target: str, user) -> None:
    if not settings.news_channel:
        return
    handle = f"@{user.username}" if user.username else f"<a href=\"tg://user?id={user.id}\">{html.escape(user.first_name or 'клиент')}</a>"
    text = (
        f"🆕 <b>Новый заказ #{order_id}</b>\n"
        f"Клиент: {handle} (<code>{user.id}</code>)\n"
        f"Товар: {html.escape(preview.title)}\n"
        f"Сумма: {format_rub(preview.price)}\n"
        f"Доставка: <code>{html.escape(target)}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выдано", callback_data=f"adm:done:{order_id}")],
            [InlineKeyboardButton(text="❌ Отменить (вернуть деньги)", callback_data=f"adm:cancel:{order_id}")],
        ]
    )
    with contextlib.suppress(Exception):
        await bot.send_message(settings.news_channel, text, reply_markup=kb)


# ---- Balance / Top-up -----------------------------------------------------
@router.message(Command("balance"))
@router.message(F.text == "💰 Баланс")
async def cmd_balance(message: Message) -> None:
    if message.from_user is None:
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        await session.commit()
        text = (
            f"<b>Баланс:</b> {format_rub(user.balance)}\n\n"
            "Выбери сумму пополнения:"
        )
    await message.answer(text, reply_markup=deposit_kb())


@router.callback_query(F.data == "bal:back")
async def cb_bal_back(callback: CallbackQuery) -> None:
    await safe_edit_or_send(
        callback.message, "Возвращаемся в меню. Открой /start или /catalog.", reply_markup=None
    )
    await callback.answer()


@router.callback_query(F.data == "dep:custom")
async def cb_dep_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpFlow.waiting_for_amount)
    await callback.message.answer(
        f"Введи сумму пополнения в рублях ({DEPOSIT_MIN_RUB:.0f}–{DEPOSIT_MAX_RUB:.0f}):"
    )
    await callback.answer()


@router.message(StateFilter(TopUpFlow.waiting_for_amount))
async def on_dep_custom(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        await message.answer("Введи число, например: 250")
        return
    if amount < DEPOSIT_MIN_RUB or amount > DEPOSIT_MAX_RUB:
        await message.answer(
            f"Сумма должна быть от {DEPOSIT_MIN_RUB:.0f}₽ до {DEPOSIT_MAX_RUB:.0f}₽."
        )
        return
    await state.clear()
    await _send_invoice(message, amount)


@router.callback_query(F.data.startswith("dep:"))
async def cb_dep_preset(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2 or not parts[1].isdigit():
        return
    amount = Decimal(parts[1])
    await _send_invoice(callback.message, amount, from_user=callback.from_user)
    await callback.answer()


async def _send_invoice(
    message: Message,
    amount: Decimal,
    *,
    from_user=None,
) -> None:
    user_obj = from_user or message.from_user
    if user_obj is None:
        return
    if not settings.crypto_pay_enabled:
        await message.answer(
            "⚠️ Криптовалютные пополнения временно недоступны.\n"
            "Админ ещё не подключил CryptoBot. Напиши в поддержку."
        )
        return
    client = CryptoPayClient()
    try:
        invoice = await client.create_invoice_rub(
            amount_rub=amount,
            user_id=user_obj.id,
            description=f"Пополнение баланса Vavilon Market на {amount:.0f}₽",
        )
    except CryptoPayError as exc:
        logger.error("CryptoPay error: %s", exc)
        await message.answer("Не удалось создать счёт. Попробуй позже.")
        return

    async with SessionLocal() as session:
        await get_or_create_user(
            session,
            user_obj.id,
            username=getattr(user_obj, "username", None),
            first_name=getattr(user_obj, "first_name", None),
        )
        session.add(
            CryptoInvoice(
                invoice_id=invoice.invoice_id,
                user_id=user_obj.id,
                amount_rub=amount,
                pay_url=invoice.pay_url,
                status="active",
            )
        )
        await session.commit()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {format_rub(amount)}", url=invoice.pay_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"chk:{invoice.invoice_id}")],
        ]
    )
    await message.answer(
        f"Счёт на <b>{format_rub(amount)}</b> создан.\n"
        "Оплати в любой криптовалюте через @CryptoBot. После оплаты баланс зачислится автоматически.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("chk:"))
async def cb_check_invoice(callback: CallbackQuery, bot: Bot) -> None:
    invoice_id = callback.data.split(":", 1)[1]
    client = CryptoPayClient()
    if not client.enabled:
        await callback.answer("CryptoBot не подключен", show_alert=True)
        return
    try:
        items = await client.get_invoices([invoice_id])
    except CryptoPayError as exc:
        logger.error("CryptoPay check error: %s", exc)
        await callback.answer("Ошибка проверки. Попробуй позже.", show_alert=True)
        return
    paid = False
    for item in items:
        if item.get("status") == "paid":
            paid = await _credit_invoice(bot, item)
            break
    if paid:
        await callback.answer("Оплата получена ✅", show_alert=True)
    else:
        await callback.answer("Оплата ещё не пришла. Подожди минуту.", show_alert=True)


async def _credit_invoice(bot: Bot, item: dict[str, Any]) -> bool:
    """Mark an invoice paid and credit user balance. Idempotent."""
    invoice_id = str(item.get("invoice_id"))
    async with SessionLocal() as session:
        from sqlalchemy import select
        inv = (
            await session.execute(select(CryptoInvoice).where(CryptoInvoice.invoice_id == invoice_id))
        ).scalar_one_or_none()
        if inv is None or inv.status == "paid":
            return inv is not None and inv.status == "paid"
        user = await session.get(User, inv.user_id)
        if user is None:
            return False
        await credit_balance(
            session,
            user,
            inv.amount_rub,
            kind="deposit",
            note=f"CryptoBot invoice {invoice_id}",
            external_id=invoice_id,
        )
        inv.status = "paid"
        from datetime import datetime

        inv.paid_at = datetime.utcnow()
        await session.commit()
        new_balance = user.balance
        user_id = user.id

    with contextlib.suppress(Exception):
        await bot.send_message(
            user_id,
            f"💸 Баланс пополнен на <b>{format_rub(inv.amount_rub)}</b>.\n"
            f"Текущий баланс: <b>{format_rub(new_balance)}</b>",
        )
    return True


# ---- Orders ---------------------------------------------------------------
@router.message(Command("orders"))
@router.message(F.text == "📦 Мои заказы")
async def cmd_orders(message: Message) -> None:
    if message.from_user is None:
        return
    async with SessionLocal() as session:
        orders = await list_orders(session, message.from_user.id)
    if not orders:
        await message.answer("У тебя пока нет заказов. Загляни в /catalog 🛒")
        return
    lines = ["<b>Последние заказы:</b>", ""]
    for o in orders:
        status_emoji = {"new": "🆕", "paid": "💳", "delivered": "✅", "cancelled": "❌"}.get(o.status, "•")
        lines.append(
            f"{status_emoji} #{o.id} — {html.escape(o.title)} • {format_rub(o.price)} • <i>{o.status}</i>"
        )
    await message.answer("\n".join(lines))


# ---- Referral -------------------------------------------------------------
@router.message(Command("referral"))
@router.message(F.text == "👥 Рефералка")
async def cmd_referral(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        return
    async with SessionLocal() as session:
        user = await get_or_create_user(
            session,
            message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        count, earned = await referral_stats(session, message.from_user.id)
        await session.commit()
        code = user.ref_code

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{code}"
    await message.answer(
        "<b>👥 Реферальная программа</b>\n\n"
        f"Твой код: <code>{code}</code>\n"
        f"Твоя ссылка: {link}\n\n"
        f"Приглашай друзей — за каждую их покупку от "
        f"<b>{format_rub(REFERRAL_PURCHASE_THRESHOLD)}</b> ты получаешь "
        f"<b>{format_rub(REFERRAL_BONUS)}</b> на баланс.\n\n"
        f"Приглашено: <b>{count}</b>\nЗаработано: <b>{format_rub(earned)}</b>"
    )


# ---- Reviews --------------------------------------------------------------
@router.message(Command("review"))
@router.message(F.text == "⭐ Оставить отзыв")
async def cmd_review(message: Message, state: FSMContext) -> None:
    await state.set_state(ReviewFlow.waiting_for_rating)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐", callback_data="rev:1"),
                InlineKeyboardButton(text="⭐⭐", callback_data="rev:2"),
                InlineKeyboardButton(text="⭐⭐⭐", callback_data="rev:3"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rev:4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rev:5"),
            ],
        ]
    )
    await message.answer("Оцени магазин:", reply_markup=kb)


@router.callback_query(F.data.regexp(r"^rev:[1-5]$"))
async def cb_review_rate(callback: CallbackQuery, state: FSMContext) -> None:
    rating = int(callback.data.split(":", 1)[1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewFlow.waiting_for_text)
    await safe_edit_or_send(
        callback.message,
        f"Оценка: {'⭐' * rating}\n\nТеперь напиши пару слов о покупке.",
    )
    await callback.answer()


@router.message(StateFilter(ReviewFlow.waiting_for_text), F.text)
async def on_review_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Слишком коротко, напиши хотя бы пару предложений.")
        return
    await state.update_data(text=text)
    await state.set_state(ReviewFlow.waiting_for_photo)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Без фото", callback_data="rev:nophoto")]]
    )
    await message.answer("Прикрепи скриншот доставки (или нажми «Без фото»):", reply_markup=kb)


@router.callback_query(F.data == "rev:nophoto", StateFilter(ReviewFlow.waiting_for_photo))
async def cb_review_nophoto(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await _finalize_review(callback.message, callback.from_user, state, bot, photo_file_id=None)
    await callback.answer()


@router.message(StateFilter(ReviewFlow.waiting_for_photo), F.photo)
async def on_review_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    photo_file_id = message.photo[-1].file_id if message.photo else None
    await _finalize_review(message, message.from_user, state, bot, photo_file_id=photo_file_id)


async def _finalize_review(message: Message, user, state: FSMContext, bot: Bot, *, photo_file_id: str | None) -> None:
    data = await state.get_data()
    await state.clear()
    rating = int(data.get("rating", 5))
    text = data.get("text", "")
    if not user:
        return
    async with SessionLocal() as session:
        await get_or_create_user(
            session,
            user.id,
            username=getattr(user, "username", None),
            first_name=getattr(user, "first_name", None),
        )
        review = await save_review(session, user.id, rating, text, photo_file_id=photo_file_id)
        await session.commit()
        review_id = review.id

    handle = f"@{user.username}" if getattr(user, "username", None) else html.escape(user.first_name or "клиент")
    caption = (
        f"⭐ <b>Отзыв от {handle}</b>\n"
        f"Оценка: {'⭐' * rating}\n\n"
        f"{html.escape(text)}"
    )
    await message.answer("Спасибо за отзыв! 💛 Он отправлен в @vavilonmarketnews.")
    if not settings.news_channel:
        return
    try:
        if photo_file_id:
            await bot.send_photo(settings.news_channel, photo=photo_file_id, caption=caption)
        else:
            await bot.send_message(settings.news_channel, caption)
        async with SessionLocal() as session:
            rev = await session.get(type(review), review_id)
            if rev:
                rev.forwarded = 1
                await session.commit()
    except Exception:  # pragma: no cover
        logger.exception("Failed to forward review to news channel")


# ---- Admin actions on orders ---------------------------------------------
async def _is_admin(user_id: int) -> bool:
    if settings.admin_user_id and user_id == settings.admin_user_id:
        return True
    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        return bool(user and user.is_admin)


@router.callback_query(F.data.startswith("adm:"))
async def cb_admin_action(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user is None or not await _is_admin(callback.from_user.id):
        await callback.answer("Только для админа", show_alert=True)
        return
    _, op, oid = callback.data.split(":")
    order_id = int(oid)
    async with SessionLocal() as session:
        order = await session.get(Order, order_id)
        if order is None:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        if op == "done":
            from .services import deliver_order
            await deliver_order(session, order_id, admin_note="доставлено")
            await session.commit()
            with contextlib.suppress(Exception):
                await bot.send_message(
                    order.user_id,
                    f"✅ Заказ #{order_id} ({html.escape(order.title)}) выдан. Спасибо за покупку!\n"
                    "Оставь отзыв — /review",
                )
            await safe_edit_or_send(
                callback.message,
                (callback.message.text or callback.message.caption or "") + "\n\n<b>✅ ВЫДАНО</b>",
            )
        elif op == "cancel":
            from .services import cancel_order
            await cancel_order(session, order_id)
            await session.commit()
            with contextlib.suppress(Exception):
                await bot.send_message(
                    order.user_id,
                    f"❌ Заказ #{order_id} отменён. Деньги возвращены на баланс.",
                )
            await safe_edit_or_send(
                callback.message,
                (callback.message.text or callback.message.caption or "") + "\n\n<b>❌ ОТМЕНЕНО</b>",
            )
    await callback.answer()


# ---------------------------------------------------------------------------
# Bot bootstrap
# ---------------------------------------------------------------------------
def make_bot() -> Bot:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    return Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def make_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


async def run_bot() -> None:
    bot = make_bot()
    dp = make_dispatcher()
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Starting polling")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())
