from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from recruitment_collab.api.routes import router
from recruitment_collab.application.collaboration import ApplicationError
from recruitment_collab.config.settings import get_settings

settings = get_settings()
app = FastAPI(title="Recruitment Collaboration API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
if not settings.is_development:
    trusted_hosts = {urlparse(settings.public_web_url).hostname, urlparse(settings.feishu_redirect_uri).hostname}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(host for host in trusted_hosts if host))


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    try:
        request_id = str(uuid.UUID(supplied_request_id)) if supplied_request_id else str(uuid.uuid4())
    except ValueError:
        request_id = str(uuid.uuid4())
    request_limit = 320 * 1024 * 1024 if request.url.path.endswith("/snapshot") else 26 * 1024 * 1024 if request.url.path.endswith("/resume") else 1_000_000
    if int(request.headers.get("content-length", "0") or 0) > request_limit:
        return JSONResponse(
            status_code=413, content={"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体超过限制", "request_id": request_id, "details": {}}}
        )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not settings.is_development:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(ApplicationError)
async def application_error(request: Request, exc: ApplicationError):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "request_id": request_id, "details": {}}})


app.include_router(router)
