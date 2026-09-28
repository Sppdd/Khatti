# Image for Nebius Serverless Jobs: synth, augment, eval, calibrate, bakeoff.
#   docker build -f jobs/Dockerfile -t <registry>/khatti-jobs .
#   python -m jobs.synth | jobs.augment | jobs.eval | jobs.calibrate | jobs.bakeoff
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY khatti ./khatti
COPY jobs ./jobs
RUN pip install --no-cache-dir ".[jobs]"
ENTRYPOINT ["python", "-m"]
CMD ["jobs.eval", "--help"]
