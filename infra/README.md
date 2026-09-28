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

> **Not yet validated.** The provider registry was unreachable from the environment where this
> was written, so resource schemas follow Nebius docs and examples. `terraform validate` against
> the real provider is the first step; fix any attribute names it reports.

Serverless Endpoints (API, worker, GPU reader) and Serverless Jobs are created with the `nebius`
CLI through `deploy/nebius-serverless.sh`, because provider coverage for them is unverified. Check
its flags against `nebius ai endpoint create --help` before the first run.

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
