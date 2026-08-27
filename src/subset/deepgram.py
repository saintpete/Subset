"""Deepgram streaming STT over a raw WebSocket (/v1/listen).

Same surface as GeminiTranscriber: feed it the audio queue, get
on_interim/on_final callbacks. Deepgram's segment semantics match our
roll-up captions natively — interims cover only the current segment and
`is_final` closes it — and server-side endpointing replaces our
breath-detection trick. No session time cap, so connections run until
an error or transcript starvation forces a recycle.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from urllib.parse import urlencode

import websockets

from subset.config import redact

_SILENCE_RMS = 0.006
_STARVE_CHUNKS = 150  # ~15s of speech with no transcripts → recycle
_ENDPOINTING_MS = 400  # server finalizes after this much post-speech silence


class DeepgramTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        on_interim: Callable[[str], None],
        on_final: Callable[[str], None],
        on_status: Callable[[str], None],
        on_session_end: Callable[[], None] | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._on_interim = on_interim
        self._on_final = on_final
        self._status = on_status
        self._on_session_end = on_session_end
        self.last_sent_capture: float | None = None
        self._last_transcript = time.monotonic()

    def _url(self) -> str:
        params = {
            "model": self._model,
            "encoding": "linear16",
            "sample_rate": "16000",
            "channels": "1",
            "interim_results": "true",
            "smart_format": "true",
            "endpointing": str(_ENDPOINTING_MS),
        }
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    async def run(self, queue: asyncio.Queue) -> None:
        backoff = 1.0
        while True:
            try:
                await self._one_connection(queue)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._status(
                    f"deepgram error: {redact(exc)} — reconnecting in {backoff:.0f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _one_connection(self, queue: asyncio.Queue) -> None:
        headers = {"Authorization": f"Token {self._key}"}
        try:
            connect = websockets.connect(self._url(), additional_headers=headers)
        except TypeError:  # older websockets naming
            connect = websockets.connect(self._url(), extra_headers=headers)
        async with connect as ws:
            self._status(f"Deepgram stream open ({self._model})")
            receiver = asyncio.create_task(self._receive(ws))
            self._last_transcript = time.monotonic()
            seen_transcript = self._last_transcript
            speechy = 0
            try:
                while True:
                    try:
                        pcm, rms, captured = await asyncio.wait_for(
                            queue.get(), timeout=5.0
                        )
                    except TimeoutError:
                        if receiver.done():
                            receiver.result()
                            raise RuntimeError("Deepgram receive loop ended")
                        # Deepgram drops idle sockets after ~10s of no data.
                        await ws.send(json.dumps({"type": "KeepAlive"}))
                        continue
                    await ws.send(pcm)
                    self.last_sent_capture = captured
                    if receiver.done():
                        receiver.result()
                    if self._last_transcript != seen_transcript:
                        seen_transcript = self._last_transcript
                        speechy = 0
                    if rms >= _SILENCE_RMS:
                        speechy += 1
                    if speechy >= _STARVE_CHUNKS:
                        self._status(
                            "speech flowing but no transcripts for ~15s — "
                            "recycling connection"
                        )
                        break
            finally:
                with contextlib.suppress(Exception):
                    await ws.send(json.dumps({"type": "CloseStream"}))
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await receiver
                if self._on_session_end is not None:
                    self._on_session_end()

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") != "Results":
                continue
            alternatives = (msg.get("channel") or {}).get("alternatives") or []
            text = (alternatives[0].get("transcript") or "").strip() if alternatives else ""
            if not text:
                continue
            self._last_transcript = time.monotonic()
            if msg.get("is_final"):
                self._on_final(text)
            else:
                self._on_interim(text)
