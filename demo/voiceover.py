"""Voices demo/narration.json into demo/voice/<id>.mp3 with a neural TTS voice (needs network).

    uv run --with edge-tts python demo/voiceover.py [voice]

Recording your own takes instead? Save them as demo/voice/<id>.mp3 and skip this step;
make_demo.py only reads the files. Dev tooling only; the engine does not depend on it.
"""
import asyncio
import json
import os
import ssl
import sys
from pathlib import Path

import certifi
import edge_tts
import edge_tts.communicate
import edge_tts.voices

DEMO = Path(__file__).resolve().parent
VOICE = sys.argv[1] if len(sys.argv) > 1 else "en-US-AndrewMultilingualNeural"

# TLS-inspecting antivirus (e.g. Norton) re-signs traffic with its own root; trust it too
extra = os.environ.get("EXTRA_CA_FILE") or os.environ.get("NODE_EXTRA_CA_CERTS")
if extra:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.load_verify_locations(extra)
    edge_tts.communicate._SSL_CTX = edge_tts.voices._SSL_CTX = ctx


async def main():
    out = DEMO / "voice"
    out.mkdir(exist_ok=True)
    for line in json.loads((DEMO / "narration.json").read_text(encoding="utf-8")):
        mp3 = out / f"{line['id']}.mp3"
        for attempt in range(3):  # the service drops the odd request
            try:
                await edge_tts.Communicate(line["say"], VOICE, rate="-3%").save(str(mp3))
                break
            except edge_tts.exceptions.NoAudioReceived:
                if attempt == 2:
                    raise
        print("voiced", line["id"])


asyncio.run(main())
