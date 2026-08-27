"""Wire everything together: audio → Gemini → captions → OBS."""

import argparse
import asyncio
import contextlib
import os
import time

from subset import config
from subset.captions import RollUpCaptions
from subset.config import redact

# Captions linger this long after the last speech, then clear.
_STALE_S = 6.0
_PUSH_INTERVAL_S = 0.1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="subset", description="Offset subtitles that don't spoil everything."
    )
    parser.add_argument("--list-devices", action="store_true", help="list audio inputs and exit")
    parser.add_argument("--no-obs", action="store_true", help="print transcripts only")
    parser.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--verbose", action="store_true", help="also print interim hypotheses")
    parser.add_argument("--audio-device", default=config.DEFAULT_AUDIO_DEVICE)
    parser.add_argument("--video-device", default=config.DEFAULT_VIDEO_DEVICE)
    parser.add_argument("--model", default=config.DEFAULT_MODEL)
    parser.add_argument("--key-file", default=config.DEFAULT_KEY_FILE)
    parser.add_argument("--obs-url", default=os.environ.get("OBS_WS_URL", config.DEFAULT_OBS_URL))
    parser.add_argument("--obs-password", default=None, help="defaults to $OBS_WS_PASSWORD")
    parser.add_argument("--font-size", type=int, default=56)
    parser.add_argument("--max-lines", type=int, default=2)
    parser.add_argument("--max-chars", type=int, default=46)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    from subset.audio import AudioCapture
    from subset.transcriber import LiveTranscriber

    api_key = config.load_api_key(args.key_file)
    captions = RollUpCaptions(max_lines=args.max_lines, max_chars=args.max_chars)

    def status(message: str) -> None:
        print(f"[subset] {message}", flush=True)

    def on_final(text: str) -> None:
        captions.on_final(text)
        print(f"  » {text}", flush=True)

    def on_interim(text: str) -> None:
        captions.on_interim(text)
        if args.verbose:
            print(f"  … {text}", flush=True)

    obs = None
    if not args.no_obs:
        from subset.obs import ObsCaptioner

        obs = ObsCaptioner(args.obs_url, args.obs_password or os.environ.get("OBS_WS_PASSWORD"))
        await obs.connect()
        await obs.ensure_scene(args.video_device, args.audio_device, args.font_size)
        status("OBS scene 'Subset' ready")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    capture = AudioCapture(args.audio_device, loop, queue)
    capture.start()
    status(f"capturing audio from '{capture.device_name}'")

    transcriber = LiveTranscriber(api_key, args.model, on_interim, on_final, status)

    async def pusher() -> None:
        pushed_version = -1
        while True:
            await asyncio.sleep(_PUSH_INTERVAL_S)
            if not captions.empty and time.monotonic() - captions.last_activity > _STALE_S:
                captions.clear()
            if captions.version == pushed_version:
                continue
            if obs is None:
                pushed_version = captions.version
                continue
            try:
                await obs.set_text(captions.render())
                pushed_version = captions.version
            except Exception:
                status("lost OBS — reconnecting (transcription keeps running)")
                await asyncio.sleep(2)
                with contextlib.suppress(Exception):
                    await obs.reconnect_and_repair()
                    status("OBS reconnected")

    async def audio_watchdog() -> None:
        # Display hot-plugs churn the Core Audio device list, which can kill
        # the input stream while the process looks healthy. Self-heal.
        while True:
            await asyncio.sleep(3)
            if capture.last_audio_age() > 6:
                status("audio input stalled — reopening capture device")
                try:
                    capture.reopen()
                    status(f"capturing audio from '{capture.device_name}'")
                except Exception as exc:
                    status(f"audio reopen failed: {redact(exc)} — will retry")

    tasks = [
        asyncio.create_task(transcriber.run(queue)),
        asyncio.create_task(pusher()),
        asyncio.create_task(audio_watchdog()),
    ]
    try:
        gathered = asyncio.gather(*tasks)
        if args.seconds:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(gathered, timeout=args.seconds)
            status(f"stopping after {args.seconds:.0f}s (--seconds)")
        else:
            await gathered
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.gather(*tasks)
        capture.stop()
        if obs is not None:
            with contextlib.suppress(Exception):
                await obs.set_text("")
                await obs.close()


def main() -> None:
    args = _parse_args()
    config.load_dotenv()
    if args.list_devices:
        from subset.audio import list_input_devices

        for name in list_input_devices():
            print(name)
        return
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n[subset] stopped")
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"[subset] fatal: {redact(exc)}") from exc


if __name__ == "__main__":
    main()
