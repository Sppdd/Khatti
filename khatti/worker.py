"""Pipeline worker: claims queued cases from Postgres and processes them.

Run with `python -m khatti.worker`. Any number of workers can run side by side;
SKIP LOCKED hands each case to exactly one of them.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .models import DocumentInput
from .services import Services, services_from_env
from .webhooks import deliver

log = logging.getLogger("khatti.worker")
MAX_ATTEMPTS = 3


async def process_one(svc: Services) -> bool:
    """Process one queued case. Returns False when the queue is empty."""
    assert svc.cases and svc.objects and svc.pipeline
    claimed = await svc.cases.claim_next()
    if claimed is None:
        return False

    try:
        docs = [
            DocumentInput(doc_type=d.doc_type, image=await svc.objects.get(d.object_key), mime_type=d.mime_type)
            for d in claimed.documents
        ]
        result = await svc.pipeline.run(docs)
    except Exception as exc:
        log.exception("case %s failed", claimed.id)
        await svc.cases.fail(claimed.id, f"{type(exc).__name__}: {exc}", retry=claimed.attempts < MAX_ATTEMPTS)
        return True

    payload = result.model_dump(mode="json")
    await svc.cases.complete(claimed.id, payload)

    if claimed.callback_url:
        ok, status = await deliver(
            svc.http, claimed.callback_url, {"case_id": str(claimed.id), "status": "done", "result": payload}, svc.webhook_secret
        )
        await svc.cases.audit(claimed.id, "webhook_delivered" if ok else "webhook_failed", {"status": status})
    return True


async def run(poll_interval_s: float = 1.0) -> None:
    svc = await services_from_env()
    if not (svc.cases and svc.pipeline):
        raise SystemExit("worker needs KHATTI_DATABASE_URL and KHATTI_READERS")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    try:
        while not stop.is_set():
            if not await process_one(svc):
                try:
                    await asyncio.wait_for(stop.wait(), poll_interval_s)
                except asyncio.TimeoutError:
                    pass
    finally:
        await svc.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
