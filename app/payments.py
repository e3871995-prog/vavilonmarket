"""Crypto Pay (https://help.crypt.bot/crypto-pay-api) integration.

We create an invoice in RUB (with crypto auto-conversion). When the user pays it,
Crypto Pay calls our webhook and we credit the user's balance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from .config import settings

logger = logging.getLogger(__name__)


CRYPTO_PAY_BASE = "https://pay.crypt.bot/api"


@dataclass
class CreatedInvoice:
    invoice_id: str
    pay_url: str
    amount_rub: Decimal


class CryptoPayError(RuntimeError):
    pass


class CryptoPayClient:
    def __init__(self, token: str | None = None):
        self.token = token or settings.crypto_pay_token

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise CryptoPayError("Crypto Pay token is not configured")
        headers = {"Crypto-Pay-API-Token": self.token}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{CRYPTO_PAY_BASE}/{method}", json=payload, headers=headers)
            data = resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data))
        return data["result"]

    async def create_invoice_rub(
        self, *, amount_rub: Decimal, user_id: int, description: str
    ) -> CreatedInvoice:
        # Crypto Pay supports fiat invoices that are paid in any supported crypto.
        # The user picks the asset at the pay page.
        result = await self._call(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": str(amount_rub.quantize(Decimal("0.01"))),
                "accepted_assets": "USDT,TON,BTC,ETH,LTC,BNB,TRX",
                "description": description,
                "payload": f"user:{user_id}",
                "allow_comments": False,
                "allow_anonymous": True,
                "expires_in": 3600,
            },
        )
        return CreatedInvoice(
            invoice_id=str(result["invoice_id"]),
            pay_url=result.get("mini_app_invoice_url")
            or result.get("bot_invoice_url")
            or result.get("web_app_invoice_url")
            or result["pay_url"],
            amount_rub=amount_rub,
        )

    async def get_invoices(self, invoice_ids: list[str]) -> list[dict[str, Any]]:
        if not invoice_ids:
            return []
        result = await self._call(
            "getInvoices", {"invoice_ids": ",".join(invoice_ids)}
        )
        return result.get("items", [])


def verify_webhook_signature(token: str, body: bytes, signature: str | None) -> bool:
    """Verify Crypto Pay webhook signature.

    Algorithm (from official docs):
        secret = sha256(token)
        signature = hmac_sha256(secret, body).hex()
    """
    if not signature:
        return False
    try:
        secret = hashlib.sha256(token.encode("utf-8")).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:  # pragma: no cover
        logger.exception("Failed to verify Crypto Pay signature")
        return False


def parse_user_id_from_payload(payload: str | None) -> int | None:
    if not payload or not payload.startswith("user:"):
        return None
    try:
        return int(payload.split(":", 1)[1])
    except ValueError:
        return None


def parse_webhook_body(body: bytes) -> dict[str, Any]:
    return json.loads(body.decode("utf-8"))
