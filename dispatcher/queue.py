"""
In-process async task queue.
Swap with Redis/Celery for multi-worker scaling.
"""

from __future__ import annotations

import asyncio
from server.models import TaskRequest

# Global task queue — shared between webhook route and worker
task_queue: asyncio.Queue[TaskRequest] = asyncio.Queue(maxsize=100)

