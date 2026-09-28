#!/usr/bin/env bash
# Build, push and (re)deploy Khatti containers to Nebius Serverless.
#
# Provider coverage for Serverless Endpoints/Jobs is unverified, so this uses the `nebius`
# CLI. CHECK THE FLAGS against `nebius ai endpoint create --help` and
# `nebius ai job create --help` before the first run; they are written from docs, not tested.
set -euo pipefail

: "${REGISTRY:?cr.eu-north1.nebius.cloud/<registry-id>}"
: "${PROJECT_ID:?Nebius project id}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"

build() {  # name dockerfile context
  docker build -t "$REGISTRY/$1:$TAG" -f "$2" "$3"
  docker push "$REGISTRY/$1:$TAG"
}

case "${1:-}" in
  images)
    build khatti-api Dockerfile .
    build khatti-jobs jobs/Dockerfile .
    build khatti-reader-omni services/reader-omni/Dockerfile services/reader-omni
    ;;
  api)  # CPU endpoint: the API. Run the worker the same way with `python -m khatti.worker`.
    nebius ai endpoint create --parent-id "$PROJECT_ID" --name khatti-api \
      --image "$REGISTRY/khatti-api:$TAG" --container-port 8000 \
      --platform cpu-e2 --preset 2vcpu-8gb --env-file .env.nebius
    ;;
  reader-omni)  # GPU endpoint: stop it when idle (no charge while stopped)
    nebius ai endpoint create --parent-id "$PROJECT_ID" --name khatti-reader-omni \
      --image "$REGISTRY/khatti-reader-omni:$TAG" --container-port 8000 \
      --platform gpu-l40s-a --preset 1gpu-8vcpu-48gb \
      --env "VLLM_API_KEY=${VLLM_API_KEY:?}" --env "HF_TOKEN=${HF_TOKEN:?}"
    ;;
  job)  # e.g. ./deploy/nebius-serverless.sh job jobs.eval --split test --run-name v1
    shift
    nebius ai job create --parent-id "$PROJECT_ID" --name "khatti-$(echo "$1" | tr . -)-$(date +%s)" \
      --image "$REGISTRY/khatti-jobs:$TAG" --platform cpu-e2 --preset 8vcpu-32gb \
      --env-file .env.nebius -- "$@"
    ;;
  *)
    echo "usage: $0 images | api | reader-omni | job <module> [args]"; exit 2 ;;
esac
