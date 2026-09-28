import json

import httpx

from jobs.inventory.__main__ import PROBE_NUMBER, main


def fake_token_factory(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-3-super-120b-a12b"}, {"id": "Qwen/Qwen2.5-VL-72B-Instruct"}]})
    body = json.loads(request.content)
    has_image = any(isinstance(m["content"], list) for m in body["messages"])
    if body["model"].startswith("nvidia/"):
        if has_image:
            return httpx.Response(400, json={"error": "does not support image input"})
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": "json_schema unsupported"})
    content = json.dumps({"number": PROBE_NUMBER}) if has_image else '{"answer": "yes"}'
    choice = {"message": {"content": content}}
    if body.get("logprobs") and body["model"].startswith("Qwen"):
        choice["logprobs"] = {"content": [{"token": "x", "logprob": -0.1}]}
    return httpx.Response(200, json={"choices": [choice]})


def test_inventory_probes_capabilities(tmp_path):
    md = main(["--key", "k", "--url", "https://tf.test/v1", "--probe", "all", "--out", str(tmp_path)],
              transport=httpx.MockTransport(fake_token_factory))
    data = json.loads((tmp_path / "inventory.json").read_text())
    probes = {p["model"]: p for p in data["probes"]}
    nem, qwen = probes["nvidia/nemotron-3-super-120b-a12b"], probes["Qwen/Qwen2.5-VL-72B-Instruct"]
    assert nem["text"]["ok"] and not nem["image"]["ok"] and "image input" in nem["image"]["error"]
    assert nem["json_object"]["ok"] and not nem["json_schema"]["ok"]
    assert qwen["image"]["ok"] and qwen["logprobs"]["ok"]
    assert "Qwen/Qwen2.5-VL-72B-Instruct" in md.read_text().split("Suggested reader B")[1]
