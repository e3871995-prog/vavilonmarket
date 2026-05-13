"""Product catalog and pricing.

Pricing rules:
- Robux: 100 robux = 80 RUB (linear; user picks any multiple of 100)
- Telegram Stars: 100 stars = 150 RUB (linear; user picks any multiple of 100)
- Clash Royale gems / Brawl Stars gems: each pack uses the official store price multiplied by 2.
  We store the source RUB price (what the user "would pay in the official store") and the
  catalog renders the doubled price.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# --- linear products -------------------------------------------------------
ROBUX_RATE = Decimal("0.80")  # rubles per 1 robux  (80 RUB / 100 robux)
STARS_RATE = Decimal("1.50")  # rubles per 1 star  (150 RUB / 100 stars)
ROBUX_STEP = 100
STARS_STEP = 100
ROBUX_MIN = 100
ROBUX_MAX = 10_000
STARS_MIN = 100
STARS_MAX = 10_000


def robux_price(qty: int) -> Decimal:
    return (Decimal(qty) * ROBUX_RATE).quantize(Decimal("0.01"))


def stars_price(qty: int) -> Decimal:
    return (Decimal(qty) * STARS_RATE).quantize(Decimal("0.01"))


# --- fixed-pack products ---------------------------------------------------
GAME_MARKUP = Decimal("2.0")  # Clash Royale / Brawl Stars markup multiplier


@dataclass(frozen=True)
class Pack:
    sku: str
    title: str
    qty: int            # e.g. 80 gems, 500 gems, 2000 gems
    source_price: Decimal  # official store price in RUB

    @property
    def price(self) -> Decimal:
        return (self.source_price * GAME_MARKUP).quantize(Decimal("0.01"))


# Brawl Stars gem packs — official Russian store prices.
BRAWL_PACKS: list[Pack] = [
    Pack("brawl_30", "30 гемов",   30,   Decimal("75")),
    Pack("brawl_80", "80 гемов",   80,   Decimal("190")),
    Pack("brawl_170", "170 гемов", 170,  Decimal("379")),
    Pack("brawl_360", "360 гемов", 360,  Decimal("749")),
    Pack("brawl_950", "950 гемов", 950,  Decimal("1890")),
    Pack("brawl_2000", "2000 гемов", 2000, Decimal("3790")),
]

# Clash Royale gem packs — official Russian store prices.
CLASH_PACKS: list[Pack] = [
    Pack("clash_80", "80 гемов",   80,   Decimal("90")),
    Pack("clash_500", "500 гемов",  500,  Decimal("499")),
    Pack("clash_1200", "1200 гемов", 1200, Decimal("1090")),
    Pack("clash_2500", "2500 гемов", 2500, Decimal("2190")),
    Pack("clash_6500", "6500 гемов", 6500, Decimal("5490")),
    Pack("clash_14000", "14000 гемов", 14000, Decimal("10990")),
]


# Top-up presets for balance.
DEPOSIT_PRESETS_RUB: list[int] = [100, 250, 500, 1000, 2000, 5000]
DEPOSIT_MIN_RUB = Decimal("50")
DEPOSIT_MAX_RUB = Decimal("100000")


# Category metadata for the UI.
@dataclass(frozen=True)
class Category:
    key: str
    title: str
    emoji: str
    description: str


CATEGORIES: list[Category] = [
    Category("robux", "Robux", "🟢", "100 робуксов = 80₽"),
    Category("stars", "Telegram Stars", "⭐", "100 звёзд = 150₽"),
    Category("brawl", "Brawl Stars", "🎮", "Гемы Brawl Stars"),
    Category("clash", "Clash Royale", "👑", "Гемы Clash Royale"),
]


def get_pack(sku: str) -> Pack | None:
    for pack in BRAWL_PACKS + CLASH_PACKS:
        if pack.sku == sku:
            return pack
    return None


def category_packs(category: str) -> list[Pack]:
    if category == "brawl":
        return BRAWL_PACKS
    if category == "clash":
        return CLASH_PACKS
    return []


def category_title(category: str) -> str:
    for c in CATEGORIES:
        if c.key == category:
            return f"{c.emoji} {c.title}"
    return category


def format_rub(value: Decimal) -> str:
    q = value.quantize(Decimal("0.01"))
    if q == q.to_integral_value():
        return f"{int(q)}₽"
    return f"{q}₽"
