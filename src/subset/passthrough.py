"""Direct audio passthrough: capture card → output device, bypassing OBS.

The source's HDMI audio and the Mac's output run on two physically
independent 48 kHz clocks, so whoever bridges them must occasionally add
or remove samples. OBS's monitoring path slips its buffer wherever the
drift lands — an audible blip every so often. This passthrough owns the
bridge instead: a ring buffer between two native-block streams, with
drift corrections applied only during quiet moments (a few trimmed
milliseconds under silence are inaudible) and a hard resync bound so
latency can never wander.
"""

import contextlib
import threading
import time

import numpy as np
import sounddevice as sd

from subset.audio import find_input_device

_RATE = 48000
_QUIET_RMS = 0.01  # corrections happen only below this level
_CHECK_S = 2.0  # how often a correction is considered
_TRIM_MS = 12  # max audio added/removed per correction


def find_output_device(name_substr: str) -> int:
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0 and name_substr.lower() in dev["name"].lower():
            return idx
    outs = [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]
    raise SystemExit(
        f"No audio output matching {name_substr!r}. Outputs seen: {outs}\n"
        "(Is the TV connected? Its HDMI audio device appears with the display.)"
    )


class AudioPassthrough:
    def __init__(
        self,
        input_substr: str,
        output_substr: str,
        target_ms: int = 100,
        on_status=print,
    ) -> None:
        self._in_substr = input_substr
        self._out_substr = output_substr
        self._target = int(_RATE * target_ms / 1000)
        self._status = on_status
        self._lock = threading.Lock()
        self._buf = np.zeros((0, 2), dtype=np.float32)
        self._primed = False
        self._depth_ema = 0.0  # output devices pull big blocks; smooth the sawtooth
        self._last_check = time.monotonic()
        self._last_in = time.monotonic()
        self._last_out = time.monotonic()
        self._trims = 0
        self._underruns = 0
        self._in_stream: sd.InputStream | None = None
        self._out_stream: sd.OutputStream | None = None
        self.output_name = ""

    def start(self) -> None:
        in_idx = find_input_device(self._in_substr)
        out_idx = find_output_device(self._out_substr)
        in_ch = min(2, int(sd.query_devices(in_idx)["max_input_channels"]))
        self.output_name = sd.query_devices(out_idx)["name"]
        self._in_stream = sd.InputStream(
            device=in_idx,
            channels=in_ch,
            samplerate=_RATE,
            dtype="float32",
            blocksize=0,
            callback=self._on_in,
        )
        self._out_stream = sd.OutputStream(
            device=out_idx,
            channels=2,
            samplerate=_RATE,
            dtype="float32",
            blocksize=0,
            callback=self._on_out,
        )
        self._last_in = self._last_out = time.monotonic()
        self._in_stream.start()
        self._out_stream.start()

    def stop(self) -> None:
        for stream in (self._in_stream, self._out_stream):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.stop()
                    stream.close()
        self._in_stream = self._out_stream = None

    def stalled(self, max_age: float = 6.0) -> bool:
        now = time.monotonic()
        return (now - self._last_in) > max_age or (now - self._last_out) > max_age

    def reopen(self) -> None:
        self.stop()
        with self._lock:
            self._buf = np.zeros((0, 2), dtype=np.float32)
            self._primed = False
        self.start()

    def stats(self) -> str:
        with self._lock:
            depth_ms = int(self._depth_ema) * 1000 // _RATE
            target_ms = self._target * 1000 // _RATE
            return (
                f"passthrough depth ~{depth_ms}ms (target {target_ms}ms) "
                f"trims {self._trims} underruns {self._underruns}"
            )

    # -- audio-thread callbacks --------------------------------------------

    def _on_in(self, indata, frames, time_info, status) -> None:
        self._last_in = time.monotonic()
        if indata.ndim == 2 and indata.shape[1] >= 2:
            block = indata[:, :2].astype(np.float32)
        else:
            mono = indata.reshape(len(indata), -1).mean(axis=1).astype(np.float32)
            block = np.column_stack([mono, mono])
        rms = float(np.sqrt(np.mean(np.square(block))))
        with self._lock:
            self._buf = np.concatenate([self._buf, block])
            # Prime past target: the output device's first callback can pull
            # a large block, and a just-barely-primed buffer would dry out.
            if not self._primed and len(self._buf) >= self._target * 3 // 2:
                self._primed = True
            self._maybe_correct(rms)

    def _on_out(self, outdata, frames, time_info, status) -> None:
        self._last_out = time.monotonic()
        with self._lock:
            if not self._primed:
                outdata[:] = 0
                return
            n = min(frames, len(self._buf))
            outdata[:n] = self._buf[:n]
            if n < frames:
                outdata[n:] = 0
                self._underruns += 1
                self._primed = False  # dry: go silent and re-prime cleanly
                # A device that ran us dry earns a deeper buffer next prime
                # (bursty consumers, rate-converted outputs). Latency is
                # bounded at 400ms.
                self._target = min(self._target * 3 // 2, _RATE * 400 // 1000)
            self._buf = self._buf[n:]
            self._depth_ema = 0.9 * self._depth_ema + 0.1 * len(self._buf)

    def _maybe_correct(self, block_rms: float) -> None:
        """Keep buffer depth near target. Callers hold the lock."""
        now = time.monotonic()
        if now - self._last_check < _CHECK_S:
            return
        self._last_check = now
        if len(self._buf) > self._target * 4:  # runaway: resync regardless of level
            self._buf = self._buf[len(self._buf) - self._target :]
            self._trims += 1
            return
        if block_rms >= _QUIET_RMS:
            return
        depth = int(self._depth_ema)  # smoothed: ignore the output-block sawtooth
        max_trim = _RATE * _TRIM_MS // 1000
        slack = self._target // 2
        if depth > self._target + slack:
            cut = min(depth - self._target, max_trim, len(self._buf))
            self._buf = self._buf[cut:]
            self._trims += 1
        elif self._primed and depth < self._target - slack:
            pad = np.zeros((min(self._target - depth, max_trim), 2), dtype=np.float32)
            self._buf = np.concatenate([pad, self._buf])
            self._trims += 1
