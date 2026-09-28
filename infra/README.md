# Infrastructure

## Nebius (eu-north1) — `infra/nebius`

Object Storage buckets (images with an `uploads/` expiry rule; artifacts), Managed PostgreSQL 16
(app user is not a superuser, so Row-Level Security applies), Container Registry, Managed MLflow,
and service accounts with S3 access keys. State lives in an Object Storage bucket.

```bash
cd infra/nebius
cp backend.hcl.example backend.hcl        # state bucket credentials
terraform init -backend-config=backend.hcl
terraform validate                        # run first: see the note below
terraform apply -var project_id=... -var subnet_id=... -var editors_group_id=...
```

> Attribute names and types were checked against the API schemas shipped in the official `nebius`
> Python SDK (0.6.14), which the provider is generated from. The provider registry itself was
> unreachable from the build environment, so run `terraform validate` before the first apply.

## Serverless Endpoints and Jobs — `deploy/nebius_deploy.py`

Typed deploys through the official SDK (`pip install -e '.[deploy]'`; auth from the Nebius CLI
profile or `NEBIUS_IAM_TOKEN`). Settings come from `.env.nebius` plus the environment.

```bash
python -m deploy.nebius_deploy endpoint reader-omni --project $P --image $REG/khatti-reader-omni:$TAG --dry-run
python -m deploy.nebius_deploy endpoint api    --project $P --image $REG/khatti-api:$TAG
python -m deploy.nebius_deploy endpoint worker --project $P --image $REG/khatti-api:$TAG
python -m deploy.nebius_deploy stop reader-omni --project $P      # no GPU charge while stopped
python -m deploy.nebius_deploy start reader-omni --project $P     # "wake NVIDIA reader"
python -m deploy.nebius_deploy job jobs.eval --project $P --image $REG/khatti-jobs:$TAG -- --split test --run-name v1
python -m deploy.nebius_deploy status --project $P
```

`--dry-run` asks the API to validate a create without doing it. Platform and preset names
(`gpu-l40s-a`, `1gpu-8vcpu-32gb`, `cpu-e2`, ...) vary by region: check `nebius compute platform list`
and pass `--platform/--preset` if they differ. The reader endpoint is protected by its auth token
(`READER_OMNI_TOKEN`); use the same value as that reader's `api_key` in `KHATTI_READERS`.

Region: eu-north1 has every service Khatti needs, including Managed MLflow. The plan advises against
me-west1 for Iraqi KYC data (Iraq's 2022 anti-normalisation law; confirm with Iraqi counsel).
Measure round-trip time from Mosul to eu-north1 and eu-west1 in week 1.

## GPU reader — `services/reader-omni`

Nemotron 3 Nano Omni FP8 on vLLM 0.20.0 (per the model card), one L40S 48GB. Stop the endpoint when
idle; run it for batch reads and judging windows (the plan budgets 40–60 GPU-hours, not 24/7).
The pipeline degrades to the Token Factory reader when it is down and records
`nvidia-omni: unavailable` in the audit trail.

```bash
python services/reader-omni/smoke.py https://<endpoint>/v1 <model-id> <VLLM_API_KEY> sample.jpg
```

## Google Cloud — `infra/gcp` (portability proof, not deployed)

Cloud Run (API + worker), Cloud SQL Postgres 16 and a GCS bucket (S3-interop HMAC keys). The app
talks S3 and Postgres only, so the same images run unchanged.
