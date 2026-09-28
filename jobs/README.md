# Jobs (Nebius Serverless Jobs)

One container (`jobs/Dockerfile`) runs every job. Outputs go to Object Storage and Managed MLflow
(`MLFLOW_TRACKING_URI`) in production; locally they land under `data/` and `eval/`.

| Job | Command | What it does |
|---|---|---|
| inventory | `python -m jobs.inventory` | Week 0: lists the models your key sees (`/v1/models?verbose=true`) and live-probes text, image input, JSON mode, `json_schema` and logprobs. Settles which vision models are really served; suggests `KHATTI_READERS`. `deploy/rtt.sh` measures latency from Mosul to eu-north1 / eu-west1. |
| synth | `python -m jobs.synth --sessions 150 --out data/synth` | Fictional Iraqi-style sessions with cross-document variants, rendered with real Arabic shaping (SPECIMEN watermark, fictional authorities, no emblems). Split by identity 60/20/20. |
| augment | `python -m jobs.augment --src data/synth --per-render 5` | Phone-photo conditions (angle, dim/backlit, shadow, glare, blur, thumb, cut corner, crumple, JPEG) with quality buckets. Fields covered by glare/thumb or out of frame become unreadable in ground truth. |
| bakeoff | `python -m jobs.bakeoff --data data/synth` | Week-1 reader bake-off on tune-split captures: Arabic-name CER, digit exact match (Arabic-Indic separately), dates. Writes the decision memo and `KHATTI_PRIMARY_READERS`. |
| calibrate | `python -m jobs.calibrate --split calibration --version cal-YYYYMMDD` | Fits per-group logistic + isotonic calibrators and τ at 99% precision. Refuses the test split. |
| eval | `python -m jobs.eval --split test --run-name v1` | Field accuracy, CER, null-correctness, **hallucination rate**, ECE/Brier/reliability, selective risk, routing P/R/F1, auto-pass error, slices by bucket, handwriting and digit script. |

All model-backed jobs cache transcripts per (reader, image hash) in `eval/cache/`, so
structuring and calibration can be re-run without paying to re-read images.

`--readers simulated` swaps in noisy ground-truth readers to test the harness without
endpoints. Those reports are marked **SIMULATED** and are not results.

Real handwriting (volunteers in Mosul) and physical photo sessions add rows to the same
manifest format with `"condition": {"kind": "photo", ...}`.
