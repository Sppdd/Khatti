"""calibrate (plan G/I): fit per-group logistic + isotonic calibrators on the calibration split.

    python -m jobs.calibrate --data data/synth --split calibration --version cal-2026-10-12

Writes eval/calibrators/<version>.json (load it with KHATTI_CALIBRATOR_PATH) and logs to
MLflow when configured. Thresholds are chosen so auto-accepted fields reach the target
precision (default 99%) on the calibration split; coverage is reported alongside.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np

from khatti.registry import FieldGroup, default_registry

from ..common import maybe_mlflow
from ..eval.metrics import Collector
from ..runner import add_args, build, load_rows, run_sessions


async def run(args) -> Path:
    if args.split == "test":
        raise SystemExit("never fit on the test split")
    rows = load_rows(args)
    args.calibrator = None  # features are calibrator-independent; fit from raw features
    pipeline, simulated = build(args, rows)
    col = Collector(default_registry())
    async for group, result in run_sessions(pipeline, args, rows):
        col.add(group, result)

    from khatti.calibration import fit_calibrator

    data = [(FieldGroup(o.group), o.features, int(o.correct)) for o in col.fields if o.pred is not None and o.features]
    version = args.version + ("-SIMULATED" if simulated else "")
    cal = fit_calibrator(data, version, target_precision=args.precision)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{version}.json"
    path.write_text(cal.to_json())

    summary = {}
    for g in FieldGroup:
        rows_g = [(f, y) for gg, f, y in data if gg is g]
        if not rows_g:
            continue
        p = np.array([cal.predict(f, g) for f, _ in rows_g])
        y = np.array([y for _, y in rows_g])
        tau = cal.threshold(g)
        acc = p >= tau
        summary[g.value] = {"n": len(rows_g), "tau": tau, "coverage": float(acc.mean()),
                            "precision_at_tau": float(y[acc].mean()) if acc.any() else None}
    print(json.dumps({"artifact": str(path), "simulated": simulated, "groups": summary}, indent=2))

    mlflow = maybe_mlflow()
    if mlflow:
        mlflow.set_experiment("khatti-calibration")
        with mlflow.start_run(run_name=version):
            mlflow.log_params({"split": args.split, "target_precision": args.precision, "simulated": simulated})
            for g, s in summary.items():
                mlflow.log_metrics({f"{g}.tau": s["tau"], f"{g}.coverage": s["coverage"]})
            mlflow.log_artifact(str(path))
    return path


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    add_args(ap)
    ap.set_defaults(split="calibration")
    ap.add_argument("--version", default="cal-dev")
    ap.add_argument("--precision", type=float, default=0.99)
    ap.add_argument("--out", type=Path, default=Path("eval/calibrators"))
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
