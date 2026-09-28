# Khatti — Arabic document agent that knows when to hand over

Khatti reads Iraqi onboarding documents (National Card front and back, commercial
registration, tax card), cross-checks them, and hands the file to a human when it is
unsure. **Blank and flagged beats invented.**

Nebius × NVIDIA hackathon, **Best Apps and Agents** track. Plan, findings and timeline:
[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).

## The Arabic gap, and why this is an ensemble

None of the NVIDIA vision/OCR models we could verify list Arabic, and Token Factory's
public Nemotron endpoints are text-only. So:

- **Perception** is an ensemble of image readers: NVIDIA Nemotron 3 Nano Omni (self-hosted
  on a Nebius Serverless Endpoint) plus an Arabic-capable open VLM on Token Factory,
  sampled 3× for self-consistency. **Disagreement between readers is the confidence signal.**
- **Nemotron does the reasoning**: Lightning classifies, Super structures (copy-only),
  Ultra adjudicates residual ambiguity and briefs the reviewer on routed files only.
- **Validation and the decision are deterministic Python**: auditable and testable.

## Pipeline (plan E)

| Stage | What happens | Where |
|---|---|---|
| Capture quality | Blur (Laplacian variance), exposure, glare blobs, card corners / corner cut, finger occlusion, resolution → Arabic/English retake guidance | `quality.py` (OpenCV) |
| Preprocess | Perspective warp to the card, CLAHE; readers get original + enhanced; every image hashed | `preprocess.py` |
| Transcribe | Every reader returns `lines[] {line_id, text, bbox}` and a caption; illegible characters become `?` | `readers.py` |
| Classify | Aspect ratio (ID-1 vs A4) + label evidence + Lightning over captions → "wrong document in slot" | `classify.py` |
| Structure | Super maps lines → fields. **Copy-only, enforced in code**: a value survives only if found verbatim in the cited lines, and the stored value is cut from the line itself | `structuring.py` |
| Merge | Per-field voting across readers/samples; features → calibrated confidence → field status | `fields.py`, `calibration.py` |
| Validate | Formats (template parameters, no invented checksums), enums, Gregorian/Hijri dates, expiry, issue ≤ today, age ≥ 18, card lifetime | `validation.py`, `kyc.py` |
| Cross-check | Declarative YAML rules: aligned four-part name matching (partial match for a missing grandfather name; transliteration-only matches always go to a human), business names, expiry | `rules/kyc.yaml`, `crossdoc.py`, `arabic.py` |
| Decide | `auto_pass` only if every required field is ok, all rules pass, nothing expired, and P(session correct) ≥ τ_doc. Otherwise `human_review` with reason codes | `orchestrator.py` |
| Brief | Ultra writes 3–6 bullets using locked placeholders (`{{field:slot.field}}`); any bullet with a raw digit or Arabic text is dropped | `orchestrator.py` |

### The "never invent a field" guarantee (enforced in code, tested)

1. The structurer can only emit text that exists verbatim in reader output. Anything else → `null`, `unreadable`.
2. A field containing `?` → `null` + `unreadable`; the partial reading is kept only in `raw_partial` for reviewers.
3. Readers disagree and no reading passes validation → `null` + `low_confidence`, both candidates shown to the reviewer.
4. The Ultra summary cannot introduce values (placeholder substitution).

Field status: `ok | low_confidence | unreadable | mismatch | invalid_format | expired | not_present`.

## API (plan K) — OpenAPI in [`openapi/khatti.v1.yaml`](openapi/khatti.v1.yaml)

Tenants use `Authorization: Bearer <api_key>`. Reviewers and the mobile app use
short-lived JWTs from `POST /v1/auth/token`. Every POST needs an `Idempotency-Key`
(kept for 24h). Rate limits answer `429` with `Retry-After`.

