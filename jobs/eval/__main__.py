"""eval-run (plan I): run the pipeline over a split and report.

    python -m jobs.eval --data data/synth --split test --readers env --run-name v1-test

Writes eval/reports/<run>/report.json, report.md, reliability.svg, selective_risk.svg and
logs to Managed MLflow when MLFLOW_TRACKING_URI is set. Run the held-out test split once,
after the tuning set is frozen, and do not tune afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from khatti.registry import default_registry

from ..common import manifest_hash, maybe_mlflow
from ..runner import add_args, build, load_rows, run_sessions
from .charts import reliability_svg, selective_risk_svg
from .metrics import Collector


def _fmt(v) -> str:
    if isinstance(v, float):
        return "–" if math.isnan(v) else f"{v:.3f}"
    return str(v)


def markdown(report: dict, meta: dict) -> str:
    f, r, c = report["fields"], report["routing"], report["calibration"]
    lines = [f"# Khatti eval — {meta['run']}", ""]
    if meta["simulated"]:
        lines += ["> **SIMULATED READERS.** These numbers test the harness only and say nothing about real models.", ""]
    lines += [f"split `{meta['split']}` · manifest sha256 `{meta['manifest_sha256'][:16]}…` · pipeline `{meta['pipeline_version']}` · {meta['sessions']} sessions", "",
              "## Headline", "",
              "| metric | value |", "|---|---|",
              f"| hallucination rate (non-null on unreadable fields; target 0) | {_fmt(f['hallucination_rate'])} (n={f['n_unreadable']}) |",
              f"| auto-pass error rate | {_fmt(r['auto_pass_error_rate'])} |",
              f"| auto-pass rate | {_fmt(r['auto_pass_rate'])} |",
              f"| human routing precision / recall / F1 | {_fmt(r['precision'])} / {_fmt(r['recall'])} / {_fmt(r['f1'])} |",
              f"| field normalized match / exact match | {_fmt(f['normalized_match'])} / {_fmt(f['exact_match'])} |",
              f"| CER Arabic text / digits | {_fmt(f['cer_arabic_text'])} / {_fmt(f['cer_digits'])} |",
              f"| ECE / Brier | {_fmt(c.get('ece', float('nan')))} / {_fmt(c.get('brier', float('nan')))} |", "",
              "## By quality bucket", "", "| bucket | n | normalized match | hallucination | null recall | CER text |", "|---|---|---|---|---|---|"]
    for b, s in report["slices"]["bucket"].items():
        if s.get("n"):
            lines.append(f"| {b} | {s['n']} | {_fmt(s['normalized_match'])} | {_fmt(s['hallucination_rate'])} | {_fmt(s['null_recall'])} | {_fmt(s['cer_arabic_text'])} |")
    lines += ["", "## By field group", "", "| group | n | normalized match | CER text | CER digits | ECE |", "|---|---|---|---|---|---|"]
    for g, s in report["slices"]["group"].items():
        if s.get("n"):
            ece = report["calibration_by_group"][g].get("ece", float("nan"))
            lines.append(f"| {g} | {s['n']} | {_fmt(s['normalized_match'])} | {_fmt(s['cer_arabic_text'])} | {_fmt(s['cer_digits'])} | {_fmt(ece)} |")
    lines += ["", "## Handwritten vs printed; digit script", "", "| slice | n | normalized match | CER text |", "|---|---|---|---|"]
    for k in ("writing", "digits"):
        for name, s in report["slices"][k].items():
            if s.get("n"):
                lines.append(f"| {name} | {s['n']} | {_fmt(s['normalized_match'])} | {_fmt(s['cer_arabic_text'])} |")
    lines += ["", "## Routing recall by variant", "", "| variant | share routed to a human |", "|---|---|"]
    for v, x in r.get("variant_recall", {}).items():
        lines.append(f"| {v} | {_fmt(x)} |")
    lines += ["", "![reliability](reliability.svg) ![selective risk](selective_risk.svg)", ""]
    return "\n".join(lines)


async def run(args) -> Path:
    rows = load_rows(args)
    pipeline, simulated = build(args, rows)
    registry = default_registry()
    col = Collector(registry)
    n = 0
    async for group, result in run_sessions(pipeline, args, rows):
        col.add(group, result)
        n += 1
    report = col.report()
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    meta = {"run": run_name, "split": args.split, "simulated": simulated, "sessions": n,
            "manifest_sha256": manifest_hash(args.data / args.manifest), "pipeline_version": pipeline.version,
            "readers": [r.name for r in pipeline.readers], "structurer": type(pipeline.structurer).__name__}
    out = args.out / run_name
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"meta": meta, **report}, indent=2, default=str))
    (out / "report.md").write_text(markdown(report, meta))
    tag = " (SIMULATED)" if simulated else ""
    (out / "reliability.svg").write_text(reliability_svg(report["calibration"].get("reliability", []), f"Reliability{tag}"))
    (out / "selective_risk.svg").write_text(selective_risk_svg(report["calibration"].get("selective_risk", []), f"Selective risk{tag}"))

    mlflow = maybe_mlflow()
    if mlflow:
        mlflow.set_experiment("khatti-eval")
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params({k: str(v) for k, v in meta.items()})
            flat = {f"fields.{k}": v for k, v in report["fields"].items() if isinstance(v, (int, float))}
            flat |= {f"routing.{k}": v for k, v in report["routing"].items() if isinstance(v, (int, float))}
            flat |= {f"calibration.{k}": v for k, v in report["calibration"].items() if isinstance(v, (int, float))}
            mlflow.log_metrics({k: v for k, v in flat.items() if not (isinstance(v, float) and math.isnan(v))})
            mlflow.log_artifacts(str(out))
    print(f"wrote {out}/report.md")
    return out


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    add_args(ap)
    ap.add_argument("--out", type=Path, default=Path("eval/reports"))
    ap.add_argument("--run-name", default="")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
