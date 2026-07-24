"""Compatibility ASGI entrypoint for ``uvicorn server:app``."""

from web_api import app

__all__ = ["app"]
