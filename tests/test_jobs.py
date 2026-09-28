import json
import random
from collections import Counter
from datetime import date

import cv2
import numpy as np
import pytest

from jobs.augment.__main__ import augment_one, bucket
from jobs.common import assign_splits, read_jsonl
from jobs.synth.identities import BLOCKLIST, VARIANTS, make_session, plan_variants
from khatti.arabic import match_names

from .conftest import card_image

AS_OF = date(2026, 9, 28)


def session(variants, seed=1):
    return make_session(random.Random(seed), AS_OF, 0, variants)


def test_variants_do_what_they_say():
    clean = session(["clean"])
    assert not clean.human_expected
    front, lic, tax = (clean.documents[k] for k in ("national_id_front", "commercial_registration", "tax_card"))
    assert match_names(front["full_name_ar"], lic["owner_name_ar"]).status == "match"

    s = session(["license_missing_grandfather"])
    assert s.human_expected
    assert match_names(s.documents["national_id_front"]["full_name_ar"], s.documents["commercial_registration"]["owner_name_ar"]).status == "partial_match"

    s = session(["different_person"], seed=3)
    assert match_names(s.documents["national_id_front"]["full_name_ar"], s.documents["tax_card"]["holder_name_ar"]).status == "mismatch"

    s = session(["arabic_indic_digits"])
    assert all(ch in "٠١٢٣٤٥٦٧٨٩" for ch in s.documents["national_id_front"]["id_number"])
    assert not s.human_expected

    s = session(["hijri_dates"])
    assert s.documents["commercial_registration"]["expiry_date"].endswith("هـ")


def test_variant_plan_covers_everything():
    plans = plan_variants(random.Random(0), 400)
    seen = Counter(v for p in plans for v in p)
    assert set(seen) == set(VARIANTS)
    assert 0.3 < seen["clean"] / len(plans) < 0.5


def test_no_blocklisted_names():
    rng = random.Random(0)
    for i in range(300):
        s = make_session(rng, AS_OF, i, ["clean"])
        given, father = s.documents["national_id_front"]["full_name_ar"].split(" ")[:2]
        assert (given, father) not in BLOCKLIST


def test_splits_are_by_identity():
    ids = [f"idn{i:03d}" for i in range(100)]
    s = assign_splits(ids + ids, seed=1)  # duplicates: same identity, same split
    counts = Counter(s.values())
    assert counts == {"tune": 60, "calibration": 20, "test": 20}


def test_augment_smoke_and_buckets():
    doc = cv2.imdecode(np.frombuffer(card_image(1, margin=0), np.uint8), cv2.IMREAD_COLOR)
    boxes = {"id_number": [0.3, 0.7, 0.9, 0.78]}
    data, cond, b, unreadable = augment_one(doc, boxes, np.random.default_rng(0), random.Random(0))
    assert cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is not None
    assert b in {"good", "medium", "worst"} and set(unreadable) <= {"id_number"}
    assert bucket({"glare": True}) == "worst"
    assert bucket({"light": "dim"}) == "medium"
    assert bucket({"angle": 10, "jpeg_quality": 90}) == "good"


def _playwright_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _playwright_ok(), reason="Playwright Chromium not available")
def test_harness_end_to_end_with_simulated_readers(tmp_path):
    from jobs.augment.__main__ import main as augment
    from jobs.bakeoff.__main__ import main as bakeoff
    from jobs.calibrate.__main__ import main as calibrate
    from jobs.eval.__main__ import main as evaluate
    from jobs.synth.__main__ import main as synth

    data = tmp_path / "synth"
    synth(["--sessions", "5", "--out", str(data), "--seed", "3"])
    rows = list(read_jsonl(data / "renders.jsonl"))
    assert len(rows) == 20 and all((data / r["image"]).exists() for r in rows)
    assert all(r["field_boxes"] for r in rows)
    augment(["--src", str(data), "--per-render", "1", "--include-clean"])

    out = evaluate(["--data", str(data), "--split", "all", "--readers", "simulated", "--out", str(tmp_path / "rep"), "--run-name", "t"])
    report = json.loads((out / "report.json").read_text())
    assert report["meta"]["simulated"] is True
    assert report["routing"]["n"] == 10
    assert "SIMULATED" in (out / "report.md").read_text()
    assert (out / "reliability.svg").read_text().startswith("<svg")

    cal = calibrate(["--data", str(data), "--split", "all", "--readers", "simulated", "--out", str(tmp_path / "cal"), "--version", "t"])
    assert json.loads(cal.read_text())["version"] == "t-SIMULATED"
    with pytest.raises(SystemExit):
        calibrate(["--data", str(data), "--split", "test", "--readers", "simulated", "--out", str(tmp_path / "cal")])

    memo = bakeoff(["--data", str(data), "--readers", "simulated", "--per-type", "2", "--out", str(tmp_path / "bake")])
    assert "Decision" in memo.read_text()
