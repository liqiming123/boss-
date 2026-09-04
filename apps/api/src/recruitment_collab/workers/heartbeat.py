from __future__ import annotations

import time

from sqlalchemy import select

from recruitment_collab.infrastructure.database import SessionLocal
from recruitment_collab.infrastructure.models import WorkerHeartbeat, now

_last_write: dict[str, float] = {}


def record_worker_heartbeat(worker_name: str, processed: int = 0, error_code: str | None = None, force: bool = False) -> None:
    """Update one bounded row per worker; never persist payloads or exception text."""
    current_monotonic = time.monotonic()
    if not force and current_monotonic - _last_write.get(worker_name, 0) < 30:
        return
    with SessionLocal() as session:
        heartbeat = session.scalar(select(WorkerHeartbeat).where(WorkerHeartbeat.worker_name == worker_name))
        current = now()
        if not heartbeat:
            heartbeat = WorkerHeartbeat(worker_name=worker_name, total_processed=0)
            session.add(heartbeat)
        heartbeat.status = "ERROR" if error_code else "HEALTHY"
        heartbeat.last_seen_at = current
        heartbeat.last_error_code = error_code
        heartbeat.total_processed = (heartbeat.total_processed or 0) + processed
        if not error_code:
            heartbeat.last_success_at = current
        session.commit()
    _last_write[worker_name] = current_monotonic
