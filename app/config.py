"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(default="", description="Telegram bot token from @BotFather")
    admin_user_id: int = Field(default=0, description="Telegram user ID of the shop admin")
    news_channel: str = Field(
        default="@vavilonmarketnews",
        description="Channel/chat where orders and reviews are forwarded",
    )
    crypto_pay_token: str = Field(default="", description="Crypto Pay API token")
    public_url: str = Field(default="", description="Public URL of the deployed app")
    port: int = Field(default=8080, description="HTTP port")
    bot_mode: str = Field(
        default="auto",
        description="Bot transport: 'polling', 'webhook', or 'auto' (webhook if PUBLIC_URL set)",
    )
    webhook_secret: str = Field(
        default="",
        description="Secret token passed by Telegram in X-Telegram-Bot-Api-Secret-Token",
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/vavilon.db",
        description="SQLAlchemy async database URL",
    )

    @property
    def crypto_pay_enabled(self) -> bool:
        return bool(self.crypto_pay_token)

    @property
    def webapp_url(self) -> str:
        """URL exposed as the Telegram Mini App."""
        if not self.public_url:
            return ""
        return self.public_url.rstrip("/")

    @property
    def effective_bot_mode(self) -> str:
        if self.bot_mode == "polling":
            return "polling"
        if self.bot_mode == "webhook":
            return "webhook"
        # auto
        return "webhook" if self.public_url else "polling"

    @property
    def telegram_webhook_path(self) -> str:
        return "/webhook/telegram"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.webapp_url}{self.telegram_webhook_path}" if self.webapp_url else ""


settings = Settings()
