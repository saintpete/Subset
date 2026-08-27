"""Gemini 3.5 Transcribe Live client with seamless session rotation.

Live sessions cap at 10 minutes, so we open a fresh one before the
limit — preferring a moment of silence so no words straddle the swap —
and reconnect with backoff on errors.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable

from google import genai
from google.genai import types

from subset.config import HARD_ROTATE_S, ROTATE_AFTER_S, redact

# ~100 ms per chunk, so 5 consecutive quiet chunks ≈ half a second of silence.
_SILENCE_RMS = 0.006
_SILENT_CHUNKS = 5


class LiveTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        on_interim: Callable[[str], None],
        on_final: Callable[[str], None],
        on_status: Callable[[str], None],
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._on_interim = on_interim
        self._on_final = on_final
        self._status = on_status

    def _config(self) -> types.LiveConnectConfig:
        try:
            audio_cfg = types.AudioTranscriptionConfig(language_codes=[])
        except TypeError:  # older SDK without language_codes
            audio_cfg = types.AudioTranscriptionConfig()
        return types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=audio_cfg,
        )

    async def run(self, queue: asyncio.Queue) -> None:
        backoff = 1.0
        while True:
            try:
                await self._one_session(queue)
                backoff = 1.0  # clean rotation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._status(f"session error: {redact(exc)} — reconnecting in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _one_session(self, queue: asyncio.Queue) -> None:
        async with self._client.aio.live.connect(
            model=self._model, config=self._config()
        ) as session:
            self._status("Gemini live session open")
            receiver = asyncio.create_task(self._receive(session))
            started = time.monotonic()
            silent_run = 0
            try:
                while True:
                    try:
                        pcm, rms = await asyncio.wait_for(queue.get(), timeout=5.0)
                    except TimeoutError:
                        # No audio for 5s. Don't sit wedged: surface receiver
                        # death, and rotate rather than letting the server
                        # kill an idle session at the 10-minute cap.
                        if receiver.done():
                            receiver.result()
                            raise RuntimeError("Gemini receive loop ended")
                        if time.monotonic() - started >= ROTATE_AFTER_S:
                            self._status("rotating idle live session")
                            break
                        continue
                    if not hasattr(session, "send_realtime_input"):
                        raise RuntimeError(
                            "google-genai SDK lacks send_realtime_input — run `uv sync -U`"
                        )
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                    )
                    if receiver.done():
                        receiver.result()  # surface receive-side errors
                    silent_run = silent_run + 1 if rms < _SILENCE_RMS else 0
                    age = time.monotonic() - started
                    if age >= HARD_ROTATE_S or (
                        age >= ROTATE_AFTER_S and silent_run >= _SILENT_CHUNKS
                    ):
                        self._status("rotating live session before the 10-minute cap")
                        break
                await asyncio.sleep(0.8)  # let trailing finals arrive
            finally:
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await receiver

    async def _receive(self, session) -> None:
        async for response in session.receive():
            content = getattr(response, "server_content", None)
            if content is None:
                continue
            interim = getattr(content, "interim_input_transcription", None)
            if interim is not None and getattr(interim, "text", None):
                self._on_interim(interim.text)
            final = getattr(content, "input_transcription", None)
            if final is not None and getattr(final, "text", None):
                self._on_final(final.text)
