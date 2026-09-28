"""Khatti HTTP API (/v1). OpenAPI is served at /openapi.json and exported to openapi/khatti.v1.yaml.

Conventions (plan K): Bearer API keys for tenants, short-lived JWTs for reviewers and the
app; Idempotency-Key required on every POST (stored 24h); token-bucket rate limits with
429 + Retry-After; create -> upload -> submit -> poll or webhook.
"""

from __future__ import annotations

import functools
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

import jwt
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import __version__
from .auth import Principal, looks_like_jwt, mint_token, verify_token
from .models import DocumentInput, SessionResult
from .service import Conflict, Invalid, KycService, NotFound, session_of_document
from .services import Services, services_from_env
from .storage import LocalObjectStore

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IDEMPOTENCY_TTL = "24 hours"

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------- request bodies

class TokenRequest(BaseModel):
    subject: str = Field(..., max_length=200, description="Reviewer or app user id")
    role: Literal["reviewer", "app"]
    ttl_seconds: int = Field(900, ge=60, le=3600)


class CreateSession(BaseModel):
    slots: list[str] = Field(..., description="Required documents, e.g. national_id_front, national_id_back, commercial_registration, tax_card")
    external_ref: str | None = Field(None, max_length=200)


class UploadsRequest(BaseModel):
    slots: list[str]


class CreateDocument(BaseModel):
    type: str


class ReviewDecision(BaseModel):
    action: Literal["approve", "reject", "request_retake"]
    corrections: dict[str, str | None] = Field(default_factory=dict, description='"<slot>.<field>" -> corrected value')
    retake_slots: list[str] = Field(default_factory=list)
    note: str | None = Field(None, max_length=2000)


class ReminderRequest(BaseModel):
    title: str = Field(..., max_length=200, description="Shown to your staff; do not put personal data here")
    due_at: date
    notify_at: date | None = Field(None, description="When to send reminder.due (default: due_at)")
    session_id: str | None = None
    external_ref: str | None = Field(None, max_length=200)


class WebhookRequest(BaseModel):
    url: str
    events: list[str]


# ---------------------------------------------------------------- dependencies

def _svc(request: Request) -> Services:
    return request.app.state.services


def _service(request: Request) -> KycService:
    s = _svc(request).service
    if s is None:
        raise HTTPException(503, "Database not configured; set KHATTI_DATABASE_URL")
    return s


async def principal(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    svc = _svc(request)
    if creds is None:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    token = creds.credentials
    p: Principal | None = None
    if looks_like_jwt(token) and svc.jwt_secret:
        try:
            p = verify_token(svc.jwt_secret, token)
        except jwt.InvalidTokenError:
            p = None
    elif svc.service is not None:
        hit = await svc.service.store.resolve_api_key(token)
        if hit:
            p = Principal(hit[0], f"key:{hit[1]}", "service", str(hit[1]))
    if p is None:
        raise HTTPException(401, "invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    wait = svc.limiter.take(p.key_id)
    if wait:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(max(1, int(wait + 0.999)))})
    return p


def role(*roles: str):
    async def check(p: Principal = Depends(principal)) -> Principal:
        if not p.can(*roles):
            raise HTTPException(403, f"requires role {' or '.join(roles) or 'service'}")
        return p

    return check


