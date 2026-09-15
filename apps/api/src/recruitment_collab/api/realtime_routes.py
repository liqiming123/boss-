"""Server-sent event stream for time-sensitive duplicate alerts."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from recruitment_collab.api.dependencies import Actor, plugin_actor
from recruitment_collab.infrastructure.realtime import stream_company_events

router = APIRouter(prefix="/api/v1")


@router.get("/plugin/stream")
async def plugin_event_stream(actor: Actor = Depends(plugin_actor)) -> StreamingResponse:
    """Hold one long-lived SSE connection open for this device.

    The stream carries only duplicate-alert notifications belonging to the
    caller's company.  It never carries candidate text, chat content, cookies
    or attachments, and losing a frame is harmless: the alert row and the
    Feishu direct message remain the authoritative record.
    """
    return StreamingResponse(
        stream_company_events(actor.company_id, actor.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            # Nginx buffers proxied responses by default, which would hold SSE
            # frames until the buffer fills and defeat the whole channel.
            "X-Accel-Buffering": "no",
        },
    )
