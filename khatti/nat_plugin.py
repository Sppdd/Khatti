"""NVIDIA NeMo Agent Toolkit (NAT) integration: profiling and evaluation without making the
core depend on it (plan E, "Orchestration").

Registers, via the `nat.components` entry point:
- workflow `khatti_kyc_session`: runs the Khatti pipeline on one onboarding session
  (input: JSON with capture paths; output: JSON with the decision and per-field results);
- evaluators `khatti_fields`, `khatti_routing`, `khatti_hallucination`.

    python -m jobs.nat_dataset --data data/synth --split test      # -> eval/nat/test.jsonl
    nat eval --config_file nat/khatti_eval.yml                      # accuracy + NAT profiler
    nat serve --config_file nat/khatti_eval.yml                     # the pipeline behind NAT's FastAPI front end
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from nat.builder.builder import Builder, EvalBuilder
from nat.builder.evaluator import EvaluatorInfo
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_evaluator, register_function
from nat.data_models.evaluator import EvalInput, EvalInputItem, EvaluatorBaseConfig
from nat.data_models.function import FunctionBaseConfig
from nat.plugins.eval.data_models.evaluator_io import EvalOutput, EvalOutputItem

from .arabic import normalize
from .models import DocumentInput, Outcome


class KycSessionConfig(FunctionBaseConfig, name="khatti_kyc_session"):
    """The Khatti KYC pipeline on one session of document photos."""

    data_dir: str = "data/synth"
    readers: str = "env"  # env: KHATTI_READERS endpoints; simulated: harness testing only
    manifest: str = "captures.jsonl"  # used to index simulated readers
    structurer: str = "env"  # env: Nemotron Super; label: deterministic
    calibrator_path: str | None = None


def build_pipeline_for(config: KycSessionConfig):
    import argparse

    from jobs.common import read_jsonl
    from jobs.runner import build

    args = argparse.Namespace(data=Path(config.data_dir), manifest=config.manifest, readers=config.readers,
                              structurer=config.structurer, calibrator=Path(config.calibrator_path) if config.calibrator_path else None,
                              cache=Path("eval/cache"))
    rows = list(read_jsonl(args.data / args.manifest)) if config.readers == "simulated" else []
    return build(args, rows)[0]


def summarize(result) -> dict:
    return {
        "outcome": result.decision.outcome.value,
        "reasons": result.decision.reasons,
        "session_confidence": result.decision.session_confidence,
        "fields": {f"{d.slot}.{n}": {"value": f.value, "status": f.status.value, "confidence": f.confidence}
                   for d in result.documents for n, f in d.fields.items()},
        "retake_requests": [r.message_en for r in result.retake_requests],
    }


@register_function(config_type=KycSessionConfig)
async def khatti_kyc_session(config: KycSessionConfig, builder: Builder):
    pipeline = build_pipeline_for(config)
    root = Path(config.data_dir)

    async def run_session(session_json: str) -> str:
        """Run Khatti on a session: {"as_of": "YYYY-MM-DD", "documents": [{"slot": ..., "capture": <path>}]}"""
        spec = json.loads(session_json)
        docs = [DocumentInput(slot=d["slot"], image=(root / d["capture"]).read_bytes(),
                              mime_type="image/png" if d["capture"].endswith(".png") else "image/jpeg")
                for d in spec["documents"]]
        as_of = date.fromisoformat(spec["as_of"]) if spec.get("as_of") else None
        return json.dumps(summarize(await pipeline.run(docs, today=as_of)), ensure_ascii=False)

    yield FunctionInfo.from_fn(run_session, description="Read, cross-check and route one Iraqi KYC onboarding session.")


# ---------------------------------------------------------------- evaluators

def _pair(item: EvalInputItem) -> tuple[dict, dict]:
    out = json.loads(item.output_obj) if isinstance(item.output_obj, str) else (item.output_obj or {})
    exp = json.loads(item.expected_output_obj) if isinstance(item.expected_output_obj, str) else item.expected_output_obj
    return out, exp


def _field_ok(pred: str | None, gt: str | None) -> bool:
    return pred is None if gt is None else pred is not None and normalize(pred) == normalize(gt)


class _Evaluator:
    def __init__(self, score_fn, concurrency: int = 8):
        self.score_fn = score_fn
        self.concurrency = concurrency

    async def evaluate(self, eval_input: EvalInput) -> EvalOutput:
        items = []
        for item in eval_input.eval_input_items:
            try:
                score, why = self.score_fn(*_pair(item))
                items.append(EvalOutputItem(id=item.id, score=score, reasoning=why))
            except Exception as exc:  # a failed workflow run scores 0
                items.append(EvalOutputItem(id=item.id, score=0.0, reasoning={}, error=str(exc)))
        scores = [i.score for i in items if isinstance(i.score, (int, float))]
        return EvalOutput(average_score=round(sum(scores) / len(scores), 4) if scores else None, eval_output_items=items)


def score_fields(out: dict, exp: dict) -> tuple[float, dict]:
    truth = exp["fields"]
    wrong = [k for k, gt in truth.items() if not _field_ok((out["fields"].get(k) or {}).get("value"), gt)]
    return 1 - len(wrong) / max(len(truth), 1), {"wrong_fields": wrong}


def score_routing(out: dict, exp: dict) -> tuple[float, dict]:
    required_wrong = [k for k in exp["required"] if not _field_ok((out["fields"].get(k) or {}).get("value"), exp["fields"].get(k))]
    needed = exp["human_expected"] or bool(required_wrong) or bool(exp["unreadable"])
    routed = out["outcome"] == Outcome.HUMAN_REVIEW.value
    return float(routed == needed), {"human_needed": needed, "routed": routed, "required_wrong": required_wrong}


def score_hallucination(out: dict, exp: dict) -> tuple[float, dict]:
    """1.0 = no value returned for any field that is unreadable in the photo (target)."""
    unreadable = exp["unreadable"]
    invented = [k for k in unreadable if (out["fields"].get(k) or {}).get("value") is not None]
    return (1 - len(invented) / len(unreadable) if unreadable else 1.0), {"invented": invented}


class FieldsEvalConfig(EvaluatorBaseConfig, name="khatti_fields"):
    """Share of fields matching ground truth (normalised; null expected where unreadable)."""


class RoutingEvalConfig(EvaluatorBaseConfig, name="khatti_routing"):
    """1 when the session was routed to a human exactly when one was needed."""


class HallucinationEvalConfig(EvaluatorBaseConfig, name="khatti_hallucination"):
    """1 - share of unreadable fields for which a value was returned."""


@register_evaluator(config_type=FieldsEvalConfig)
async def khatti_fields(config: FieldsEvalConfig, builder: EvalBuilder):
    yield EvaluatorInfo(config=config, evaluate_fn=_Evaluator(score_fields).evaluate, description=FieldsEvalConfig.__doc__)


@register_evaluator(config_type=RoutingEvalConfig)
async def khatti_routing(config: RoutingEvalConfig, builder: EvalBuilder):
    yield EvaluatorInfo(config=config, evaluate_fn=_Evaluator(score_routing).evaluate, description=RoutingEvalConfig.__doc__)


@register_evaluator(config_type=HallucinationEvalConfig)
async def khatti_hallucination(config: HallucinationEvalConfig, builder: EvalBuilder):
    yield EvaluatorInfo(config=config, evaluate_fn=_Evaluator(score_hallucination).evaluate, description=HallucinationEvalConfig.__doc__)
