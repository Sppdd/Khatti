import asyncio
import json
from argparse import Namespace

import httpx

from jobs.validator_codegen.__main__ import LocalExecutor, fixtures_for, run
from khatti.config import EndpointConfig
from khatti.llm import ChatClient

GOOD = '''
import re
def normalize(value):
    return re.sub(r"\\s+", "", value.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
def validate(value):
    return bool(re.fullmatch(r"\\d{9}", normalize(value)))
'''
TESTS = '''
def test_ok():
    assert validate("123456789")
def test_bad():
    assert not validate("12345")
'''
LOOSE = "def normalize(v):\n    return v\ndef validate(v):\n    return True\n"
EVIL = "import os\ndef normalize(v):\n    return v\ndef validate(v):\n    os.system('echo hi')\n    return True\n"


def test_fixtures_are_synthetic_and_split():
    fx = fixtures_for("tax_card", "tax_number", n=20)
    assert fx["valid"] and fx["invalid"] and not set(fx["valid"]) & set(fx["invalid"])
    assert any(any(ch in "٠١٢٣٤٥٦٧٨٩" for ch in v) for v in fx["valid"])


def test_local_executor_scores_and_limits():
    fx = fixtures_for("tax_card", "tax_number", n=20)
    good = asyncio.run(LocalExecutor().run(GOOD, TESTS, fx))
    assert good.ok and good.report["valid_ok"] == good.report["valid_n"] and good.report["own_failed"] == 0
    hang = asyncio.run(LocalExecutor(timeout_s=2).run("def normalize(v):\n    return v\ndef validate(v):\n    while True: pass\n", "", fx))
    assert not hang.ok


def test_codegen_picks_best_variant_and_rejects_forbidden_imports(tmp_path):
    replies = iter([{"code": LOOSE, "tests": ""}, {"code": GOOD, "tests": TESTS}, {"code": EVIL, "tests": ""}])

    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(next(replies))}}]})

    client = ChatClient(EndpointConfig("super", "https://tf.test/v1", "super", "k"),
                        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # run variants sequentially so the fake replies map to variants in order
    out = asyncio.run(run(Namespace(doc="tax_card", field="tax_number", variants=3, seed=0, out=tmp_path), client=client))
    results = json.loads((out / "results.json").read_text())
    by = {r["variant"]: r for r in results}
    assert by[2]["rejected"]
    assert max(results, key=lambda r: r["score"])["variant"] in (0, 1)
    proposed = (out / "proposed_validator.py").read_text()
    assert "Review before use" in proposed
