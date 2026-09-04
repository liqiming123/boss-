from datetime import datetime, timedelta
from typing import Optional, Protocol

from recruitment_collab.infrastructure.models import now

MAX_RETRIES = 5


class RetryableTask(Protocol):
    retry_count: int
    last_error: Optional[str]
    status: str
    next_retry_at: datetime


def schedule_retry(task: RetryableTask, error: Exception, operation: str) -> None:
    task.retry_count += 1
    # Keep only a short, sanitized provider code/message. Never persist full
    # HTTP response bodies, which may contain request metadata or payloads.
    detail = " ".join(str(error).split())[:180]
    task.last_error = f"{type(error).__name__}: {operation} failed" + (f" [{detail}]" if detail else "")
    task.status = "FAILED" if task.retry_count >= MAX_RETRIES else "PENDING"
    task.next_retry_at = now() + timedelta(seconds=min(3600, 2**task.retry_count * 5))
