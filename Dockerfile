# One image for the API and the worker (Nebius Container Registry -> Serverless Endpoints).
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY khatti ./khatti
RUN pip install --no-cache-dir . && useradd --system --uid 10001 khatti
USER khatti
EXPOSE 8000
# Worker: override the command with `python -m khatti.worker`.
CMD ["uvicorn", "khatti.api:app", "--host", "0.0.0.0", "--port", "8000"]
