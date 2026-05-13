"""Run the bot (long polling) and the web server in one process."""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .bot import make_bot, make_dispatcher
from .config import settings
from .db import init_db
from .web import app as fastapi_app
from .web import set_bot

log = logging.getLogger(__name__)


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()

    if not settings.bot_token:
        log.warning("BOT_TOKEN is not set — bot polling is disabled, web only")
        bot = None
        dp = None
    else:
        bot = make_bot()
        dp = make_dispatcher()
        set_bot(bot)
        try:
            me = await bot.get_me()
            os.environ["BOT_USERNAME"] = me.username or ""
            log.info("Bot @%s started", me.username)
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            log.exception("Failed to initialize bot")

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    tasks = [asyncio.create_task(server.serve(), name="web")]
    if bot is not None and dp is not None:
        tasks.append(
            asyncio.create_task(
                dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
                name="bot",
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
