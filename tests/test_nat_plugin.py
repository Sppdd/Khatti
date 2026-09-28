import json
import shutil
import subprocess
import sys

import pytest
from pathlib import Path

NAT = shutil.which("nat") or str(Path(sys.executable).parent / "nat")

pytest.importorskip("nat")

from khatti.nat_plugin import score_fields, score_hallucination, score_routing  # noqa: E402

EXP = {
    "fields": {"tax_card.tax_number": "123", "tax_card.holder_name_ar": "علي", "tax_card.expiry_date": None},
    "required": ["tax_card.tax_number", "tax_card.holder_name_ar", "tax_card.expiry_date"],
    "unreadable": ["tax_card.expiry_date"],
    "human_expected": False,
}


def out(values: dict, outcome: str) -> dict:
    return {"outcome": outcome, "fields": {k: {"value": v} for k, v in values.items()}}


def test_scorers():
    good = out({"tax_card.tax_number": "١٢٣", "tax_card.holder_name_ar": "على", "tax_card.expiry_date": None}, "human_review")
    assert score_fields(good, EXP)[0] == 1.0
    assert score_hallucination(good, EXP)[0] == 1.0
    # an unreadable field exists, so a human is needed: routing to review is correct
    assert score_routing(good, EXP)[0] == 1.0
    invented = out({"tax_card.tax_number": "123", "tax_card.holder_name_ar": "علي", "tax_card.expiry_date": "2027/01/01"}, "auto_pass")
    assert score_hallucination(invented, EXP) == (0.0, {"invented": ["tax_card.expiry_date"]})
    assert score_routing(invented, EXP)[0] == 0.0


def _playwright_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not (_playwright_ok() and Path(NAT).exists()), reason="needs Playwright and the nat CLI")
def test_nat_eval_end_to_end(tmp_path):
    from jobs.augment.__main__ import main as augment
    from jobs.nat_dataset.__main__ import main as export
    from jobs.synth.__main__ import main as synth

    data = tmp_path / "synth"
    synth(["--sessions", "2", "--out", str(data), "--seed", "4"])
    augment(["--src", str(data), "--per-render", "1"])
    export(["--data", str(data), "--split", "all", "--out", str(tmp_path / "ds.jsonl")])
    cfg = tmp_path / "cfg.yml"
    cfg.write_text(f"""
workflow: {{_type: khatti_kyc_session, data_dir: "{data}", readers: simulated, structurer: label}}
eval:
  general:
    output_dir: "{tmp_path / 'out'}"
    dataset: {{_type: jsonl, file_path: "{tmp_path / 'ds.jsonl'}"}}
  evaluators:
    routing: {{_type: khatti_routing}}
    hallucination: {{_type: khatti_hallucination}}
""")
    r = subprocess.run([NAT, "eval", "--config_file", str(cfg)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    scores = json.loads((tmp_path / "out" / "hallucination_output.json").read_text())
    assert scores["average_score"] == 1.0
