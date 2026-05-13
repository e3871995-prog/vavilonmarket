"""Domain services shared by the bot and the web API."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .catalog import (
    BRAWL_PACKS,
    CLASH_PACKS,
    ROBUX_MAX,
    ROBUX_MIN,
    ROBUX_STEP,
    STARS_MAX,
    STARS_MIN,
    STARS_STEP,
    get_pack,
    robux_price,
    stars_price,
)
from .db import BalanceTx, Order, Review, User

REFERRAL_PURCHASE_THRESHOLD = Decimal("100")  # purchase >= 100 RUB triggers bonus
REFERRAL_BONUS = Decimal("10")                 # +10 RUB to the referrer


@dataclass
class PurchasePreview:
    sku: str
    title: str
    quantity: int
    price: Decimal


def preview_purchase(sku: str, quantity: int) -> PurchasePreview | None:
    """Return a preview without touching the DB.

    For linear products (robux/stars) the SKU is just the family ("robux" / "stars")
    and quantity is the amount of robux/stars. For packs the SKU is the pack id
    and quantity is always 1.
    """
    if sku == "robux":
        qty = max(ROBUX_MIN, min(ROBUX_MAX, (quantity // ROBUX_STEP) * ROBUX_STEP))
        if qty <= 0:
            return None
        return PurchasePreview("robux", f"{qty} Robux", qty, robux_price(qty))
    if sku == "stars":
        qty = max(STARS_MIN, min(STARS_MAX, (quantity // STARS_STEP) * STARS_STEP))
        if qty <= 0:
            return None
        return PurchasePreview("stars", f"{qty} Telegram Stars", qty, stars_price(qty))
    pack = get_pack(sku)
    if pack is None:
        return None
    return PurchasePreview(pack.sku, pack.title, pack.qty, pack.price)


async def credit_balance(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    *,
    kind: str,
    note: str | None = None,
    external_id: str | None = None,
) -> BalanceTx:
    user.balance = (user.balance + amount).quantize(Decimal("0.01"))
    tx = BalanceTx(
        user_id=user.id, amount=amount, kind=kind, note=note, external_id=external_id
    )
    session.add(tx)
    await session.flush()
    return tx


async def debit_balance(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    *,
    kind: str,
    note: str | None = None,
) -> BalanceTx | None:
    if user.balance < amount:
        return None
    user.balance = (user.balance - amount).quantize(Decimal("0.01"))
    tx = BalanceTx(user_id=user.id, amount=-amount, kind=kind, note=note)
    session.add(tx)
    await session.flush()
    return tx


async def place_order(
    session: AsyncSession,
    user: User,
    preview: PurchasePreview,
    *,
    target: str,
) -> Order | None:
    """Deduct the balance and create a new order in 'paid' state.

    Returns None when the user does not have enough balance.
    """
    debit = await debit_balance(
        session,
        user,
        preview.price,
        kind="purchase",
        note=f"{preview.title} → {target}",
    )
    if debit is None:
        return None
    order = Order(
        user_id=user.id,
        sku=preview.sku,
        title=preview.title,
        quantity=preview.quantity,
        price=preview.price,
        target=target,
        status="paid",
    )
    session.add(order)
    await session.flush()

    # Award referral bonus exactly once per qualifying purchase.
    if (
        user.referred_by
        and preview.price >= REFERRAL_PURCHASE_THRESHOLD
    ):
        referrer = await session.get(User, user.referred_by)
        if referrer is not None:
            await credit_balance(
                session,
                referrer,
                REFERRAL_BONUS,
                kind="referral",
                note=f"Бонус за покупку @{user.username or user.id} (заказ #{order.id})",
                external_id=f"order:{order.id}",
            )
    return order


async def deliver_order(session: AsyncSession, order_id: int, admin_note: str | None) -> Order | None:
    from datetime import datetime

    order = await session.get(Order, order_id)
    if order is None or order.status == "delivered":
        return order
    order.status = "delivered"
    order.admin_note = admin_note
    order.delivered_at = datetime.utcnow()
    await session.flush()
    return order


async def cancel_order(session: AsyncSession, order_id: int) -> Order | None:
    """Cancel a paid order and refund the user."""
    order = await session.get(Order, order_id)
    if order is None or order.status in {"cancelled", "delivered"}:
        return order
    order.status = "cancelled"
    user = await session.get(User, order.user_id)
    if user is not None:
        await credit_balance(
            session,
            user,
            order.price,
            kind="refund",
            note=f"Возврат за #{order.id} ({order.title})",
            external_id=f"order:{order.id}",
        )
    await session.flush()
    return order


async def list_orders(session: AsyncSession, user_id: int, limit: int = 20) -> list[Order]:
    q = (
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(desc(Order.created_at))
        .limit(limit)
    )
    return list((await session.execute(q)).scalars().all())


async def referral_stats(session: AsyncSession, user_id: int) -> tuple[int, Decimal]:
    """Return (number_of_referrals, total_earned_from_referrals)."""
    count_q = select(User.id).where(User.referred_by == user_id)
    count = len((await session.execute(count_q)).scalars().all())

    earned_q = select(BalanceTx.amount).where(
        BalanceTx.user_id == user_id, BalanceTx.kind == "referral"
    )
    total = Decimal("0")
    for amount in (await session.execute(earned_q)).scalars().all():
        total += amount
    return count, total.quantize(Decimal("0.01"))


def all_packs():
    return BRAWL_PACKS + CLASH_PACKS


async def save_review(
    session: AsyncSession,
    user_id: int,
    rating: int,
    text: str,
    order_id: int | None = None,
    photo_file_id: str | None = None,
) -> Review:
    rating = max(1, min(5, int(rating)))
    review = Review(
        user_id=user_id,
        order_id=order_id,
        rating=rating,
        text=text.strip()[:2000],
        photo_file_id=photo_file_id,
    )
    session.add(review)
    await session.flush()
    return review
