"""admin_panel.routes.index — serves the Mini App placeholder page. No auth required (no sensitive data)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_INDEX_FILE = Path(__file__).parent.parent / "static" / "index.html"


@router.get("/")
async def index() -> FileResponse:
    return FileResponse(_INDEX_FILE)
