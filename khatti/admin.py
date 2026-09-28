"""Admin CLI.

    python -m khatti.admin create-tenant "Bank of Example"   # prints tenant id + API key (shown once)
    python -m khatti.admin create-key <tenant_id> [label]
    python -m khatti.admin gen-data-key                       # value for KHATTI_DATA_KEY
    python -m khatti.admin verify-audit <tenant_id>
    python -m khatti.admin export-openapi [path]
    python -m khatti.admin seed-demo                          # demo tenant + judge credentials
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path


async def _store():
    from .store import Store

    url = os.getenv("KHATTI_DATABASE_URL")
    if not url:
        raise SystemExit("KHATTI_DATABASE_URL is not set")
    return await Store.connect(url)


async def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("help", [])
    if cmd == "gen-data-key":
        from .crypto import Keyring

        print(Keyring.generate()[1])
    elif cmd == "export-openapi":
        import yaml

        from .api import create_app
        from .services import Services
        from .registry import default_registry

        path = Path(args[0] if args else "openapi/khatti.v1.yaml")
        spec = create_app(Services(registry=default_registry())).openapi()
        spec["openapi"] = "3.1.0"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"wrote {path}")
    elif cmd == "seed-demo":
        store = await _store()
        try:
            tid = await store.create_tenant("Khatti Demo Bank (fictional)")
            server = await store.create_api_key(tid, "demo-server")
            judge = await store.create_api_key(tid, "judges")
        finally:
            await store.close()
        base = os.getenv("KHATTI_PUBLIC_BASE_URL", "http://localhost:8000")
        print(f"""Demo tenant {tid}
  server API key : {server}
  judges API key : {judge}   (mint reviewer tokens with POST /v1/auth/token)

Try it:
  python -m khatti.demo samples/glare_on_id --base-url {base} --api-key {judge}
Samples are fictional (SPECIMEN-watermarked); see samples/README.md.""")
    elif cmd in ("create-tenant", "create-key", "verify-audit"):
        store = await _store()
        try:
            if cmd == "create-tenant":
                tid = await store.create_tenant(args[0] if args else "tenant")
                print(f"tenant_id={tid}\napi_key={await store.create_api_key(tid, 'initial')}")
            elif cmd == "create-key":
                print(await store.create_api_key(uuid.UUID(args[0]), args[1] if len(args) > 1 else ""))
            else:
                ok = await store.verify_audit_chain(uuid.UUID(args[0]))
                print("audit chain OK" if ok else "audit chain BROKEN")
                raise SystemExit(0 if ok else 1)
        finally:
            await store.close()
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
