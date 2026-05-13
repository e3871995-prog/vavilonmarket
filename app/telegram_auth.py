"""Validate Telegram WebApp (Mini App) init data.

Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


@dataclass
class TelegramWebAppUser:
    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    start_param: str | None = None


class WebAppAuthError(ValueError):
    pass


def parse_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86_400) -> TelegramWebAppUser:
    if not init_data:
        raise WebAppAuthError("empty init data")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise WebAppAuthError("hash missing")

    # auth_date freshness check
    auth_date_raw = parsed.get("auth_date")
    if auth_date_raw and auth_date_raw.isdigit():
        if time.time() - int(auth_date_raw) > max_age_seconds:
            raise WebAppAuthError("init data expired")

    data_check_string = "\n".join(f"{k}={parsed[k]}" for k in sorted(parsed))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise WebAppAuthError("bad signature")

    user_raw = parsed.get("user")
    if not user_raw:
        raise WebAppAuthError("no user")
    user: dict[str, Any] = json.loads(user_raw)
    return TelegramWebAppUser(
        id=int(user["id"]),
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        start_param=parsed.get("start_param"),
    )
