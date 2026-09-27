# Khatti — KYC Document Agent

Arabic-first reader for Iraqi onboarding documents (national ID, passport, residence
card). It reads each document with an ensemble of vision models, cross-checks the
results, and hands the case to a human whenever it is unsure.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the plan and architecture.

The same API also powers the **Khatti mobile app** ([`mobile/`](mobile/README.md)): an
Expo app for iOS and Android that turns any photo into structured data through
`POST /v1/extract`, saves it to Supabase, and tests the KYC endpoint from a phone.

## Pipeline

1. **Read**: every image reader (e.g. an NVIDIA VLM on a Nebius Serverless Endpoint and
   an Arabic-capable VLM on Nebius Token Factory) reads every document in parallel.
2. **Merge**: after Arabic normalisation, readers vote per field. Agreement is
   the confidence signal; a reader that failed counts as a missing vote.
3. **Check**: deterministic rules: Iraqi 12-digit national ID, expiry, age ≥ 18,
   passport MRZ check digits (ICAO 9303), MRZ vs. visual zone, name/DOB match across
   documents.
4. **Route**: Nemotron (text-only, on Token Factory) reviews the evidence and writes
   the rationale. It can escalate a clean case to human review, but can never approve
   a case the rules escalated or reject on its own. If Nemotron fails, the case goes
   to a human.

Decisions: `approve`, `human_review`, `reject` (only on a hard failure all readers agree on).

## Run

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env   # fill in keys and endpoints, then export them
.venv/bin/uvicorn khatti.api:app --reload
```

```bash
curl -F files=@id.jpg -F doc_types=national_id \
     -F files=@passport.jpg -F doc_types=passport \
     http://localhost:8000/v1/kyc/cases
```

### Photo to data (mobile app)

```bash
curl -F file=@receipt.jpg -F mode=prices http://localhost:8000/v1/extract
```

`mode` is one of `auto`, `prices`, `mind`, `prompt`, `text`. The reply is one JSON
record: `kind`, `title`, `summary`, `text`, `tags`, `fields`, `items` (name, price,
currency, quantity, unit), `store`, and `prompt` (a ready-to-paste generative-media prompt).

## Configuration

| Variable | Purpose |
|---|---|
| `KHATTI_TOKEN_FACTORY_URL` | Token Factory OpenAI-compatible base URL |
| `KHATTI_TOKEN_FACTORY_KEY` | API key; enables the Nemotron router |
| `KHATTI_ROUTER_MODEL` | Nemotron model id |
| `KHATTI_READERS` | JSON list of vision readers: `name`, `base_url`, `model`, optional `api_key` |
| `KHATTI_MIN_CONFIDENCE` | Below this case confidence → human review (default 0.85) |
| `KHATTI_EXTRACTOR` | JSON `{name, base_url, model, api_key?}` vision model for `/v1/extract`; defaults to the first reader |
| `KHATTI_API_KEY` | If set, `/v1/*` requires `Authorization: Bearer <key>` |

## Test

```bash
.venv/bin/python -m pytest
```
