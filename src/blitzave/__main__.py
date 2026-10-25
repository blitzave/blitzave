import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from blitzave import jobs
from blitzave.api.routes import router
from blitzave.store import db, users

logger = logging.getLogger(__name__)

# engine/dev/ — a throwaway debug page, not the product UI. See AGENTS.md
# ("Exception: engine/dev/"). Mounted same-origin so it needs no CORS.
DEV_DIR = Path(__file__).resolve().parents[2] / "dev"

# engine/web/ — the vibecoded UI, served same-origin at / so the localhost
# API never needs CORS. See AGENTS.md ("The vibecoded zone: engine/web/").
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    conn = db.connect()
    try:
        users.get_or_create_user(conn)
    finally:
        conn.close()

    # "The ally shows up when you open it" — sync on startup so there's
    # something to see with no button to press. A failed/skipped sync must
    # never take the API down (AGENTS.md: "the API reports an honest 'last
    # synced'"), so this only logs.
    try:
        jobs.sync()
    except jobs.SyncError as e:
        logger.info("startup sync skipped: %s", e)

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BlitZave", lifespan=lifespan)
    app.include_router(router)
    if DEV_DIR.is_dir():
        app.mount("/dev", StaticFiles(directory=DEV_DIR, html=True), name="dev")
    if WEB_DIR.is_dir():
        # Mounted last: API routes and /docs are matched first, everything
        # else falls through to the UI's static files.
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8742)


if __name__ == "__main__":
    main()
