"""Pipeline worker: processes queued sessions, delivers webhooks, and sweeps retention.

Run with `python -m khatti.worker`. Any number of workers can run side by side;
SKIP LOCKED hands each job and each delivery to exactly one of them.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from .service import MAX_ATTEMPTS, KycService
from .services import services_from_env

log = logging.getLogger("khatti.worker")
RETENTION_EVERY_S = 3600


async def process_one(service: KycService) -> bool:
    """Process one queued session. Returns False when the queue is empty."""
    job = await service.store.claim_job()
    if job is None:
        return False
    try:
        await service.process(job["tenant_id"], job["session_id"])
    except Exception as exc:
        log.exception("session %s failed (attempt %s)", job["session_id"], job["attempts"])
        error = f"{type(exc).__name__}: {exc}"
        retry = job["attempts"] < MAX_ATTEMPTS
        if not retry:
            await service.fail(job["tenant_id"], job["session_id"], error)
        await service.store.finish_job(job["id"], error, retry=retry)
        return True
    await service.store.finish_job(job["id"])
    return True


async def run(poll_interval_s: float = 1.0) -> None:
    svc = await services_from_env()
    if svc.service is None or svc.pipeline is None:
        raise SystemExit("worker needs KHATTI_DATABASE_URL and KHATTI_READERS")
    service = svc.service
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    last_sweep = 0.0
    try:
        while not stop.is_set():
            busy = await process_one(service)
            busy = await service.deliver_one() or busy
            if time.monotonic() - last_sweep > RETENTION_EVERY_S:
                n = await service.retention_sweep()
                if n:
                    log.info("retention: deleted %s originals", n)
                last_sweep = time.monotonic()
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), poll_interval_s)
                except asyncio.TimeoutError:
                    pass
    finally:
        await svc.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
