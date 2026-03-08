"""
Health check endpoint.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "agent": "saturn", "version": "0.1.0"}

