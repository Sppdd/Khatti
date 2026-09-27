"""HTTP API for the KYC Document Agent."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile

from .config import load_settings
from .llm import ChatClient
from .models import CaseResult, DocumentInput, DocumentType
from .orchestrator import NemotronRouter
from .pipeline import KycPipeline
from .readers import VisionChatReader

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def pipeline_from_env() -> KycPipeline | None:
    settings = load_settings()
    if not settings.readers:
        return None
    readers = [VisionChatReader(ChatClient(r, settings.timeout_s)) for r in settings.readers]
    router = NemotronRouter(ChatClient(settings.router, settings.timeout_s)) if settings.router else None
    return KycPipeline(readers, router, settings.min_confidence)


def create_app(pipeline: KycPipeline | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.pipeline is None:
            app.state.pipeline = pipeline_from_env()
        yield

    app = FastAPI(title="Khatti KYC Document Agent", version="0.1.0", lifespan=lifespan)
    app.state.pipeline = pipeline

    @app.get("/health")
    async def health(request: Request) -> dict:
        p: KycPipeline | None = request.app.state.pipeline
        return {
            "status": "ok" if p else "unconfigured",
            "readers": [r.name for r in p.readers] if p else [],
            "router": p.router.name if p and p.router else None,
        }

    @app.post("/v1/kyc/cases", response_model=CaseResult)
    async def create_case(
        request: Request,
        files: list[UploadFile] = File(..., description="Document images"),
        doc_types: list[DocumentType] = Form(..., description="Document type per file, same order"),
    ) -> CaseResult:
        p: KycPipeline | None = request.app.state.pipeline
        if p is None:
            raise HTTPException(503, "No image readers configured; set KHATTI_READERS")
        if len(files) != len(doc_types):
            raise HTTPException(422, "files and doc_types must have the same length")

        docs = []
        for f, t in zip(files, doc_types):
            data = await f.read()
            if not data:
                raise HTTPException(422, f"{f.filename} is empty")
            if len(data) > MAX_IMAGE_BYTES:
                raise HTTPException(413, f"{f.filename} exceeds {MAX_IMAGE_BYTES} bytes")
            docs.append(DocumentInput(doc_type=t, image=data, mime_type=f.content_type or "image/jpeg"))
        return await p.run(docs)

    return app


app = create_app()
