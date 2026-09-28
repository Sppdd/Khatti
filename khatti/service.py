"""Business logic behind the API and the worker. Everything tenant-scoped runs inside
Store.tx(tenant) so Postgres Row-Level Security applies."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from psycopg.types.json import Jsonb

from . import webhooks
from .auth import Principal
from .crypto import Keyring
from .llm import CallLog, current_call_log
from .models import DocumentInput, FieldStatus, Outcome, SessionResult
from .pipeline import Pipeline
from .registry import FieldGroup, Registry
from .storage import ObjectStore
from .store import Store, new_id

MAX_SLOTS = 10
MAX_ATTEMPTS = 3


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


@dataclass
class KycService:
    store: Store
    objects: ObjectStore
    keys: Keyring
    registry: Registry
    pipeline: Pipeline | None = None
    http: httpx.AsyncClient | None = None
    webhook_allowed_hosts: frozenset[str] = frozenset()
    retention_days: int = 30

    # ------------------------------------------------------------ sessions

    async def create_session(self, p: Principal, slots: list[str], kind: str = "kyc", external_ref: str | None = None) -> dict:
        if not slots or len(slots) > MAX_SLOTS or len(set(slots)) != len(slots):
            raise Invalid(f"slots must be 1..{MAX_SLOTS} distinct document types")
        unknown = [s for s in slots if s not in self.registry.types]
        if unknown:
            raise Invalid(f"unknown slots {unknown}; known: {sorted(self.registry.types)}")
        domain = self.registry.get(slots[0]).domain
        ruleset = self.registry.rulesets.get(domain)
        sid = new_id("kyc" if kind == "kyc" else "dss")
        async with self.store.tx(p.tenant_id) as conn:
            await conn.execute(
                "INSERT INTO sessions (id, tenant_id, kind, status, slots, ruleset_version, external_ref) VALUES (%s, %s, %s, 'created', %s, %s, %s)",
                (sid, p.tenant_id, kind, Jsonb(slots), getattr(ruleset, "version", "none"), external_ref),
            )
            uploads = [await self._new_document(conn, p, sid, slot) for slot in slots]
            await self.store.audit(conn, p.tenant_id, sid, p.subject, "session.created", {"slots": slots, "kind": kind})
        return {"session_id": sid, "status": "created", "uploads": uploads}

    async def _new_document(self, conn, p: Principal, sid: str, slot: str) -> dict:
        did = new_id("doc")
        key = f"uploads/{p.tenant_id}/{did}"
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, session_id, slot, status, upload_key) VALUES (%s, %s, %s, %s, 'awaiting_upload', %s)",
            (did, p.tenant_id, sid, slot, key),
        )
        return {"slot": slot, "document_id": did, **self.objects.presign_put(key, "image/jpeg")}

    async def new_uploads(self, p: Principal, sid: str, slots: list[str]) -> dict:
        """Fresh upload URLs for some slots (retakes). Previous documents for them are superseded."""
        async with self.store.tx(p.tenant_id) as conn:
            s = await self._session_row(conn, sid)
            if s["status"] not in ("created", "retake_requested"):
                raise Conflict(f"session is {s['status']}")
            bad = [x for x in slots if x not in s["slots"]]
            if bad or not slots:
                raise Invalid(f"slots {bad or slots} are not part of this session")
            await conn.execute(
                "UPDATE documents SET status = 'superseded' WHERE session_id = %s AND slot = ANY(%s) AND status <> 'superseded'",
                (sid, slots),
            )
            uploads = [await self._new_document(conn, p, sid, slot) for slot in slots]
            await self.store.audit(conn, p.tenant_id, sid, p.subject, "session.uploads_reissued", {"slots": slots})
        return {"session_id": sid, "uploads": uploads}

    async def submit(self, p: Principal, sid: str) -> dict:
        async with self.store.tx(p.tenant_id) as conn:
            s = await self._session_row(conn, sid, lock=True)
            if s["status"] not in ("created", "retake_requested"):
                raise Conflict(f"session is {s['status']}")
            docs = await self._current_documents(conn, sid)
            missing = [d["slot"] for d in docs if d["status"] == "awaiting_upload" and not await self.objects.exists(d["upload_key"])]
            if missing:
                raise Invalid(f"no upload yet for slots {missing}")
            await conn.execute(
                "UPDATE documents SET status = 'uploaded' WHERE session_id = %s AND status = 'awaiting_upload'", (sid,)
            )
            await conn.execute(
                "UPDATE sessions SET status = 'submitted', submitted_at = now(), updated_at = now() WHERE id = %s", (sid,)
            )
            await self.store.enqueue(conn, p.tenant_id, sid)
            await self.store.audit(conn, p.tenant_id, sid, p.subject, "session.submitted", {})
        return {"session_id": sid, "status": "submitted"}

    async def _session_row(self, conn, sid: str, lock: bool = False) -> dict:
        cur = await conn.execute(f"SELECT * FROM sessions WHERE id = %s{' FOR UPDATE' if lock else ''}", (sid,))
        row = await cur.fetchone()
        if row is None:
            raise NotFound("session not found")
        return row

    async def _current_documents(self, conn, sid: str) -> list[dict]:
        cur = await conn.execute(
            "SELECT * FROM documents WHERE session_id = %s AND status NOT IN ('superseded') ORDER BY created_at", (sid,)
        )
        return await cur.fetchall()

    # ------------------------------------------------------------ processing (worker)

    async def process(self, tenant_id: uuid.UUID, sid: str) -> SessionResult:
        if self.pipeline is None:
            raise RuntimeError("pipeline not configured")
        system = Principal(tenant_id, "worker", "service", "worker")
        async with self.store.tx(tenant_id) as conn:
            s = await self._session_row(conn, sid, lock=True)
            if s["status"] not in ("submitted", "processing"):
                raise Conflict(f"session is {s['status']}")
            await conn.execute("UPDATE sessions SET status = 'processing', updated_at = now() WHERE id = %s", (sid,))
            docs = await self._current_documents(conn, sid)

        inputs, doc_ids = [], {}
        for d in docs:
            image = await self._ingest(tenant_id, d)
            inputs.append(DocumentInput(slot=d["slot"], image=image, mime_type=d["mime_type"]))
            doc_ids[d["slot"]] = d["id"]

        log = CallLog()
        token = current_call_log.set(log)
        try:
            result = await self.pipeline.run(inputs)
        finally:
            current_call_log.reset(token)

        await self._persist(system, sid, doc_ids, result, log)
        return result

    async def _ingest(self, tenant_id, d: dict) -> bytes:
        """Move a plaintext upload into an envelope-encrypted original (once)."""
        context = f"{tenant_id}/{d['id']}"
        if d["object_key"]:
            return self.keys.open(await self.objects.get(d["object_key"]), context)
        data = await self.objects.get(d["upload_key"])
        key = f"originals/{tenant_id}/{d['id']}.enc"
        await self.objects.put(key, self.keys.seal(data, context), "application/octet-stream")
        digest = hashlib.sha256(data).hexdigest()
        async with self.store.tx(tenant_id) as conn:
            await conn.execute(
                "UPDATE documents SET status = 'ingested', object_key = %s, sha256 = %s WHERE id = %s", (key, digest, d["id"])
            )
            await self.store.audit(conn, tenant_id, d["session_id"], "worker", "document.ingested", {"document_id": d["id"], "sha256": digest})
        await self.objects.delete(d["upload_key"])
        return data

    async def _persist(self, p: Principal, sid: str, doc_ids: dict[str, str], result: SessionResult, log: CallLog) -> None:
        tid = p.tenant_id
        needs_review = result.decision.outcome is Outcome.HUMAN_REVIEW
        status = "needs_review" if needs_review else "completed"
        async with self.store.tx(tid) as conn:
            await conn.execute(
                """UPDATE sessions SET status = %s, outcome = %s, session_confidence = %s, reasons = %s, result_enc = %s,
                          final_outcome = %s, decided_at = now(), updated_at = now(), error = NULL WHERE id = %s""",
                (status, result.decision.outcome.value, result.decision.session_confidence, Jsonb(result.decision.reasons),
                 self.keys.seal_json(result.model_dump(mode="json"), f"{tid}/{sid}"),
                 None if needs_review else "auto_pass", sid),
            )
            for doc in result.documents:
                spec = self.registry.get(doc.slot)
                for name, f in doc.fields.items():
                    group = spec.field(name).group
                    enc = self.keys.seal_json(f.value, f"{tid}/{sid}/{name}") if f.value is not None else None
                    mac = self.keys.lookup_hmac(name, f.value) if f.value and group is FieldGroup.DIGITS else None
                    cur = await conn.execute(
                        """INSERT INTO fields (tenant_id, session_id, document_id, name, status, confidence, value_enc, value_hmac)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (document_id, name) DO UPDATE SET status = EXCLUDED.status, confidence = EXCLUDED.confidence,
                               value_enc = EXCLUDED.value_enc, value_hmac = EXCLUDED.value_hmac, version = fields.version + 1
                           RETURNING id, version""",
                        (tid, sid, doc_ids[doc.slot], name, f.status.value, f.confidence, enc, mac),
                    )
                    row = await cur.fetchone()
                    await conn.execute(
                        "INSERT INTO field_versions (tenant_id, field_id, version, status, value_enc, source, actor) VALUES (%s, %s, %s, %s, %s, 'model', %s)",
                        (tid, row["id"], row["version"], f.status.value, enc, result.pipeline_version),
                    )
            await conn.execute("DELETE FROM checks WHERE session_id = %s", (sid,))
            for c in result.checks:
                await conn.execute(
                    "INSERT INTO checks (tenant_id, session_id, code, severity, slot, field) VALUES (%s, %s, %s, %s, %s, %s)",
                    (tid, sid, c.code, c.severity.value, c.slot, c.field),
                )
            await conn.execute(
                "INSERT INTO decisions (tenant_id, session_id, outcome, session_confidence, reasons, decided_by, pipeline_version) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tid, sid, result.decision.outcome.value, result.decision.session_confidence, Jsonb(result.decision.reasons),
                 result.decision.decided_by, result.pipeline_version),
            )
            for r in log.records:
                await conn.execute(
                    """INSERT INTO model_calls (tenant_id, session_id, endpoint, model, purpose, prompt_sha256, image_sha256,
                           latency_ms, prompt_tokens, completion_tokens, ok, output_enc, error)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (tid, sid, r.endpoint, r.model, r.purpose, r.prompt_sha256, r.image_sha256, r.latency_ms,
                     r.prompt_tokens, r.completion_tokens, r.ok,
                     self.keys.seal(r.output.encode(), f"{tid}/{sid}/call") if r.output else None, r.error),
                )
            if needs_review:
                await conn.execute("UPDATE review_items SET status = 'closed', closed_at = now() WHERE session_id = %s AND status = 'open'", (sid,))
                await conn.execute(
                    "INSERT INTO review_items (id, tenant_id, session_id, status, reasons) VALUES (%s, %s, %s, 'open', %s)",
                    (new_id("rev"), tid, sid, Jsonb(result.decision.reasons)),
                )
            await self.store.audit(conn, tid, sid, "worker", "session.decided", {
                "outcome": result.decision.outcome.value, "reasons": result.decision.reasons,
                "session_confidence": result.decision.session_confidence, "pipeline_version": result.pipeline_version,
                "readers": {f"{d.slot}:{k}": v.value for d in result.documents for k, v in d.readers.items()},
                "image_sha256": {d.slot: d.image_hashes for d in result.documents},
                "model_calls": len(log.records),
            })
            await self._emit(conn, tid, "session.needs_review" if needs_review else "session.completed",
                             {"session_id": sid, "status": status, "outcome": result.decision.outcome.value,
                              "reasons": result.decision.reasons})
            if result.retake_requests:
                await self._emit(conn, tid, "session.retake_requested",
                                 {"session_id": sid, "status": status, "retake_requests": result.retake_requests})

    async def fail(self, tenant_id, sid: str, error: str) -> None:
        async with self.store.tx(tenant_id) as conn:
            await conn.execute("UPDATE sessions SET status = 'failed', error = %s, updated_at = now() WHERE id = %s", (error, sid))
            await self.store.audit(conn, tenant_id, sid, "worker", "session.failed", {"error": error})

    # ------------------------------------------------------------ reading results

    async def get_session(self, p: Principal, sid: str, reviewer_view: bool = False) -> dict:
        async with self.store.tx(p.tenant_id) as conn:
            s = await self._session_row(conn, sid)
            docs = await self._current_documents(conn, sid)
            cur = await conn.execute("SELECT f.document_id, f.name, f.status, f.value_enc, f.version FROM fields f WHERE f.session_id = %s", (sid,))
            overrides = {(r["document_id"], r["name"]): r for r in await cur.fetchall() if r["version"] > 1}
        view = {
            "session_id": sid,
            "kind": s["kind"],
            "status": s["status"],
            "slots": s["slots"],
            "external_ref": s["external_ref"],
            "ruleset_version": s["ruleset_version"],
            "created_at": s["created_at"],
            "decided_at": s["decided_at"],
            "final_outcome": s["final_outcome"],
        }
        by_slot = {d["slot"]: d for d in docs}
        if s["status"] in ("created", "retake_requested"):
            view["uploads"] = [
                {"slot": d["slot"], "document_id": d["id"], **self.objects.presign_put(d["upload_key"], "image/jpeg")}
                for d in docs if d["status"] == "awaiting_upload"
            ]
        if s["result_enc"] is None:
            return view
        result = SessionResult.model_validate(self.keys.open_json(s["result_enc"], f"{p.tenant_id}/{sid}"))
        view["decision"] = {"outcome": result.decision.outcome.value, "session_confidence": result.decision.session_confidence,
                            "reasons": result.decision.reasons}
        view["documents"] = []
        for d in result.documents:
            row = by_slot.get(d.slot)
            did = row["id"] if row else None
            fields = {}
            for name, f in d.fields.items():
                item = {"value": f.value, "confidence": f.confidence, "status": f.status.value}
                if f.reason:
                    item["reason"] = f.reason
                if f.calendar:
                    item["calendar"] = f.calendar
                    item["calendar_converted"] = f.calendar_converted
                o = overrides.get((did, name))
                if o is not None and o["status"] == "corrected":
                    item.update(value=self.keys.open_json(o["value_enc"], f"{p.tenant_id}/{sid}/{name}") if o["value_enc"] else None,
                                status="ok", corrected=True)
                if reviewer_view:
                    item.update(candidates=f.candidates, raw_partial=f.raw_partial, sources=f.sources)
                fields[name] = item
            view["documents"].append({
                "document_id": did, "slot": d.slot, "type": d.doc_type,
                "classification_confidence": d.classification_confidence,
                "quality": d.quality.model_dump(include={"issues", "guidance_ar", "guidance_en", "metrics"}) if d.quality else None,
                "readers": {k: v.value for k, v in d.readers.items()},
                "fields": fields,
            })
        view["cross_checks"] = [c.model_dump() for c in result.cross_checks]
        view["review_summary"] = result.review_summary
        view["retake_requests"] = result.retake_requests
        if reviewer_view:
            view["checks"] = [c.model_dump(mode="json") for c in result.checks]
            view["pipeline_version"] = result.pipeline_version
        return view

    async def document_image(self, p: Principal, document_id: str) -> tuple[bytes, str]:
        async with self.store.tx(p.tenant_id) as conn:
            cur = await conn.execute("SELECT * FROM documents WHERE id = %s", (document_id,))
            d = await cur.fetchone()
            if d is None or not d["object_key"]:
                raise NotFound("image not available")
            await self.store.audit(conn, p.tenant_id, d["session_id"], p.subject, "document.image_viewed", {"document_id": document_id})
        return self.keys.open(await self.objects.get(d["object_key"]), f"{p.tenant_id}/{d['id']}"), d["mime_type"]

    # ------------------------------------------------------------ review

    async def review_queue(self, p: Principal, reason: str | None = None, limit: int = 50) -> list[dict]:
        async with self.store.tx(p.tenant_id) as conn:
            cur = await conn.execute(
                """SELECT r.id, r.session_id, r.reasons, r.created_at, s.session_confidence, s.external_ref,
                          extract(epoch FROM now() - r.created_at)::int AS age_seconds
                     FROM review_items r JOIN sessions s ON s.id = r.session_id
                    WHERE r.status = 'open' AND (%s::text IS NULL OR r.reasons ? %s::text)
                    ORDER BY r.created_at LIMIT %s""",
                (reason, reason, min(max(limit, 1), 200)),
            )
            return await cur.fetchall()

    async def review_item(self, p: Principal, item_id: str) -> dict:
        async with self.store.tx(p.tenant_id) as conn:
            cur = await conn.execute("SELECT * FROM review_items WHERE id = %s", (item_id,))
            item = await cur.fetchone()
        if item is None:
            raise NotFound("review item not found")
        return {"review_item_id": item_id, "status": item["status"], "reasons": item["reasons"],
                "session": await self.get_session(p, item["session_id"], reviewer_view=True)}

    async def review_decision(
        self, p: Principal, item_id: str, action: str, corrections: dict[str, str | None] | None = None,
        retake_slots: list[str] | None = None, note: str | None = None,
    ) -> dict:
        if action not in ("approve", "reject", "request_retake"):
            raise Invalid("action must be approve, reject or request_retake")
        corrections = corrections or {}
        retake_slots = retake_slots or []
        tid = p.tenant_id
        async with self.store.tx(tid) as conn:
            cur = await conn.execute("SELECT * FROM review_items WHERE id = %s FOR UPDATE", (item_id,))
            item = await cur.fetchone()
            if item is None:
                raise NotFound("review item not found")
            if item["status"] != "open":
                raise Conflict("review item is already closed")
            sid = item["session_id"]
            s = await self._session_row(conn, sid, lock=True)
            if action == "request_retake" and (not retake_slots or any(x not in s["slots"] for x in retake_slots)):
                raise Invalid("request_retake needs retake_slots from this session")

            # Per-field corrections: "<slot>.<field>" -> value. Stored as new versions (labelled data).
            docs = {d["slot"]: d for d in await self._current_documents(conn, sid)}
            for ref, value in corrections.items():
                slot, _, name = ref.partition(".")
                if slot not in docs or name not in self.registry.get(slot).field_names:
                    raise Invalid(f"unknown field '{ref}'")
                enc = self.keys.seal_json(value, f"{tid}/{sid}/{name}") if value is not None else None
                cur = await conn.execute(
                    """UPDATE fields SET status = 'corrected', value_enc = %s, version = version + 1
                        WHERE document_id = %s AND name = %s RETURNING id, version""",
                    (enc, docs[slot]["id"], name),
                )
                row = await cur.fetchone()
                if row is None:
                    raise Invalid(f"field '{ref}' has no machine reading to correct")
                await conn.execute(
                    "INSERT INTO field_versions (tenant_id, field_id, version, status, value_enc, source, actor) VALUES (%s, %s, %s, 'corrected', %s, 'reviewer', %s)",
                    (tid, row["id"], row["version"], enc, p.subject),
                )

            await conn.execute(
                "INSERT INTO review_actions (tenant_id, review_item_id, actor, action, corrections_enc, retake_slots, note) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tid, item_id, p.subject, action, self.keys.seal_json(corrections, f"{tid}/{item_id}") if corrections else None,
                 Jsonb(retake_slots), note),
            )
            await conn.execute("UPDATE review_items SET status = 'closed', closed_at = now() WHERE id = %s", (item_id,))
            if action == "request_retake":
                await conn.execute(
                    "UPDATE documents SET status = 'superseded' WHERE session_id = %s AND slot = ANY(%s) AND status <> 'superseded'",
                    (sid, retake_slots),
                )
                for slot in retake_slots:
                    await self._new_document(conn, p, sid, slot)
                await conn.execute("UPDATE sessions SET status = 'retake_requested', updated_at = now() WHERE id = %s", (sid,))
                event, status = "session.retake_requested", "retake_requested"
            else:
                await conn.execute(
                    "UPDATE sessions SET status = 'completed', final_outcome = %s, updated_at = now() WHERE id = %s", (action, sid)
                )
                event, status = "session.completed", "completed"
            await self.store.audit(conn, tid, sid, p.subject, f"review.{action}", {
                "review_item_id": item_id, "corrected_fields": sorted(corrections), "retake_slots": retake_slots,
            })
            await self._emit(conn, tid, event, {"session_id": sid, "status": status, "final_outcome": None if action == "request_retake" else action,
                                                "retake_slots": retake_slots})
        return {"review_item_id": item_id, "session_id": sid, "status": status}

    # ------------------------------------------------------------ webhooks

    async def register_webhook(self, p: Principal, url: str, events: list[str]) -> dict:
        webhooks.validate_url(url, self.webhook_allowed_hosts)
        bad = [e for e in events if e not in webhooks.EVENTS]
        if not events or bad:
            raise Invalid(f"events must be a non-empty subset of {sorted(webhooks.EVENTS)}")
        wid, secret = new_id("whk"), "whsec_" + secrets.token_urlsafe(24)
        async with self.store.tx(p.tenant_id) as conn:
            await conn.execute(
                "INSERT INTO webhooks (id, tenant_id, url, secret_enc, events) VALUES (%s, %s, %s, %s, %s)",
                (wid, p.tenant_id, url, self.keys.seal(secret.encode(), f"{p.tenant_id}/{wid}"), events),
            )
            await self.store.audit(conn, p.tenant_id, None, p.subject, "webhook.registered", {"webhook_id": wid, "url": url, "events": events})
        return {"id": wid, "url": url, "events": events, "secret": secret}  # the secret is shown once

    async def list_webhooks(self, p: Principal) -> list[dict]:
        async with self.store.tx(p.tenant_id) as conn:
            cur = await conn.execute("SELECT id, url, events, active, created_at FROM webhooks ORDER BY created_at")
            return await cur.fetchall()

    async def delete_webhook(self, p: Principal, wid: str) -> None:
        async with self.store.tx(p.tenant_id) as conn:
            cur = await conn.execute("UPDATE webhooks SET active = false WHERE id = %s RETURNING id", (wid,))
            if await cur.fetchone() is None:
                raise NotFound("webhook not found")
            await self.store.audit(conn, p.tenant_id, None, p.subject, "webhook.deactivated", {"webhook_id": wid})

    async def _emit(self, conn, tenant_id, event: str, payload: dict) -> None:
        cur = await conn.execute("SELECT id FROM webhooks WHERE active AND %s = ANY(events)", (event,))
        for w in await cur.fetchall():
            await conn.execute(
                "INSERT INTO webhook_deliveries (tenant_id, webhook_id, event, payload) VALUES (%s, %s, %s, %s)",
                (tenant_id, w["id"], event, Jsonb({"event": event, "created_at": datetime.now(timezone.utc).isoformat(), "data": payload})),
            )

    async def deliver_one(self) -> bool:
        """Deliver one due webhook. Returns False when nothing is due."""
        d = await self.store.claim_delivery()
        if d is None:
            return False
        async with self.store.tx(d["tenant_id"]) as conn:
            cur = await conn.execute("SELECT url, secret_enc, active FROM webhooks WHERE id = %s", (d["webhook_id"],))
            w = await cur.fetchone()
        if w is None or not w["active"]:
            await self.store.finish_delivery(d["id"], False, "webhook inactive", None)
            return True
        secret = self.keys.open(w["secret_enc"], f"{d['tenant_id']}/{d['webhook_id']}").decode()
        ok, status = await webhooks.post(self.http or httpx.AsyncClient(timeout=10), w["url"], d["payload"], secret)
        await self.store.finish_delivery(d["id"], ok, status, None if ok else webhooks.next_backoff(d["attempts"], d["created_at"]))
        return True

    # ------------------------------------------------------------ retention

    async def retention_sweep(self, now: datetime | None = None) -> int:
        """Delete KYC originals N days after the final decision. Returns objects deleted."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.retention_days)
        deleted = 0
        async with self.store.system_tx() as conn:
            cur = await conn.execute("SELECT id FROM tenants")
            tenants = [r["id"] for r in await cur.fetchall()]
        for tid in tenants:
            async with self.store.tx(tid) as conn:
                cur = await conn.execute(
                    """SELECT d.id, d.session_id, d.object_key FROM documents d JOIN sessions s ON s.id = d.session_id
                        WHERE s.kind = 'kyc' AND s.status = 'completed' AND s.updated_at < %s
                          AND d.object_key IS NOT NULL AND d.deleted_at IS NULL""",
                    (cutoff,),
                )
                for d in await cur.fetchall():
                    await self.objects.delete(d["object_key"])
                    await conn.execute(
                        "UPDATE documents SET status = 'deleted', deleted_at = now(), object_key = NULL WHERE id = %s", (d["id"],)
                    )
                    await self.store.audit(conn, tid, d["session_id"], "retention", "document.deleted",
                                           {"document_id": d["id"], "retention_days": self.retention_days})
                    deleted += 1
        return deleted


__all__ = ["KycService", "NotFound", "Conflict", "Invalid", "MAX_ATTEMPTS", "FieldStatus"]


async def session_of_document(service: KycService, p: Principal, document_id: str) -> str:
    async with service.store.tx(p.tenant_id) as conn:
        cur = await conn.execute("SELECT session_id FROM documents WHERE id = %s", (document_id,))
        row = await cur.fetchone()
    if row is None:
        raise NotFound("document not found")
    return row["session_id"]
