"""HTTP API for the KYC Document Agent."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .bridge import Bridge, BridgeCompleter, BridgeError, bridge_from_env
from .bridge_api import router as bridge_router
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


def require_key(request: Request) -> None:
    key = request.app.state.api_key
    if key and request.headers.get("authorization") != f"Bearer {key}":
        raise HTTPException(401, "Missing or wrong API key")


def create_app(
    pipeline: KycPipeline | None = None,
    extractor: PhotoExtractor | None = None,
    api_key: str | None = None,
    bridge: Bridge | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.pipeline is None:
            app.state.pipeline = pipeline_from_env()
        if app.state.extractor is None:
            app.state.extractor = extractor_from_env()
        if app.state.api_key is None:
            app.state.api_key = load_settings().api_key
        if app.state.bridge is None:
            app.state.bridge = bridge_from_env(load_settings().timeout_s)
        yield
        if app.state.bridge is not None:
            await app.state.bridge.http.aclose()

    app = FastAPI(title="Khatti API", version="0.2.0", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.extractor = extractor
    app.state.api_key = api_key
    app.state.bridge = bridge
    app.include_router(bridge_router, dependencies=[Depends(require_key)])
    # Browsers only (Expo web, dashboards); native apps do not need CORS.
    if origins := os.getenv("KHATTI_CORS_ORIGINS"):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in origins.split(",")],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health(request: Request) -> dict:
        p: KycPipeline | None = request.app.state.pipeline
        return {
            "status": "ok" if p or request.app.state.extractor else "unconfigured",
            "readers": [r.name for r in p.readers] if p else [],
            "router": p.router.name if p and p.router else None,
            "extractor": request.app.state.extractor.model if request.app.state.extractor else None,
            "auth": bool(request.app.state.api_key),
            "providers": [p["name"] for p in request.app.state.bridge.describe()] if request.app.state.bridge else [],
        }

    @app.post("/v1/extract", response_model=ExtractResult, dependencies=[Depends(require_key)])
    async def extract(
        request: Request,
        file: UploadFile = File(..., description="Any photo"),
        mode: ExtractMode = Form(ExtractMode.AUTO),
        model: str | None = Form(None, description="Any bridged vision model, e.g. 'nebius/<model id>'"),
    ) -> ExtractResult:
        x: PhotoExtractor | None = request.app.state.extractor
        if model:
            if request.app.state.bridge is None:
                raise HTTPException(503, "Bridge not configured")
            x = PhotoExtractor(BridgeCompleter(request.app.state.bridge, model), model)
        if x is None:
            raise HTTPException(503, "No extractor configured; set KHATTI_EXTRACTOR or KHATTI_READERS, or pass a model")
        data = await _read_image(file)
        try:
            return await x.extract(data, file.content_type or "image/jpeg", mode)
        except BridgeError as exc:
            raise HTTPException(exc.status if exc.status < 500 else 502, str(exc)) from exc
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
