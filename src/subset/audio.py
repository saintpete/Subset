"""Capture the card's HDMI audio and downsample it for Gemini.

The MS2130 exposes HDMI audio as a 48 kHz stereo Core Audio input;
Gemini live transcription wants raw 16-bit PCM at 16 kHz mono. 48→16 is
an integer factor of 3, so a small windowed-sinc FIR plus decimation
does the job without any DSP dependency.
"""

import asyncio

import numpy as np
import sounddevice as sd


def list_input_devices() -> list[str]:
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


def find_input_device(name_substr: str) -> int:
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and name_substr.lower() in dev["name"].lower():
            return idx
    raise SystemExit(
        f"No audio input device matching {name_substr!r}.\n"
        f"Inputs seen: {list_input_devices()}\n"
        "Is the capture card plugged in? (Try --list-devices.)"
    )


class _Decimator:
    """Streaming lowpass-then-take-every-Nth with carry across blocks."""

    def __init__(self, factor: int) -> None:
        self.factor = factor
        cutoff = 0.45 / factor  # fraction of the input sample rate
        n = np.arange(121) - 60
        taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(121)
        self.taps = (taps / taps.sum()).astype(np.float32)
        self._carry = np.zeros(len(self.taps) - 1, dtype=np.float32)
        self._phase = 0

    def process(self, block: np.ndarray) -> np.ndarray:
        samples = np.concatenate([self._carry, block])
        filtered = np.convolve(samples, self.taps, mode="valid")
        out = filtered[self._phase :: self.factor]
        self._phase = (self._phase - len(filtered)) % self.factor
        self._carry = samples[-(len(self.taps) - 1) :]
        return out


class AudioCapture:
    """Feeds (pcm16_bytes, rms) tuples into an asyncio queue from a Core Audio thread."""

    def __init__(
        self,
        device_substr: str,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
        in_rate: int = 48000,
        out_rate: int = 16000,
        chunk_ms: int = 100,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._decimator = _Decimator(in_rate // out_rate)
        device = find_input_device(device_substr)
        channels = min(2, int(sd.query_devices(device)["max_input_channels"]))
        self.device_name = sd.query_devices(device)["name"]
        self._stream = sd.InputStream(
            device=device,
            channels=channels,
            samplerate=in_rate,
            dtype="float32",
            blocksize=in_rate * chunk_ms // 1000,
            callback=self._callback,
        )

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        mono = indata.mean(axis=1) if indata.ndim == 2 else indata
        out = self._decimator.process(mono.astype(np.float32))
        if not len(out):
            return
        rms = float(np.sqrt(np.mean(np.square(out))))
        pcm = (np.clip(out, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        self._loop.call_soon_threadsafe(self._enqueue, (pcm, rms))

    def _enqueue(self, item: tuple[bytes, float]) -> None:
        # If the consumer stalls (e.g. a reconnect), drop the oldest audio
        # rather than building an ever-growing caption lag.
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()
