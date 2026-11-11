"""ASGI entry point: ``uvicorn ccradar.asgi:app``."""

from ccradar.main import create_app

app = create_app()
