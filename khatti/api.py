"""HTTP API for the Khatti document agent."""

from __future__ import annotations

import hmac
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import StoredDocument
from .models import CaseResult, DocumentInput
from .services import Services, services_from_env
from .webhooks import validate_callback_url

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENTS = 10

_bearer = HTTPBearer(auto_error=False)


def _services(request: Request) -> Services:
    return request.app.state.services


def require_api_key(
    request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> None:
    keys = _services(request).api_keys
    if not keys:
        return  # auth disabled (local development)
    token = creds.credentials if creds else ""
    if not any(hmac.compare_digest(token, k) for k in keys):
        raise HTTPException(401, "invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})


async def _read_documents(svc: Services, files: list[UploadFile], doc_types: list[str]) -> list[DocumentInput]:
    if svc.pipeline is None:
        raise HTTPException(503, "No image readers configured; set KHATTI_READERS")
    if len(files) != len(doc_types):
        raise HTTPException(422, "files and doc_types must have the same length")
    if len(files) > MAX_DOCUMENTS:
        raise HTTPException(422, f"at most {MAX_DOCUMENTS} documents per case")
    unknown = [t for t in doc_types if t not in svc.pipeline.registry.types]
    if unknown:
        raise HTTPException(422, f"unknown doc_types {unknown}; known: {sorted(svc.pipeline.registry.types)}")

    docs = []
    for f, t in zip(files, doc_types):
        data = await f.read()
        if not data:
            raise HTTPException(422, f"{f.filename} is empty")
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, f"{f.filename} exceeds {MAX_IMAGE_BYTES} bytes")
        docs.append(DocumentInput(doc_type=t, image=data, mime_type=f.content_type or "image/jpeg"))
    return docs


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = app.state.services is None
        if owned:
            app.state.services = await services_from_env()
        yield
        if owned:
            await app.state.services.close()

    app = FastAPI(title="Khatti Document Agent", version="0.2.0", lifespan=lifespan)
    app.state.services = services
    auth = [Depends(require_api_key)]

    @app.get("/health")
    async def health(request: Request) -> dict:
        svc = _services(request)
        p = svc.pipeline
        return {
            "status": "ok" if p else "unconfigured",
            "readers": [r.name for r in p.readers] if p else [],
            "router": p.router.name if p and p.router else None,
            "reviewer": p.reviewer.name if p and p.reviewer else None,
            "queue": svc.cases is not None,
        }

    @app.get("/v1/document-types", dependencies=auth)
    async def document_types(request: Request) -> list[dict]:
        p = _services(request).pipeline
        if p is None:
            return []
        return [
            {"key": s.key, "domain": s.domain, "label": s.label, "fields": [f.name for f in s.fields]}
            for s in p.registry.types.values()
        ]

    @app.post("/v1/cases/analyze", response_model=CaseResult, dependencies=auth)
    async def analyze(
        request: Request,
        files: list[UploadFile] = File(..., description="Document images"),
        doc_types: list[str] = Form(..., description="Document type per file, same order"),
    ) -> CaseResult:
        """Synchronous: runs the pipeline in the request. Nothing is stored."""
        svc = _services(request)
        docs = await _read_documents(svc, files, doc_types)
        return await svc.pipeline.run(docs)

    @app.post("/v1/cases", status_code=202, dependencies=auth)
    async def create_case(
        request: Request,
        files: list[UploadFile] = File(..., description="Document images"),
        doc_types: list[str] = Form(..., description="Document type per file, same order"),
        callback_url: str | None = Form(None, description="Webhook called with the result"),
    ) -> dict:
        """Asynchronous: stores the images, queues the case, returns its id."""
        svc = _services(request)
        if svc.cases is None or svc.objects is None:
            raise HTTPException(503, "Case queue not configured; set KHATTI_DATABASE_URL")
        if callback_url:
            try:
                validate_callback_url(callback_url, svc.webhook_allowed_hosts)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        docs = await _read_documents(svc, files, doc_types)

        case_id = uuid.uuid4()
        stored = []
        for i, d in enumerate(docs):
            key = f"cases/{case_id}/{i}"
            await svc.objects.put(key, d.image, d.mime_type)
            stored.append(StoredDocument(i, d.doc_type, key, d.mime_type))
        await svc.cases.create_case(case_id, stored, callback_url)
        return {"id": str(case_id), "status": "queued"}

    @app.get("/v1/cases/{case_id}", dependencies=auth)
    async def get_case(request: Request, case_id: uuid.UUID) -> dict:
        svc = _services(request)
        if svc.cases is None:
            raise HTTPException(503, "Case queue not configured; set KHATTI_DATABASE_URL")
        case = await svc.cases.get_case(case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        return case

    return app


app = create_app()
