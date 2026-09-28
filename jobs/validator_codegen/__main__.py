""""Add a document type" workflow (plan E): Nemotron Super writes a field validator and its
tests; every variant runs isolated against SYNTHETIC fixtures; the best variant is proposed
for human review. Generated code is never registered automatically.

    python -m jobs.validator_codegen --doc tax_card --field tax_number --variants 4

Execution backends:
- local (default): a separate `python -I` process in a temp dir with CPU/memory limits and an
  empty environment. Good enough for synthetic fixtures on a dev machine.
- Token Factory Sandboxes (VM isolation, branching) is the intended backend. Its SDK was not
  reachable from the environment this was written in; implement `Executor.run` with it.
  Beta terms: synthetic data only, never customer data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from khatti.config import load_settings
from khatti.llm import ChatClient
from khatti.registry import default_registry

CODEGEN_PROMPT = """Write a Python validator for one field of an Iraqi-style (fictional) document.

Return JSON only: {"code": "<python module>", "tests": "<python test functions>"}

The module must define:
    def normalize(value: str) -> str      # canonical form: Arabic-Indic digits -> 0-9, trim, collapse spaces
    def validate(value: str) -> bool      # True only for well-formed values of this field
Rules: standard library only; no I/O, no network, no imports other than re/unicodedata/datetime;
never invent checksums: validate length/charset/shape only unless the spec gives an algorithm.
The tests are plain `def test_*():` functions using assert, calling normalize/validate directly."""

HARNESS = textwrap.dedent('''
    import json, os, sys
    sys.path.insert(0, os.getcwd())  # -I drops the script dir from sys.path
    import candidate
    fixtures = json.load(open("fixtures.json", encoding="utf-8"))
    res = {"valid_ok": 0, "valid_n": 0, "invalid_ok": 0, "invalid_n": 0, "own_tests": 0, "own_failed": 0, "errors": []}
    for v in fixtures["valid"]:
        res["valid_n"] += 1
        try:
            res["valid_ok"] += bool(candidate.validate(v))
        except Exception as e:
            res["errors"].append(repr(e)[:200])
    for v in fixtures["invalid"]:
        res["invalid_n"] += 1
        try:
            res["invalid_ok"] += not candidate.validate(v)
        except Exception as e:
            res["errors"].append(repr(e)[:200])
    try:
        import candidate_tests
        for name in dir(candidate_tests):
            if name.startswith("test_"):
                res["own_tests"] += 1
                try:
                    getattr(candidate_tests, name)()
                except Exception:
                    res["own_failed"] += 1
    except Exception as e:
        res["errors"].append("tests: " + repr(e)[:200])
    print(json.dumps(res))
''')


@dataclass
class RunResult:
    ok: bool
    report: dict
    stderr: str = ""


class Executor(Protocol):
    async def run(self, code: str, tests: str, fixtures: dict) -> RunResult: ...


class LocalExecutor:
    """Separate interpreter, isolated mode, empty env, CPU/memory limits, timeout."""

    def __init__(self, timeout_s: int = 10, memory_mb: int = 512):
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb

    def _limits(self):  # runs in the child before exec
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (self.timeout_s, self.timeout_s))
        mem = self.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))

    def _run_sync(self, code: str, tests: str, fixtures: dict) -> RunResult:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "candidate.py").write_text(code, encoding="utf-8")
            (p / "candidate_tests.py").write_text("from candidate import *\n" + tests, encoding="utf-8")
            (p / "fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
            (p / "harness.py").write_text(HARNESS, encoding="utf-8")
            try:
                proc = subprocess.run([sys.executable, "-I", "harness.py"], cwd=d, capture_output=True, text=True,
                                      timeout=self.timeout_s + 5, env={}, preexec_fn=self._limits)
            except subprocess.TimeoutExpired:
                return RunResult(False, {}, "timeout")
            try:
                report = json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                return RunResult(False, {}, proc.stderr[-2000:])
            return RunResult(proc.returncode == 0, report, proc.stderr[-2000:])

    async def run(self, code: str, tests: str, fixtures: dict) -> RunResult:
        return await asyncio.to_thread(self._run_sync, code, tests, fixtures)


def score(r: RunResult) -> float:
    rep = r.report
    if not r.ok or not rep.get("valid_n"):
        return 0.0
    acc = (rep["valid_ok"] + rep["invalid_ok"]) / (rep["valid_n"] + rep["invalid_n"])
    own = 1.0 if rep["own_tests"] and not rep["own_failed"] else 0.5
    return round(acc * (0.9 + 0.1 * own) - 0.01 * len(rep["errors"]), 4)


# ---------------------------------------------------------------- fixtures (synthetic only)

_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def fixtures_for(doc: str, field: str, n: int = 40, seed: int = 0) -> dict:
    from datetime import date

    from jobs.synth.identities import make_session, plan_variants

    rng = random.Random(seed)
    valid = []
    for i, variants in enumerate(plan_variants(rng, n)):
        v = make_session(rng, date(2026, 9, 28), i, variants).documents[doc].get(field)
        if v:
            valid.append(v)
    valid += [v.translate(_INDIC) for v in valid[:5]]
    invalid = set()
    for v in valid:
        invalid.update({v[:-1], v + "7", v.replace(v[0], "X", 1), "", " ", v[: len(v) // 2]})
    invalid -= set(valid)
    return {"valid": sorted(set(valid)), "invalid": sorted(invalid)}


async def generate(client: ChatClient, doc: str, field: str, spec_note: str, variants: int) -> list[dict]:
    async def one(i: int) -> dict | None:
        reply = await client.complete(
            [
                {"role": "system", "content": CODEGEN_PROMPT},
                {"role": "user", "content": json.dumps({"document": doc, "field": field, "spec": spec_note,
                                                        "examples_of_valid_values": fixtures_for(doc, field, 6)["valid"][:6]},
                                                       ensure_ascii=False)},
            ],
            temperature=0.2 if i == 0 else 0.7,  # variant 0 conservative, the rest explore
            max_tokens=2500,
            purpose=f"codegen:{doc}.{field}#{i}",
        )
        try:
            from khatti.llm import parse_json_object

            obj = parse_json_object(reply)
            return {"code": str(obj["code"]), "tests": str(obj.get("tests", ""))}
        except (ValueError, KeyError):
            return None

    return [v for v in await asyncio.gather(*(one(i) for i in range(variants))) if v]


_FORBIDDEN = re.compile(r"^\s*(import|from)\s+(?!re\b|unicodedata\b|datetime\b)", re.M)


async def run(args, client: ChatClient | None = None, executor: Executor | None = None) -> Path:
    registry = default_registry()
    spec = registry.get(args.doc)
    spec.field(args.field)  # must exist
    ruleset = registry.rulesets.get(spec.domain)
    fmt = ruleset.format_for(args.doc, args.field) if ruleset else None
    note = f"pattern {fmt.pattern.pattern} ({fmt.note})" if fmt else "no format known; infer the shape from the examples"
    if client is None:
        s = load_settings()
        if s.structurer is None:
            raise SystemExit("KHATTI_TOKEN_FACTORY_KEY is required (Nemotron Super writes the code)")
        client = ChatClient(s.structurer, s.timeout_s)
    executor = executor or LocalExecutor()

    fixtures = fixtures_for(args.doc, args.field, seed=args.seed)
    candidates = await generate(client, args.doc, args.field, note, args.variants)
    results = []
    for i, c in enumerate(candidates):
        if _FORBIDDEN.search(c["code"]) or _FORBIDDEN.search(c["tests"]):
            results.append({"variant": i, "score": 0.0, "rejected": "imports outside re/unicodedata/datetime"})
            continue
        r = await executor.run(c["code"], c["tests"], fixtures)
        results.append({"variant": i, "score": score(r), "report": r.report, "stderr": r.stderr[-500:]})
    best = max(results, key=lambda x: x["score"], default=None)

    out = args.out / f"{args.doc}.{args.field}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2))
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    if best and best["score"] > 0:
        c = candidates[best["variant"]]
        (out / "proposed_validator.py").write_text(
            f"# PROPOSED by Nemotron Super (variant {best['variant']}, score {best['score']}). Review before use.\n" + c["code"])
        (out / "proposed_tests.py").write_text(c["tests"])
    print(json.dumps({"doc": args.doc, "field": args.field, "variants": len(candidates),
                      "best": best and {k: best[k] for k in ("variant", "score")}}, ensure_ascii=False))
    return out


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--field", required=True)
    ap.add_argument("--variants", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("eval/codegen"))
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
