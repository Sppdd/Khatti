"""Run a sample session through a running Khatti API (the flow the mobile app uses).

    python -m khatti.demo samples/glare_on_id --base-url http://localhost:8000 --api-key kh_...

create session -> upload each photo to its presigned URL -> submit -> poll -> print the result.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

SLOT_ORDER = ["national_id_front", "national_id_back", "commercial_registration", "tax_card"]


def run(folder: Path, base_url: str, api_key: str, timeout_s: int = 300, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(base_url=base_url, timeout=60)
    auth = {"Authorization": f"Bearer {api_key}"}
    slots = [s for s in SLOT_ORDER if (folder / f"{s}.jpg").exists()]
    if not slots:
        raise SystemExit(f"no <slot>.jpg photos in {folder}")

    r = http.post("/v1/kyc/sessions", json={"slots": slots, "external_ref": f"demo-{folder.name}"},
                  headers={**auth, "Idempotency-Key": str(uuid.uuid4())})
    r.raise_for_status()
    session = r.json()
    for up in session["uploads"]:
        put = http.request(up["method"], up["url"], content=(folder / f"{up['slot']}.jpg").read_bytes(),
                           headers={**up["headers"], "Content-Type": "image/jpeg"})
        put.raise_for_status()
    http.post(f"/v1/kyc/sessions/{session['session_id']}/submit",
              headers={**auth, "Idempotency-Key": str(uuid.uuid4())}).raise_for_status()

    deadline = time.monotonic() + timeout_s
    while True:
        view = http.get(f"/v1/kyc/sessions/{session['session_id']}", headers=auth).json()
        if view["status"] in ("completed", "needs_review", "failed") or time.monotonic() > deadline:
            return view
        time.sleep(2)


def print_summary(view: dict) -> None:
    d = view.get("decision") or {}
    print(f"session {view['session_id']}: {view['status']} -> {d.get('outcome')} "
          f"(confidence {d.get('session_confidence')}) reasons={d.get('reasons')}")
    for doc in view.get("documents", []):
        print(f"  {doc['slot']}  photo issues: {', '.join(doc['quality']['issues']) if doc.get('quality') else '-'}")
        for name, f in doc["fields"].items():
            shown = f["value"] if f["value"] is not None else "∅ (blank and flagged)"
            print(f"    {name:22} {f['status']:15} {f['confidence']:.2f}  {shown}")
    for c in view.get("cross_checks", []):
        print(f"  cross-check {c['rule']}: {c['status']} {c.get('detail') or ''}")
    for r in view.get("retake_requests", []):
        print(f"  retake: {r['message_ar']}  /  {r['message_en']}")
    for b in view.get("review_summary", []):
        print(f"  • {b}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--json", action="store_true", help="print the raw session JSON")
    args = ap.parse_args(argv)
    view = run(args.folder, args.base_url, args.api_key)
    if args.json:
        json.dump(view, sys.stdout, ensure_ascii=False, indent=2, default=str)
    else:
        print_summary(view)


if __name__ == "__main__":
    main()
