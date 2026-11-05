"""ASGI entry point: ``uvicorn costlens.asgi:app``."""

from costlens.main import create_app

app = create_app()