| Method & path | Purpose |
|---|---|
| `POST /v1/kyc/sessions` | Create a session with slots → presigned upload URLs |
| `PUT <upload_url>` | Upload each photo directly (S3 presigned PUT; `/v1/uploads/{token}` in local dev) |
| `POST /v1/kyc/sessions/{id}/submit` | Queue the pipeline |
| `GET /v1/kyc/sessions/{id}` | Status, decision, per-field value/confidence/status, cross-checks, review summary, retake requests |
| `POST /v1/kyc/sessions/{id}/uploads` | New upload URLs for retakes |
| `POST /v1/documents` · `POST /v1/documents/{id}/submit` · `GET /v1/documents/{id}` | Single-document extraction |
| `GET /v1/review/queue` · `GET /v1/review/items/{id}` · `GET /v1/review/documents/{id}/image` | Reviewer queue, candidates, decrypted image |
| `POST /v1/review/items/{id}/decision` | Approve / reject / request retake, with per-field corrections (kept as labelled data) |
| `POST /v1/webhooks` | Events `session.completed`, `session.needs_review`, `session.retake_requested`, `reminder.due`; signed `Khatti-Signature: t=…,v1=…`, retried with backoff for 24h |
| `POST /v1/reminders` · `GET /v1/reminders` · `DELETE /v1/reminders/{id}` | Reminders: every extracted expiry date schedules one automatically (30 days ahead, `KHATTI_REMINDER_LEAD_DAYS`); partners can add their own. Due reminders fire the `reminder.due` webhook |
| `POST /v1/kyc/analyze` | Synchronous demo/eval run; stores nothing |

## Data layer (plan L)

- **Postgres** with Row-Level Security on every tenant table (the app role is not a superuser).
- **PII**: field values and results are envelope-encrypted (AES-GCM, per-record data keys wrapped by `KHATTI_DATA_KEY`). ID-like fields also store a keyed HMAC for lookup and dedupe.
- **Images**: uploaded with presigned PUTs, then moved into envelope-encrypted originals. Retention deletes KYC originals N days (default 30) after the final decision.
- **Audit**: an append-only, hash-chained `audit_log`, plus every model call (model id, prompt hash, image hashes, latency, tokens) in `model_calls`.
- **Queue**: Postgres `SKIP LOCKED` jobs and webhook deliveries.

## Evaluation and data (plan H/I) — [`jobs/`](jobs/README.md)

Synthetic fictional documents (rendered with real Arabic shaping, SPECIMEN-watermarked), phone-photo
augmentation with quality buckets, the week-1 reader bake-off, calibration fitting, and the eval
harness (hallucination rate, calibration, routing, selective risk). Each runs as a Nebius Serverless
Job from one container and logs to Managed MLflow.

## Infrastructure — [`infra/`](infra/README.md)

Terraform for Nebius (Object Storage, Managed PostgreSQL, Container Registry, Managed MLflow, IAM),
the vLLM image for the NVIDIA reader (`services/reader-omni`), a CLI deploy script for Serverless
Endpoints/Jobs, and a GCP portability module.

## Run locally

```bash
cp .env.example .env
python -m khatti.admin gen-data-key   # -> KHATTI_DATA_KEY; also set KHATTI_JWT_SECRET
docker compose up --build             # Postgres + API (:8000) + worker
docker compose exec api python -m khatti.admin create-tenant "Demo Bank"   # prints an API key
```

## Test

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
# API + data-layer tests need Postgres (a superuser URL; tests create a non-superuser app role):
KHATTI_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/khatti_test .venv/bin/python -m pytest
```

## What is verified vs. assumed

- The Iraqi National Card number format (12 digits) is from secondary sources and is a
  template parameter; **no checksum is applied because we found no public algorithm**.
- Commercial registration and tax card formats are our own **"Iraqi-style (fictional)"**
  synthetic specs (`khatti/rules/kyc.yaml`).
- Model IDs come from the plan's research. Confirm them with `GET /v1/models?verbose=true`.
- The Nebius Terraform and the Serverless CLI flags have not been validated against the real
  provider/CLI yet (see `infra/README.md`).
- Until `jobs/calibrate` fits a calibrator on the calibration split, confidences come
  from hand-set weights and are **not calibrated probabilities**.
