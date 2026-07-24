"""Usage statistics endpoints."""

from fastapi import APIRouter, HTTPException

import storage as db

from .auth import current_user_id

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/tokens")
def token_stats():
    user_id = current_user_id()
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    return db.user_token_stats(user_id)
