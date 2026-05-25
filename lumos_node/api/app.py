"""FastAPI app for Lumos HUD: SSE chat, telemetry, search, static HUD hosting."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from ..log import get_logger
from . import routes


log = get_logger(__name__)


async def _auto_dream_loop() -> None:
    """Background task: periodically consolidate pending turns into identity FAISS.

    Disabled when settings.auto_dream_interval_minutes == 0.
    Runs only when pending turn count >= settings.auto_dream_min_pending.
    """
    settings = get_settings()
    interval = settings.auto_dream_interval_minutes
    if interval <= 0:
        return  # disabled

    from ..dream import dream_status, run_dream_cycle

    interval_sec = interval * 60
    log.info("auto_dream.started", interval_minutes=interval)

    # Sleep first so we don't fire immediately on startup.
    while True:
        try:
            await asyncio.sleep(interval_sec)
            status = dream_status(settings)
            pending = int(status.get("pending", 0))
            if pending < settings.auto_dream_min_pending:
                log.info("auto_dream.skipped", pending=pending)
                continue
            log.info("auto_dream.firing", pending=pending)
            result = await run_dream_cycle(settings=settings)
            log.info(
                "auto_dream.completed",
                consolidated=result.get("consolidated", 0),
                skipped=result.get("skipped", False),
            )
        except asyncio.CancelledError:
            log.info("auto_dream.cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            # Auto-dream failures should never crash the server. Log and continue.
            log.warning("auto_dream.failed", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    try:
        from ..retrieval import get_identity_store, get_knowledge_store
        get_identity_store(settings)
        get_knowledge_store(settings)
        log.info("api.lifespan.indexes_loaded")
    except Exception as e:  # noqa: BLE001
        log.warning("api.lifespan.indexes_load_failed", error=str(e))

    dream_task: asyncio.Task[None] | None = None
    if settings.auto_dream_interval_minutes > 0:
        dream_task = asyncio.create_task(_auto_dream_loop())

    cosmic_task: asyncio.Task[None] | None = None
    if settings.cosmic_trigger_enabled:
        from ..telemetry.worker import cosmic_worker_loop
        cosmic_task = asyncio.create_task(cosmic_worker_loop())

    yield

    for task in (dream_task, cosmic_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def create_app() -> FastAPI:
    app = FastAPI(title="lumos_node", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router, prefix="/api")

    hud_dist = Path(__file__).resolve().parent.parent.parent / "hud" / "dist"
    if hud_dist.exists():
        app.mount("/", StaticFiles(directory=str(hud_dist), html=True), name="hud")

    return app
