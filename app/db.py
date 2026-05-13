"""Database engine, session factory and ORM models."""
from __future__ import annotations

import os
import secrets
import string
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings


def _ensure_data_dir() -> None:
    if settings.database_url.startswith("sqlite"):
        # e.g. sqlite+aiosqlite:///./data/vavilon.db -> ./data/vavilon.db
        path = settings.database_url.split("///", 1)[-1]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)


_ensure_data_dir()
engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


def _ref_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    first_name: Mapped[str | None] = mapped_column(String(128), default=None)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    ref_code: Mapped[str] = mapped_column(String(16), unique=True, default=_ref_code, index=True)
    referred_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), default=None, index=True
    )
    is_admin: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orders: Mapped[list[Order]] = relationship(back_populates="user")


class BalanceTx(Base):
    """Any change to the user's balance (deposit, purchase, referral bonus)."""

    __tablename__ = "balance_tx"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # positive = credit, negative = debit
    kind: Mapped[str] = mapped_column(String(32))  # deposit | purchase | referral | manual
    note: Mapped[str | None] = mapped_column(String(255), default=None)
    external_id: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))  # e.g. robux_100, brawl_2000, stars_100
    title: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))  # final RUB price paid
    target: Mapped[str] = mapped_column(String(255))  # username / game tag / etc.
    status: Mapped[str] = mapped_column(
        String(32), default="new"
    )  # new | paid | delivered | cancelled
    admin_note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    user: Mapped[User] = relationship(back_populates="orders")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("orders.id"), default=None)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    text: Mapped[str] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(255), default=None)
    forwarded: Mapped[int] = mapped_column(Integer, default=0)  # 1 if posted to news channel
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CryptoInvoice(Base):
    __tablename__ = "crypto_invoices"
    __table_args__ = (UniqueConstraint("invoice_id", name="uq_crypto_invoice_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="active")  # active | paid | expired
    pay_url: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_or_create_user(
    session: AsyncSession,
    user_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    ref_code: str | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        referred_by: int | None = None
        if ref_code:
            referrer = (
                await session.execute(select(User).where(User.ref_code == ref_code))
            ).scalar_one_or_none()
            if referrer and referrer.id != user_id:
                referred_by = referrer.id
        # First user becomes admin automatically (only if there are no users yet).
        existing_count = (
            await session.execute(select(func.count(User.id)))
        ).scalar_one()
        first_user_admin = 1 if existing_count == 0 else 0
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            referred_by=referred_by,
            is_admin=first_user_admin,
        )
        session.add(user)
        await session.flush()
    else:
        # keep username/first_name fresh
        changed = False
        if username is not None and user.username != username:
            user.username = username
            changed = True
        if first_name is not None and user.first_name != first_name:
            user.first_name = first_name
            changed = True
        if changed:
            await session.flush()
    return user
