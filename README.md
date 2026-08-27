# Subset

**Offset subtitles that don't spoil everything.**

Subtitles on TV shows and movies routinely appear *before* the words are spoken.
Instead of clarifying what you just heard, they spoil what you're about to hear —
and once they're on screen, it's hard not to peek. Subset fixes this by throwing
the shipped subtitles away and regenerating captions live from the audio itself.
Captions derived from the audio can trail it, but can never precede it.

## How it works

```
Apple TV ──HDMI──▶ capture card (MS2130) ──USB──▶ Mac
                                                   │ audio: 48 kHz stereo → 16 kHz mono PCM
                                                   ▼
                                   Gemini 3.5 Transcribe Live (WebSocket)
                                                   │ interim + finalized text
                                                   ▼
                              OBS: captured video + roll-up caption overlay
                                                   │
                                                   ▼
                                      Mac HDMI out ──▶ television
```

The Mac sits between the source device and the TV. A Python app captures the
card's audio, streams it to `gemini-3.5-transcribe-live`, folds interim and
finalized transcripts into live-TV-style roll-up captions, and pushes them into
an OBS text source over obs-websocket. OBS composes captions over the captured
video; its fullscreen projector on the Mac's HDMI output feeds the TV.

This is a proof of concept for one living room, not a product.

## Setup

Requirements: macOS, Python 3.12+, [uv](https://docs.astral.sh/uv/), OBS Studio 30+,
a UVC HDMI capture card, and a Gemini API key.

```sh
uv sync
```

Two transcription engines are supported: **Gemini** (`gemini-3.5-transcribe-live`,
the default) and **Deepgram** (`nova-3`) — select with `--engine` or the
`SUBSET_ENGINE` env var. Put the Gemini key in `gemini-key.txt` and/or the
Deepgram key in `deepgram-key.txt` at the repo root (`*-key.txt` is git-ignored;
a bare key or a pasted console export block both work). Enable OBS's WebSocket
server (Tools → WebSocket Server Settings) and put its password in `.env` as
`OBS_WS_PASSWORD=…` (also git-ignored).

On first run the app builds an OBS scene named **Subset** automatically: the
capture card's video, its audio set to monitor-only, and a styled caption
overlay. No manual scene assembly.

## Run

```sh
uv run subset
```

Useful flags: `--engine gemini|deepgram` (transcription backend),
`--list-devices` (show audio inputs), `--no-obs` (transcribe to stdout only),
`--verbose` (print interim hypotheses with turnaround), `--seconds N` (timed run),
`--audio-device` / `--video-device` (substring match, default `USB3.0 …`),
`--font-size`, `--max-lines`, `--max-chars`.

For the living room: connect the Mac to the TV over HDMI, set OBS's monitoring
device to the TV output (Settings → Audio → Advanced), and open a fullscreen
projector on the TV display (right-click the preview → Fullscreen Projector).

## Notes

- **Latency:** captions trail speech by roughly the transcription latency
  (~0.5–2 s). That trailing is the point — they can't lead.
- **Session rotation:** Gemini live sessions cap at 10 minutes; the app rotates
  to a fresh session early, preferring a moment of silence, and reconnects with
  backoff on errors.
- **HDCP:** capture cards vary in what protected sources they'll pass. Test
  yours with the content you actually watch.
- **Audio format:** if captured audio is garbled or silent, set the source
  device to stereo/PCM output (e.g. Apple TV: Settings → Video and Audio →
  Audio Format → Change Format → Stereo).
