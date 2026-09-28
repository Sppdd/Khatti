"""Smoke-test a reader endpoint with one image and print the transcript.

    python services/reader-omni/smoke.py https://<endpoint>/v1 <model> <api_key> path/to/image.jpg
"""

import asyncio
import sys
from pathlib import Path

from khatti.config import EndpointConfig
from khatti.llm import ChatClient
from khatti.readers import VisionChatReader


async def main(base_url: str, model: str, key: str, image: str) -> None:
    reader = VisionChatReader(ChatClient(EndpointConfig("omni", base_url, model, key), timeout_s=180))
    data = Path(image).read_bytes()
    for t in await reader.transcribe([(data, "image/jpeg")]):
        print(f"status={t.status.value} caption={t.caption!r} error={t.error}")
        for line in t.lines:
            print(f"  {line.line_id}: {line.text}")


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:5]))
