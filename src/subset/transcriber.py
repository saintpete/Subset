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

# Hybrid VAD: after speech, ~400 ms of quiet triggers audio_stream_end so the
# server finalizes at natural breaths instead of waiting for long silences
# (which continuous programming never provides). Rate-limited so a long quiet
# stretch sends only one.
_BREATH_CHUNKS = 4
_MIN_END_INTERVAL_S = 2.0

# If this many speech-bearing chunks (~15s) stream out with zero transcripts
# back, assume the session landed on a congested backend and recycle it.
_STARVE_CHUNKS = 150


class GeminiTranscriber:
    def __init__(
        self,
        api_key: str,
        model: str,
        on_interim: Callable[[str], None],
        on_final: Callable[[str], None],
        on_status: Callable[[str], None],
        on_session_end: Callable[[], None] | None = None,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._on_interim = on_interim
        self._on_final = on_final
        self._status = on_status
        self._on_session_end = on_session_end
        self._stream_end_unsupported = False
        # Capture timestamp of the newest audio chunk actually sent —
        # lets the app estimate transcription turnaround.
        self.last_sent_capture: float | None = None

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
            self._last_transcript = started
            seen_transcript = started
            speechy = 0
            silent_run = 0
            spoke = False
            last_end = 0.0
            try:
                while True:
                    try:
                        pcm, rms, captured = await asyncio.wait_for(queue.get(), timeout=5.0)
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
                    self.last_sent_capture = captured
                    if receiver.done():
                        receiver.result()  # surface receive-side errors
                    if self._last_transcript != seen_transcript:
                        seen_transcript = self._last_transcript
                        speechy = 0
                    if rms >= _SILENCE_RMS:
                        speechy += 1
                    if speechy >= _STARVE_CHUNKS:
                        self._status(
                            "speech flowing but no transcripts for ~15s — "
                            "recycling session (service may be congested)"
                        )
                        break
                    if rms < _SILENCE_RMS:
                        silent_run += 1
                    else:
                        silent_run = 0
                        spoke = True
                    now = time.monotonic()
                    if (
                        spoke
                        and silent_run == _BREATH_CHUNKS
                        and now - last_end >= _MIN_END_INTERVAL_S
                    ):
                        await self._send_stream_end(session)
                        spoke = False
                        last_end = now
                    age = now - started
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
                if self._on_session_end is not None:
                    self._on_session_end()

    async def _send_stream_end(self, session) -> None:
        """Nudge the server to finalize the current utterance (hybrid VAD)."""
        if self._stream_end_unsupported:
            return
        try:
            await session.send_realtime_input(audio_stream_end=True)
        except TypeError:
            self._stream_end_unsupported = True
            self._status(
                "SDK doesn't support audio_stream_end — finals only at long pauses"
            )

    async def _receive(self, session) -> None:
        async for response in session.receive():
            content = getattr(response, "server_content", None)
            if content is None:
                continue
            interim = getattr(content, "interim_input_transcription", None)
            if interim is not None and getattr(interim, "text", None):
                self._last_transcript = time.monotonic()
                self._on_interim(interim.text)
            final = getattr(content, "input_transcription", None)
            if final is not None and getattr(final, "text", None):
                self._last_transcript = time.monotonic()
                self._on_final(final.text)
