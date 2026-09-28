import asyncio
from pathlib import Path

import httpx

from khatti.demo import print_summary, run

from .test_api import ADMIN_URL, Env, pytestmark  # noqa: F401  (Postgres-backed; skipped without it)


def test_demo_client_runs_a_sample_session(tmp_path, images, pages, capsys):
    folder = tmp_path / "s"
    folder.mkdir()
    for slot, img in images.items():
        (folder / f"{slot}.jpg").write_bytes(img)

    async def scenario():
        env = await Env().start(tmp_path / "store", images, pages)
        try:
            # drive the sync client through the ASGI app on a worker thread, processing the queue meanwhile
            transport = httpx.ASGITransport(app=env.api._transport.app)
            loop = asyncio.get_running_loop()

            def sync_request(request: httpx.Request) -> httpx.Response:
                async def go():
                    async with httpx.AsyncClient(transport=transport, base_url="http://khatti.test") as c:
                        return await c.send(request)
                resp = asyncio.run_coroutine_threadsafe(go(), loop).result()
                return httpx.Response(resp.status_code, headers=resp.headers, content=resp.content)

            client = httpx.Client(base_url="http://khatti.test", transport=httpx.MockTransport(sync_request))

            async def worker():
                from khatti.worker import process_one
                while not await process_one(env.service):
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(worker())
            view = await asyncio.to_thread(run, folder, "http://khatti.test", env.key_a, 30, client)
            await task
            return view
        finally:
            await env.close()

    view = asyncio.run(scenario())
    assert view["status"] in ("completed", "needs_review")
    print_summary(view)
    assert "national_id_front" in capsys.readouterr().out
