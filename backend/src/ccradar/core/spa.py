"""Serve the built single-page web UI next to the API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

RESERVED_PREFIXES = ("api/", "healthz", "readyz", "metrics", "docs", "openapi.json")


def mount_spa(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(RESERVED_PREFIXES):
            raise HTTPException(status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
