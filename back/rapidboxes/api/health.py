"""Lightweight health check for install scripts and monitoring."""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}
