"""Run the bot (webhook or polling) and the web server in one process."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets

import uvicorn

from .bot import make_bot, make_dispatcher
from .config import settings
from .db import init_db
from .web import app as fastapi_app
from .web import set_bot

log = logging.getLogger(__name__)


async def _setup_bot():
    if not settings.bot_token:
        log.warning("BOT_TOKEN is not set — bot disabled, web only")
        return None, None
    bot = make_bot()
    dp = make_dispatcher()
    set_bot(bot, dp)
    try:
        me = await bot.get_me()
        os.environ["BOT_USERNAME"] = me.username or ""
        log.info("Bot @%s ready (mode=%s)", me.username, settings.effective_bot_mode)
    except Exception:
        log.exception("Failed to initialize bot")
        return None, None

    if settings.effective_bot_mode == "webhook":
        # Generate a random webhook secret if not configured.
        if not settings.webhook_secret:
            settings.webhook_secret = secrets.token_urlsafe(32)
        webhook_url = settings.telegram_webhook_url
        if not webhook_url:
            log.error("Cannot set webhook: PUBLIC_URL is empty")
            return bot, dp
        try:
            await bot.set_webhook(
                url=webhook_url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=False,
                allowed_updates=dp.resolve_used_update_types(),
            )
            log.info("Webhook set to %s", webhook_url)
        except Exception:
            log.exception("Failed to set webhook")
    else:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            log.exception("Failed to delete webhook")

    return bot, dp


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    bot, dp = await _setup_bot()

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    tasks = [asyncio.create_task(server.serve(), name="web")]
    if bot is not None and dp is not None and settings.effective_bot_mode == "polling":
        tasks.append(
            asyncio.create_task(
                dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
                name="bot-polling",
            )
        )

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc:
            raise exc


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