async def idempotency_key(idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> str:
    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(400, "Idempotency-Key header is required on POST (max 200 chars)")
    return idempotency_key


async def _idempotent(request: Request, p: Principal, key: str, status: int, handler) -> JSONResponse:
    """Run handler once per (tenant, key). Replays return the stored response."""
    service = _service(request)
    body = await request.body()
    req_hash = hashlib.sha256(request.method.encode() + request.url.path.encode() + body).hexdigest()
    async with service.store.tx(p.tenant_id) as conn:
        await conn.execute(f"DELETE FROM idempotency_keys WHERE key = %s AND created_at < now() - interval '{IDEMPOTENCY_TTL}'", (key,))
        cur = await conn.execute(
            "INSERT INTO idempotency_keys (tenant_id, key, request_hash) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING key",
            (p.tenant_id, key, req_hash),
        )
        fresh = await cur.fetchone() is not None
        if not fresh:
            cur = await conn.execute("SELECT * FROM idempotency_keys WHERE key = %s", (key,))
            row = await cur.fetchone()
    if not fresh:
        if row["request_hash"] != req_hash:
            raise HTTPException(422, "Idempotency-Key was already used with a different request")
        if row["response_status"] is None:
            raise HTTPException(409, "a request with this Idempotency-Key is still in progress")
        return JSONResponse(row["response_body"], status_code=row["response_status"], headers={"Idempotent-Replayed": "true"})
    try:
        result = await handler()
    except BaseException:
        async with service.store.tx(p.tenant_id) as conn:
            await conn.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))
        raise
    payload = json.loads(json.dumps(result, default=str))
    async with service.store.tx(p.tenant_id) as conn:
        await conn.execute(
            "UPDATE idempotency_keys SET response_status = %s, response_body = %s WHERE key = %s", (status, Jsonb(payload), key)
        )
    return JSONResponse(payload, status_code=status)


