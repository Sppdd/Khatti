#!/usr/bin/env bash
# Weights are downloaded on first start into $HF_HOME (mount a volume there to avoid
# re-downloading on every cold start). The Serverless Endpoint's auth token protects it;
# VLLM_API_KEY (read by vLLM from the environment) adds a second check when set.
set -euo pipefail
exec vllm serve "$MODEL_ID" \
  --host 0.0.0.0 --port 8000 \
  --served-model-name "$MODEL_ID" \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --limit-mm-per-prompt '{"image": 2}' \
  --trust-remote-code
