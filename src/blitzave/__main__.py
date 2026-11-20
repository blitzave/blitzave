import argparse
import atexit
import logging
import shutil
import signal
import sys
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from blitzave import demo, jobs
from blitzave.api.routes import router
from blitzave.store import db, users

logger = logging.getLogger(__name__)

# dev/ — a throwaway debug page, not the product UI. See AGENTS.md
# ("Two zones"). Mounted same-origin so it needs no CORS.
DEV_DIR = Path(__file__).resolve().parents[2] / "dev"

# blitzave/web/ — the vibecoded UI, served same-origin at / so the localhost
# API never needs CORS. See AGENTS.md ("The vibecoded zone"). It lives inside
# the package so `uv tool install` ships it; dev/ above is left out on purpose.
WEB_DIR = Path(__file__).resolve().parent / "web"

HOST = "127.0.0.1"
PORT = 8742
URL = f"http://{HOST}:{PORT}"

# DNS rebinding: a page on evil.example can point its own name at 127.0.0.1,
# and the browser then lets it read this server as if it were its own site.
# The request's Host header still says evil.example, so any name but ours
# gets a 400. Only the name is checked: a page can't make the browser send
# 127.0.0.1 or localhost unless it really was opened there.
ALLOWED_HOSTS = [HOST, "localhost"]


class RevalidatingStaticFiles(StaticFiles):
    """The UI is plain ES modules with no build step and no hashed filenames,
    so after an update the browser must re-check every file or it can run a
    mix of old and new modules. no-cache still allows cached copies; it only
    forces a revalidation, which on localhost is an instant 304."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


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

    # Opt-in and at most once a day (see jobs.check_for_update). Offline or
    # no release yet just means there's nothing new to show. Never in demo
    # mode: a demo touches the network for nothing.
    if not jobs.DEMO:
        try:
            jobs.check_for_update(date.today())
        except jobs.UpdateCheckError as e:
            logger.info("update check skipped: %s", e)

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BlitZave", lifespan=lifespan)
    # Wraps everything below: the API, /docs, /dev and the UI.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.include_router(router)
    if DEV_DIR.is_dir():
        app.mount("/dev", RevalidatingStaticFiles(directory=DEV_DIR, html=True), name="dev")
    if WEB_DIR.is_dir():
        # Mounted last: API routes and /docs are matched first, everything
        # else falls through to the UI's static files.
        app.mount("/", RevalidatingStaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()


class BrowserOpeningServer(uvicorn.Server):
    """Opens the UI in a browser tab once the server is listening. uvicorn
    runs the lifespan (and so the startup sync) before it binds the socket,
    so opening from the lifespan could beat the server to it. A failed start
    exits inside startup(), so a tab only opens on a server that's up."""

    async def startup(self, sockets=None):
        await super().startup(sockets)
        webbrowser.open(f"http://{HOST}:{self.config.port}")


def _start_demo() -> int:
    """Made-up data in a temporary folder, removed however the process ends:
    Ctrl+C arrives as a KeyboardInterrupt and kill as SIGTERM, which uvicorn
    raises again after shutting down. Both end in atexit. Returns the port."""
    home = demo.start()
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    demo.set_up(app)
    print(
        "BlitZave demo: a made-up household. Nothing here is yours, "
        "and it's all deleted when you stop it (Ctrl+C).",
        file=sys.stderr,
    )
    return demo.PORT


def main() -> None:
    parser = argparse.ArgumentParser(prog="blitzave", description="BlitZave, on your own machine.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="try it on a made-up household; nothing is kept, and your real setup is never touched",
    )
    args = parser.parse_args()
    try:
        port = _start_demo() if args.demo else PORT
        BrowserOpeningServer(uvicorn.Config(app, host=HOST, port=port)).run()
    except KeyboardInterrupt:
        pass  # Ctrl+C: uvicorn has already shut down; this hides the traceback.


if __name__ == "__main__":
    main()
