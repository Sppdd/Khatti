# NVIDIA image reader: Nemotron 3 Nano Omni (FP8) served by vLLM with an OpenAI-compatible API.
# The model card requires vLLM 0.20.0. FP8 fits one L40S 48GB.
#
#   docker build -t <registry>/khatti-reader-omni services/reader-omni
#   Serverless Endpoint: GPU L40S x1, port 8000, env HF_TOKEN + VLLM_API_KEY, stopped when idle.
FROM vllm/vllm-openai:v0.20.0

# Verify the exact FP8 repository name on Hugging Face before the first deploy.
ENV MODEL_ID=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-FP8 \
    MAX_MODEL_LEN=32768 \
    GPU_MEMORY_UTILIZATION=0.92 \
    HF_HOME=/models

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
