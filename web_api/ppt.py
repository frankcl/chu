"""Presentation theme endpoints."""

import importlib.util
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/ppt", tags=["ppt"])
_PPT_THEMES_MOD = None


def _ppt_themes_module():
    global _PPT_THEMES_MOD
    if _PPT_THEMES_MOD is None:
        path = Path(__file__).resolve().parent.parent / "skills" / "ppt" / "themes.py"
        spec = importlib.util.spec_from_file_location("ppt_themes", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PPT_THEMES_MOD = mod
    return _PPT_THEMES_MOD


@router.get("/themes")
def ppt_themes():
    return {"themes": _ppt_themes_module().theme_previews()}
