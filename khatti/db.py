"""Postgres case store, job queue (SELECT ... FOR UPDATE SKIP LOCKED) and audit trail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id            uuid PRIMARY KEY,
    status        text NOT NULL CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    decision      text,
    result        jsonb,
    error         text,
    callback_url  text,
    attempts      int NOT NULL DEFAULT 0,
    locked_until  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cases_pending_idx ON cases (created_at) WHERE status IN ('queued', 'processing');

CREATE TABLE IF NOT EXISTS case_documents (
    case_id     uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    position    int NOT NULL,
    doc_type    text NOT NULL,
    object_key  text NOT NULL,
    mime_type   text NOT NULL,
    PRIMARY KEY (case_id, position)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id       bigserial PRIMARY KEY,
    case_id  uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    at       timestamptz NOT NULL DEFAULT now(),
    event    text NOT NULL,
    detail   jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_events_case_idx ON audit_events (case_id, id);
"""

# A claimed case whose worker died becomes claimable again after the lease expires.
LEASE = timedelta(minutes=5)


@dataclass(frozen=True)
class StoredDocument:
    position: int
    doc_type: str
    object_key: str
    mime_type: str


@dataclass(frozen=True)
class ClaimedCase:
    id: uuid.UUID
    attempts: int
    callback_url: str | None
    documents: list[StoredDocument]


class CaseStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "CaseStore":
        pool = AsyncConnectionPool(database_url, min_size=1, max_size=10, open=False, kwargs={"autocommit": True})
        await pool.open()
        store = cls(pool)
        await store.migrate()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        # API and workers start together; serialise so concurrent CREATE ... IF NOT EXISTS cannot collide.
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('khatti.migrate'))")
            await conn.execute(SCHEMA)

    async def create_case(self, case_id: uuid.UUID, documents: list[StoredDocument], callback_url: str | None) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO cases (id, status, callback_url) VALUES (%s, 'queued', %s)", (case_id, callback_url)
            )
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO case_documents (case_id, position, doc_type, object_key, mime_type) VALUES (%s, %s, %s, %s, %s)",
                    [(case_id, d.position, d.doc_type, d.object_key, d.mime_type) for d in documents],
                )
            await self._audit(conn, case_id, "created", {"documents": [d.doc_type for d in documents]})

    async def claim_next(self) -> ClaimedCase | None:
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                UPDATE cases
                   SET status = 'processing', attempts = attempts + 1,
                       locked_until = now() + %s, updated_at = now()
                 WHERE id = (
                        SELECT id FROM cases
                         WHERE status = 'queued' OR (status = 'processing' AND locked_until < now())
                         ORDER BY created_at
                         FOR UPDATE SKIP LOCKED
                         LIMIT 1)
             RETURNING id, attempts, callback_url
                """,
                (LEASE,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            case_id, attempts, callback_url = row
            cur = await conn.execute(
                "SELECT position, doc_type, object_key, mime_type FROM case_documents WHERE case_id = %s ORDER BY position",
                (case_id,),
            )
            docs = [StoredDocument(*r) for r in await cur.fetchall()]
            await self._audit(conn, case_id, "claimed", {"attempt": attempts})
        return ClaimedCase(case_id, attempts, callback_url, docs)

    async def complete(self, case_id: uuid.UUID, result: dict) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE cases SET status = 'done', decision = %s, result = %s, error = NULL, locked_until = NULL, updated_at = now() WHERE id = %s",
                (result["decision"], Jsonb(result), case_id),
            )
            readers = {
                f"{doc['doc_type']}:{name}": status
                for doc in result["documents"]
                for name, status in doc["readers"].items()
            }
            await self._audit(
                conn,
                case_id,
                "decided",
                {"decision": result["decision"], "router": result["router"], "confidence": result["confidence"], "readers": readers},
            )

    async def fail(self, case_id: uuid.UUID, error: str, retry: bool) -> None:
        status = "queued" if retry else "failed"
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE cases SET status = %s, error = %s, locked_until = NULL, updated_at = now() WHERE id = %s",
                (status, error, case_id),
            )
            await self._audit(conn, case_id, "retry" if retry else "failed", {"error": error})

    async def audit(self, case_id: uuid.UUID, event: str, detail: dict) -> None:
        async with self.pool.connection() as conn:
            await self._audit(conn, case_id, event, detail)

    async def get_case(self, case_id: uuid.UUID) -> dict | None:
        async with self.pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                "SELECT id, status, decision, result, error, attempts, created_at, updated_at FROM cases WHERE id = %s",
                (case_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT at, event, detail FROM audit_events WHERE case_id = %s ORDER BY id", (case_id,)
            )
            row["audit"] = await cur.fetchall()
            return row

    @staticmethod
    async def _audit(conn, case_id: uuid.UUID, event: str, detail: dict) -> None:
        await conn.execute(
            "INSERT INTO audit_events (case_id, event, detail) VALUES (%s, %s, %s)", (case_id, event, Jsonb(detail))
        )
