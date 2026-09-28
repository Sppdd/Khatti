"""Postgres data layer: tenant-scoped transactions (RLS), hash-chained audit log, job queue."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import AsyncIterator

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
LEASE = timedelta(minutes=5)
GENESIS = "0" * 64


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12).replace('-', 'x').replace('_', 'y')}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class Store:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str, migrate: bool = True, max_size: int = 10) -> "Store":
        pool = AsyncConnectionPool(
            database_url, min_size=1, max_size=max_size, open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        await pool.open()
        store = cls(pool)
        if migrate:
            await store.migrate()
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        # API and workers start together; serialise so concurrent DDL cannot collide.
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('khatti.migrate'))")
            await conn.execute(SCHEMA)

    # ------------------------------------------------------------ transactions

    @asynccontextmanager
    async def tx(self, tenant_id: uuid.UUID | str) -> AsyncIterator[AsyncConnection]:
        """Transaction with Row-Level Security scoped to one tenant."""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("SELECT set_config('khatti.tenant_id', %s, true)", (str(tenant_id),))
            yield conn

    @asynccontextmanager
    async def system_tx(self) -> AsyncIterator[AsyncConnection]:
        """Transaction without a tenant: only non-RLS tables (tenants, api_keys, jobs, deliveries)."""
        async with self.pool.connection() as conn, conn.transaction():
            yield conn

    # ------------------------------------------------------------ tenants and keys

    async def create_tenant(self, name: str) -> uuid.UUID:
        tid = uuid.uuid4()
        async with self.system_tx() as conn:
            await conn.execute("INSERT INTO tenants (id, name) VALUES (%s, %s)", (tid, name))
        return tid

    async def create_api_key(self, tenant_id: uuid.UUID, label: str = "") -> str:
        key = "kh_" + secrets.token_urlsafe(32)
        async with self.system_tx() as conn:
            await conn.execute(
                "INSERT INTO api_keys (id, tenant_id, key_hash, label) VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), tenant_id, hash_key(key), label),
            )
        return key

    async def resolve_api_key(self, key: str) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Returns (tenant_id, key_id) for an active key."""
        async with self.system_tx() as conn:
            cur = await conn.execute(
                "SELECT id, tenant_id FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL", (hash_key(key),)
            )
            row = await cur.fetchone()
        return (row["tenant_id"], row["id"]) if row else None

    # ------------------------------------------------------------ audit (hash-chained)

    @staticmethod
    async def audit(conn: AsyncConnection, tenant_id, session_id: str | None, actor: str, event: str, detail: dict) -> None:
        # Serialise appends per tenant so the chain has no forks.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"audit:{tenant_id}",))
        cur = await conn.execute(
            "SELECT hash FROM audit_log WHERE tenant_id = %s ORDER BY id DESC LIMIT 1", (tenant_id,)
        )
        row = await cur.fetchone()
        prev = row["hash"] if row else GENESIS
        body = {"tenant_id": str(tenant_id), "session_id": session_id, "actor": actor, "event": event, "detail": detail}
        digest = hashlib.sha256((prev + _canonical(body)).encode()).hexdigest()
        await conn.execute(
            "INSERT INTO audit_log (tenant_id, session_id, actor, event, detail, prev_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tenant_id, session_id, actor, event, Jsonb(detail), prev, digest),
        )

    async def verify_audit_chain(self, tenant_id) -> bool:
        async with self.tx(tenant_id) as conn:
            cur = await conn.execute(
                "SELECT session_id, actor, event, detail, prev_hash, hash FROM audit_log WHERE tenant_id = %s ORDER BY id",
                (tenant_id,),
            )
            prev = GENESIS
            for r in await cur.fetchall():
                body = {"tenant_id": str(tenant_id), "session_id": r["session_id"], "actor": r["actor"],
                        "event": r["event"], "detail": r["detail"]}
                if r["prev_hash"] != prev or hashlib.sha256((prev + _canonical(body)).encode()).hexdigest() != r["hash"]:
                    return False
                prev = r["hash"]
        return True

    # ------------------------------------------------------------ job queue

    @staticmethod
    async def enqueue(conn: AsyncConnection, tenant_id, session_id: str) -> None:
        await conn.execute("INSERT INTO jobs (tenant_id, session_id) VALUES (%s, %s)", (tenant_id, session_id))

    async def claim_job(self) -> dict | None:
        async with self.system_tx() as conn:
            cur = await conn.execute(
                """
                UPDATE jobs SET status = 'processing', attempts = attempts + 1,
                                locked_until = now() + %s, updated_at = now()
                 WHERE id = (SELECT id FROM jobs
                              WHERE status = 'queued' OR (status = 'processing' AND locked_until < now())
                              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
             RETURNING id, tenant_id, session_id, attempts
                """,
                (LEASE,),
            )
            return await cur.fetchone()

    async def finish_job(self, job_id: int, error: str | None = None, retry: bool = False) -> None:
        status = "done" if error is None else ("queued" if retry else "failed")
        async with self.system_tx() as conn:
            await conn.execute(
                "UPDATE jobs SET status = %s, error = %s, locked_until = NULL, updated_at = now() WHERE id = %s",
                (status, error, job_id),
            )

    # ------------------------------------------------------------ webhook deliveries

    async def claim_delivery(self) -> dict | None:
        async with self.system_tx() as conn:
            cur = await conn.execute(
                """
                UPDATE webhook_deliveries SET attempts = attempts + 1, locked_until = now() + interval '1 minute'
                 WHERE id = (SELECT id FROM webhook_deliveries
                              WHERE status = 'pending' AND next_attempt_at <= now()
                                AND (locked_until IS NULL OR locked_until < now())
                              ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1)
             RETURNING id, tenant_id, webhook_id, event, payload, attempts, created_at
                """
            )
            return await cur.fetchone()

    async def finish_delivery(self, delivery_id: int, delivered: bool, last_status: str, next_in: timedelta | None) -> None:
        status = "delivered" if delivered else ("pending" if next_in is not None else "failed")
        async with self.system_tx() as conn:
            await conn.execute(
                """UPDATE webhook_deliveries SET status = %s, last_status = %s, locked_until = NULL,
                          next_attempt_at = now() + %s WHERE id = %s""",
                (status, last_status, next_in or timedelta(0), delivery_id),
            )
