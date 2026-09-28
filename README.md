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

## Model bridge

One gateway to **Nebius Token Factory**, **Hugging Face** and **any self-hosted model**.
Address a model as `<provider>/<model id>`; an id without a known provider prefix goes
to the default provider (`KHATTI_BRIDGE_DEFAULT`, else the first configured).

| Provider | Model ids | Enabled by |
|---|---|---|
| `nebius` | `nebius/meta-llama/Llama-3.3-70B-Instruct` | `KHATTI_TOKEN_FACTORY_KEY` |
| `hf` | `hf/Qwen/Qwen2.5-VL-7B-Instruct` (Inference Providers router) | `HF_TOKEN` |
| `hfe` | `hfe/<endpoint name>` (your dedicated Inference Endpoint) | `HF_TOKEN` |
| custom | `<name>/<model>`, e.g. vLLM serving a Hub model on a Nebius GPU | `KHATTI_PROVIDERS` |

```bash
# OpenAI-compatible: point any OpenAI SDK at <khatti>/v1/bridge/openai
curl -H "Authorization: Bearer $KHATTI_API_KEY" -H 'Content-Type: application/json' \
     localhost:8000/v1/bridge/openai/chat/completions \
     -d '{"model":"nebius/meta-llama/Llama-3.3-70B-Instruct","messages":[{"role":"user","content":"مرحبا"}],"stream":true}'
```

| Route | What it does |
|---|---|
| `GET /v1/bridge/providers` | Configured providers and their features |
| `GET /v1/bridge/models` | Every provider's models in one list (per-provider errors reported, not fatal) |
| `POST /v1/bridge/openai/{chat/completions, completions, embeddings, images/generations}` | Routed by `model`, streaming relayed |
| `GET/POST/DELETE /v1/bridge/{provider}/{path}` | Provider-native features with the provider's own model ids: `files`, `batches`, `fine_tuning/jobs`, `models`... |
| `GET/POST /v1/bridge/hosting/endpoints`, `…/{name}/{pause,resume,scale-to-zero}`, `DELETE …/{name}` | Host any Hugging Face model on a dedicated Inference Endpoint and manage it |
| `GET /v1/bridge/hosting/catalog`, `POST …/catalog/deploy`, `GET …/hardware` | HF one-click catalog and available hardware |
| `GET /v1/bridge/hosting/recipe?repo=<hub id>` | vLLM command plus `KHATTI_PROVIDERS` entry for serving a Hub model on your own GPU |

Only the listed OpenAI paths are forwarded, so the bridge is never an open proxy. Creating
or resuming endpoints bills your Hugging Face account, so it needs `KHATTI_ALLOW_HOSTING=1`;
pausing and scaling to zero are always allowed. New endpoints scale to zero after 15 idle
minutes by default. `/v1/extract` accepts an optional `model` to read photos with any
bridged vision model.

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
| `HF_TOKEN` | Enables the `hf` (Inference Providers) and `hfe` (Inference Endpoints) bridge providers |
| `KHATTI_HF_ROUTER_URL` | HF router base URL (default `https://router.huggingface.co/v1`) |
| `KHATTI_HF_NAMESPACE` | HF user or org that owns endpoints (default: the token's user) |
| `KHATTI_PROVIDERS` | JSON list of extra OpenAI-compatible providers: `name`, `base_url`, optional `api_key`, `label`, `features` |
| `KHATTI_BRIDGE_DEFAULT` | Provider for model ids without a prefix |
| `KHATTI_ALLOW_HOSTING` | `1` to allow creating and resuming paid HF endpoints |
| `KHATTI_CORS_ORIGINS` | Comma-separated origins for browser clients (Expo web) |

## Test

```bash
.venv/bin/python -m pytest
```
