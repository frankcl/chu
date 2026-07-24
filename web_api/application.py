"""FastAPI application assembly and lifecycle."""

import asyncio
from contextlib import asynccontextmanager, suppress

from dotenv import load_dotenv
from fastapi import FastAPI

import storage as db

load_dotenv()

from . import auth, chat, conversations, files, ppt, runtime, sessions, stats  # noqa: E402

db.init_db()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sweeper = asyncio.create_task(runtime.memory_sweeper())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        for session_id in list(runtime.sessions):
            runtime.teardown_session(session_id)


def create_app() -> FastAPI:
    application = FastAPI(lifespan=lifespan)
    auth.configure_auth(application)
    application.include_router(auth.router)
    application.include_router(files.router)
    application.include_router(ppt.router)
    application.include_router(sessions.router)
    application.include_router(conversations.router)
    application.include_router(stats.router)
    application.include_router(chat.router)
    return application


app = create_app()
