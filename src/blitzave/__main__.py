from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from blitzave.api.routes import router
from blitzave.store import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.connect().close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BlitZave", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8742)


if __name__ == "__main__":
    main()
