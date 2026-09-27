"""HTTP API for the KYC Document Agent."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile

from .config import load_settings
from .extract import ExtractMode, ExtractResult, PhotoExtractor
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


def extractor_from_env() -> PhotoExtractor | None:
    settings = load_settings()
    if settings.extractor is None:
        return None
    return PhotoExtractor(ChatClient(settings.extractor, settings.timeout_s), settings.extractor.model)


async def _read_image(f: UploadFile) -> bytes:
    data = await f.read()
    if not data:
        raise HTTPException(422, f"{f.filename} is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"{f.filename} exceeds {MAX_IMAGE_BYTES} bytes")
    return data


def create_app(
    pipeline: KycPipeline | None = None,
    extractor: PhotoExtractor | None = None,
    api_key: str | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.pipeline is None:
            app.state.pipeline = pipeline_from_env()
        if app.state.extractor is None:
            app.state.extractor = extractor_from_env()
        if app.state.api_key is None:
            app.state.api_key = load_settings().api_key
        yield

    app = FastAPI(title="Khatti API", version="0.2.0", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.extractor = extractor
    app.state.api_key = api_key

    def require_key(request: Request) -> None:
        key = request.app.state.api_key
        if key and request.headers.get("authorization") != f"Bearer {key}":
            raise HTTPException(401, "Missing or wrong API key")

    @app.get("/health")
    async def health(request: Request) -> dict:
        p: KycPipeline | None = request.app.state.pipeline
        return {
            "status": "ok" if p or request.app.state.extractor else "unconfigured",
            "readers": [r.name for r in p.readers] if p else [],
            "router": p.router.name if p and p.router else None,
            "extractor": request.app.state.extractor.model if request.app.state.extractor else None,
            "auth": bool(request.app.state.api_key),
        }

    @app.post("/v1/extract", response_model=ExtractResult, dependencies=[Depends(require_key)])
    async def extract(
        request: Request,
        file: UploadFile = File(..., description="Any photo"),
        mode: ExtractMode = Form(ExtractMode.AUTO),
    ) -> ExtractResult:
        x: PhotoExtractor | None = request.app.state.extractor
        if x is None:
            raise HTTPException(503, "No extractor configured; set KHATTI_EXTRACTOR or KHATTI_READERS")
        data = await _read_image(file)
        try:
            return await x.extract(data, file.content_type or "image/jpeg", mode)
        except ValueError as exc:
            raise HTTPException(502, f"Model reply was not usable JSON: {exc}") from exc
        except Exception as exc:  # upstream HTTP/network failure
            raise HTTPException(502, f"Extractor failed: {type(exc).__name__}: {exc}") from exc

    @app.post("/v1/kyc/cases", response_model=CaseResult, dependencies=[Depends(require_key)])
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
            data = await _read_image(f)
            docs.append(DocumentInput(doc_type=t, image=data, mime_type=f.content_type or "image/jpeg"))
        return await p.run(docs)

    return app


app = create_app()