def _errors(fn):
    """Map service exceptions to HTTP errors."""

    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except (Invalid, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    return wrapped


# ---------------------------------------------------------------- app

def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = app.state.services is None
        if owned:
            app.state.services = await services_from_env()
        yield
        if owned:
            await app.state.services.close()

    app = FastAPI(
        title="Khatti API",
        version=__version__,
        description="Arabic-first KYC document agent: reads Iraqi onboarding documents, cross-checks them, "
        "and hands the file to a human when unsure.",
        lifespan=lifespan,
    )
    app.state.services = services

    @app.get("/health")
    async def health(request: Request) -> dict:
        svc = _svc(request)
        p = svc.pipeline
        return {
            "status": "ok" if p else "unconfigured",
            "version": __version__,
            "readers": [r.name for r in p.readers] if p else [],
            "structurer": type(p.structurer).__name__ if p else None,
            "classifier": type(p.classifier).__name__ if p else None,
            "reviewer": p.reviewer.name if p and p.reviewer else None,
            "calibrator": p.calibrator.version if p else None,
            "database": svc.service is not None,
        }

    # ------------------------------------------------------------ auth

    @app.post("/v1/auth/token", tags=["auth"])
    async def auth_token(body: TokenRequest, request: Request, p: Principal = Depends(role())) -> dict:
        """Mint a short-lived JWT for a reviewer or the mobile app (service API keys only)."""
        secret = _svc(request).jwt_secret
        if not secret:
            raise HTTPException(503, "KHATTI_JWT_SECRET not configured")
        token, exp = mint_token(secret, p, body.subject, body.role, body.ttl_seconds)
        return {"access_token": token, "token_type": "bearer", "expires_at": exp, "role": body.role}

    # ------------------------------------------------------------ KYC sessions

    @app.post("/v1/kyc/sessions", status_code=201, tags=["kyc"])
    @_errors
    async def create_session(body: CreateSession, request: Request, key: str = Depends(idempotency_key),
                             p: Principal = Depends(role("app"))):
        return await _idempotent(request, p, key, 201,
                                 lambda: _service(request).create_session(p, body.slots, "kyc", body.external_ref))

    @app.post("/v1/kyc/sessions/{session_id}/uploads", tags=["kyc"])
    @_errors
    async def reissue_uploads(session_id: str, body: UploadsRequest, request: Request, key: str = Depends(idempotency_key),
                              p: Principal = Depends(role("app"))):
        return await _idempotent(request, p, key, 200, lambda: _service(request).new_uploads(p, session_id, body.slots))

    @app.post("/v1/kyc/sessions/{session_id}/submit", status_code=202, tags=["kyc"])
    @_errors
    async def submit(session_id: str, request: Request, key: str = Depends(idempotency_key), p: Principal = Depends(role("app"))):
        return await _idempotent(request, p, key, 202, lambda: _service(request).submit(p, session_id))

    @app.get("/v1/kyc/sessions/{session_id}", tags=["kyc"])
    @_errors
    async def get_session(session_id: str, request: Request, p: Principal = Depends(role("app", "reviewer"))) -> dict:
        return await _service(request).get_session(p, session_id)

    @app.post("/v1/kyc/analyze", response_model=SessionResult, tags=["kyc"])
    async def analyze(
        request: Request,
        files: list[UploadFile] = File(..., description="Document photos"),
        slots: list[str] = Form(..., description="Slot per file, same order"),
        p: Principal = Depends(role()),
    ) -> SessionResult:
        """Synchronous demo/eval endpoint: runs the pipeline in the request and stores nothing."""
        svc = _svc(request)
        if svc.pipeline is None:
            raise HTTPException(503, "No image readers configured; set KHATTI_READERS")
        if len(files) != len(slots) or not files or len(files) > 10:
            raise HTTPException(422, "need 1..10 files with one slot each")
        unknown = [s for s in slots if s not in svc.registry.types]
        if unknown:
            raise HTTPException(422, f"unknown slots {unknown}")
        docs = []
        for f, s in zip(files, slots):
            data = await f.read()
            if not data or len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413 if data else 422, f"{f.filename}: empty or larger than {MAX_IMAGE_BYTES} bytes")
            docs.append(DocumentInput(slot=s, image=data, mime_type=f.content_type or "image/jpeg"))
        return await svc.pipeline.run(docs)

    # ------------------------------------------------------------ single documents

    @app.post("/v1/documents", status_code=202, tags=["documents"])
    @_errors
    async def create_document(body: CreateDocument, request: Request, key: str = Depends(idempotency_key),
                              p: Principal = Depends(role("app"))):
        async def go():
            s = await _service(request).create_session(p, [body.type], "document")
            up = s["uploads"][0]
            return {"document_id": up["document_id"], "session_id": s["session_id"], "upload": up}

        return await _idempotent(request, p, key, 202, go)

    @app.post("/v1/documents/{document_id}/submit", status_code=202, tags=["documents"])
    @_errors
    async def submit_document(document_id: str, request: Request, key: str = Depends(idempotency_key),
                              p: Principal = Depends(role("app"))):
        async def go():
            sid = await session_of_document(_service(request), p, document_id)
            return {"document_id": document_id, **await _service(request).submit(p, sid)}

        return await _idempotent(request, p, key, 202, go)

    @app.get("/v1/documents/{document_id}", tags=["documents"])
    @_errors
    async def get_document(document_id: str, request: Request, p: Principal = Depends(role("app", "reviewer"))) -> dict:
        service = _service(request)
        view = await service.get_session(p, await session_of_document(service, p, document_id))
        doc = next((d for d in view.get("documents", []) if d["document_id"] == document_id), None)
        return {"document_id": document_id, "status": view["status"], "decision": view.get("decision"), "document": doc,
                "retake_requests": view.get("retake_requests", [])}

    # ------------------------------------------------------------ uploads (local storage only)

    @app.put("/v1/uploads/{token}", status_code=204, tags=["uploads"])
    async def upload(token: str, request: Request) -> Response:
        """Stand-in for an S3 presigned PUT when images are stored on local disk (development)."""
        objects = _svc(request).objects
        if not isinstance(objects, LocalObjectStore):
            raise HTTPException(404, "uploads go directly to object storage")
        try:
            key, content_type = objects.verify_upload_token(token)
        except ValueError as exc:
            raise HTTPException(403, str(exc)) from None
        data = await request.body()
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413 if data else 422, "empty or too large")
        await objects.put(key, data, content_type)
        return Response(status_code=204)

    # ------------------------------------------------------------ review

    @app.get("/v1/review/queue", tags=["review"])
    @_errors
    async def review_queue(request: Request, reason: str | None = None, limit: int = 50,
                           p: Principal = Depends(role("reviewer"))) -> list[dict]:
        return await _service(request).review_queue(p, reason, limit)

    @app.get("/v1/review/items/{item_id}", tags=["review"])
    @_errors
    async def review_item(item_id: str, request: Request, p: Principal = Depends(role("reviewer"))) -> dict:
        return await _service(request).review_item(p, item_id)

    @app.post("/v1/review/items/{item_id}/decision", tags=["review"])
    @_errors
    async def review_decision(item_id: str, body: ReviewDecision, request: Request, key: str = Depends(idempotency_key),
                              p: Principal = Depends(role("reviewer"))):
        return await _idempotent(request, p, key, 200, lambda: _service(request).review_decision(
            p, item_id, body.action, body.corrections, body.retake_slots, body.note))

    @app.get("/v1/review/documents/{document_id}/image", tags=["review"])
    @_errors
    async def review_image(document_id: str, request: Request, p: Principal = Depends(role("reviewer"))) -> Response:
        data, mime = await _service(request).document_image(p, document_id)
        return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ webhooks

    @app.post("/v1/webhooks", status_code=201, tags=["webhooks"])
    @_errors
    async def register_webhook(body: WebhookRequest, request: Request, key: str = Depends(idempotency_key),
                               p: Principal = Depends(role())):
        return await _idempotent(request, p, key, 201, lambda: _service(request).register_webhook(p, body.url, body.events))

    @app.get("/v1/webhooks", tags=["webhooks"])
    @_errors
    async def list_webhooks(request: Request, p: Principal = Depends(role())) -> list[dict]:
        return await _service(request).list_webhooks(p)

    @app.delete("/v1/webhooks/{webhook_id}", status_code=204, tags=["webhooks"])
    @_errors
    async def delete_webhook(webhook_id: str, request: Request, p: Principal = Depends(role())) -> Response:
        await _service(request).delete_webhook(p, webhook_id)
        return Response(status_code=204)

    # ------------------------------------------------------------ reminders

    @app.post("/v1/reminders", status_code=201, tags=["reminders"])
    @_errors
    async def create_reminder(body: ReminderRequest, request: Request, key: str = Depends(idempotency_key),
                              p: Principal = Depends(role())):
        return await _idempotent(request, p, key, 201, lambda: _service(request).create_reminder(
            p, body.title, body.due_at, body.notify_at, body.session_id, body.external_ref))

    @app.get("/v1/reminders", tags=["reminders"])
    @_errors
    async def list_reminders(request: Request, status: str | None = None, due_before: date | None = None, limit: int = 100,
                             p: Principal = Depends(role("reviewer"))) -> list[dict]:
        """Expiry reminders are created automatically from extracted dates (lead time KHATTI_REMINDER_LEAD_DAYS)."""
        return await _service(request).list_reminders(p, status, due_before, limit)

    @app.delete("/v1/reminders/{reminder_id}", status_code=204, tags=["reminders"])
    @_errors
    async def cancel_reminder(reminder_id: str, request: Request, p: Principal = Depends(role())) -> Response:
        await _service(request).cancel_reminder(p, reminder_id)
        return Response(status_code=204)

    # ------------------------------------------------------------ registry

    @app.get("/v1/document-types", tags=["registry"])
    async def document_types(request: Request, p: Principal = Depends(principal)) -> list[dict]:
        return [
            {"key": s.key, "domain": s.domain, "label": s.label, "label_ar": s.label_ar, "shape": s.shape.value,
             "fields": [{"name": f.name, "group": f.group.value, "required": f.required, "label_ar": f.label_ar,
                         "label_en": f.label_en} for f in s.fields]}
            for s in _svc(request).registry.types.values()
        ]

    return app


app = create_app()
