"""Generated artifact download endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/files", tags=["files"])
GENERATED_DIR = Path(__file__).resolve().parent.parent / "generated"


@router.get("/{filename}")
def download_file(filename: str):
    base = GENERATED_DIR.resolve()
    target = (base / filename).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)
