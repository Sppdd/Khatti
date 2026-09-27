# Khatti — Arabic document agent that knows when to hand over

Arabic-first reader for Iraqi onboarding documents (national ID, passport, residence
card). It reads each document with an ensemble of vision models, cross-checks the
results, and hands the case to a human whenever it is unsure. *Blank and flagged beats invented.*

Built for the Nebius × NVIDIA hackathon, **Best Apps and Agents** track. See
[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the plan, findings and build status.

## Why an ensemble

None of the NVIDIA vision/OCR models we could verify list Arabic, and Token Factory's
public Nemotron endpoints are text-only. So an ensemble of image readers (at least one
NVIDIA) does the perception, **disagreement between readers is the confidence signal**,
and Nemotron does the reasoning and routing.

## Pipeline

| Step | What happens | Model |
|---|---|---|
| Read | Every reader reads every document in parallel | NVIDIA Nemotron 3 Nano Omni (Nebius Serverless Endpoint) + an Arabic-capable open VLM |
| Structure | Reader replies that are not valid JSON are mapped onto the schema | Nemotron 3 Super |
| Merge | Arabic normalisation, then per-field voting. A reader that failed or is unavailable counts as a missing vote | — |
| Check | Generic quality checks + the document type's validators + cross-document rules | — (deterministic) |
| Route | Decides inside guardrails (below) | Nemotron 3.5 Lightning |
| Brief | Routed cases only: adjudicates across documents and writes an English + Arabic reviewer brief | Nemotron 3 Ultra |

**Guardrails.** A hard failure that all readers agree on (expired, under 18) rejects. Any
warning or confidence below `KHATTI_MIN_CONFIDENCE` goes to a human. The router can
escalate a clean case to a human, but can never approve an escalated case or reject on
its own. If the router is down, the case goes to a human. The Ultra brief never changes
the decision.

**Document types** live in a registry (`khatti/registry.py`): each type is a schema,
validators and cross-document rules. KYC types are in `khatti/kyc.py`; receipts, price
books and notes plug in the same way.

**KYC checks:** Iraqi 12-digit national ID, expiry, age ≥ 18, passport MRZ check digits
(ICAO 9303), MRZ vs. visual zone, and name/DOB match across documents.

## API

| Endpoint | Purpose |
|---|---|
| `POST /v1/cases` | Async. Stores images in Object Storage, queues the case, returns `202 {id}`. Optional `callback_url` gets a signed webhook. |
| `GET /v1/cases/{id}` | Status, decision, full result and audit trail |
| `POST /v1/cases/analyze` | Sync. Runs the pipeline in the request; stores nothing. For demos. |
| `GET /v1/document-types` | Registered types and their fields |
| `GET /health` | Configured readers and models |

All `/v1` endpoints need `Authorization: Bearer <key>` when `KHATTI_API_KEYS` is set.
Webhooks carry `X-Khatti-Signature: sha256=<HMAC of the body>`.

```bash
curl -H "Authorization: Bearer $KEY" \
     -F files=@id.jpg -F doc_types=national_id \
     -F files=@passport.jpg -F doc_types=passport \
     -F callback_url=https://bank.example/khatti-hook \
     http://localhost:8000/v1/cases
```

Every case has an audit trail (`created`, `claimed`, `decided` with each reader's
status, e.g. `national_id:nvidia-omni: unavailable`, then `webhook_delivered`).

## Run locally

```bash
cp .env.example .env          # fill in keys and reader endpoints
docker compose up --build     # Postgres + API (:8000) + worker
```

Without Docker:

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
set -a && . ./.env && set +a
.venv/bin/uvicorn khatti.api:app --reload     # API
.venv/bin/python -m khatti.worker             # worker(s): run as many as you like
```

## Test

```bash
.venv/bin/python -m pytest
# Postgres queue tests run when a database is available:
KHATTI_TEST_DATABASE_URL=postgresql://user@localhost:5432/khatti_test .venv/bin/python -m pytest
```

## Layout

```
khatti/
  api.py           FastAPI app
  worker.py        queue worker (python -m khatti.worker)
  pipeline.py      read -> merge -> check -> route -> brief
  readers.py       vision readers + Super structurer
  consensus.py     per-field voting
  validation.py    generic checks, runs registry validators and cross rules
  registry.py      DocumentType registry
  kyc.py           Iraqi KYC document types and rules
  orchestrator.py  Lightning router + Ultra reviewer, guardrails
  arabic.py        Arabic normalisation, dates, similarity
  db.py            Postgres store, SKIP LOCKED queue, audit trail
  storage.py       S3 / local object storage
  webhooks.py      signed webhook delivery
  llm.py           OpenAI-compatible client
  config.py, services.py
```
