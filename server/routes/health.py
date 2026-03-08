"""
Health check endpoint.
"""

from fastapi import APIRouter
from config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": "saturn",
        "version": "0.2.0",
        "repo": settings.repo_url or "(not configured)",
    }

