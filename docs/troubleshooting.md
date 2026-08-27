# Troubleshooting

Every entry here is a failure we actually hit while building Subset, with
the diagnosis that worked. Start by reading the app's own output — it
narrates its health (`[subset …]` status lines every 10 seconds) and names
most of these conditions explicitly.

## Reading the stats line

```
[subset  20.1s] send backlog 0.0s | interims 1.0/s, turnaround avg 0.02s max 0.04s | finals 0.3/s, ...
```

- **send backlog** rising above ~1s → audio is being produced faster than it
  uploads (network trouble). The queue drops oldest audio at 5 s so captions
  stay current at the cost of missed words.
- **interims/finals "none"** while people are clearly speaking → see
  *Captions sparse* below.
- **turnaround** is the transcription round trip. Healthy is < 0.2 s. If
  it's healthy but captions *feel* behind, the engine is emitting rarely
  (cadence), not late.

## Video problems

**Black video from the source (menus never appear).**
HDCP negotiation between your source and capture card, or the source is set
to a mode the card can't accept (4K/HDR on a 1080p card). Set the source to
1080p60 SDR first; see [hardware.md](hardware.md#about-hdcp) for the HDCP
reality.

**Menus display, but a specific app blacks out.**
That app demands HDCP; your card isn't negotiating it. Nothing software can
do — watch different content or use a different source/card.

**The TV isn't offered in OBS's Fullscreen Projector menu.**
macOS isn't detecting the TV as a display. Select the TV's input *first*,
then replug the Mac end of the HDMI cable (many TVs only assert hotplug on
the active input). Then System Settings → Displays, hold **Option** →
**Detect Displays**. If the Mac knows the TV's name but never lights it up,
replace the cable — EDID survives cheap cables, high-bandwidth video
doesn't.

**Washed-out / gray picture.**
The source is sending HDR to a card that doesn't tone-map. Force SDR on the
source.

## Audio problems

**No sound on the TV.**
OBS Settings → Audio → Advanced → Monitoring Device must be the TV's HDMI
output, and the `Subset Audio` input must be "Monitor Only" (the app sets
this). Also confirm OBS has microphone permission (System Settings →
Privacy & Security).

**Garbled sound or loud static.**
The source is sending a compressed bitstream (Dolby) instead of PCM. Force
stereo PCM on the source (Apple TV: Audio Format → Change Format → Stereo).

**Brief audio dropouts every few seconds on the TV.**
Three causes we've met, in order of likelihood:
1. Another program has opened the capture device with a large buffer size —
   Core Audio device buffers are shared, so one client's big blocks make
   OBS's monitoring hiccup. (Subset itself uses native block sizes for
   exactly this reason.)
2. Sample-rate mismatch: OBS and all devices should be at 48 kHz (check
   Audio MIDI Setup).
3. OBS's "Low Latency Audio Buffering Mode" is checked. Uncheck it.
4. If rare blips persist after all of the above, it's clock drift inside
   OBS's monitoring path itself — the source's audio clock and the Mac's
   output clock are physically independent, and OBS bridges them with
   blind buffer slips. Bypass monitoring entirely: run with
   `--audio-out "<TV device name>"` and the app plays audio to the TV
   through a drift-compensating buffer that corrects only during silent
   moments. The stats line gains a `passthrough depth/trims/underruns`
   field; occasional trims are normal and inaudible, and a device that
   causes underruns automatically earns a deeper buffer.

**Captions reference words you can't hear.**
Your TV volume is just low — the card taps the source's full-level audio
regardless of TV volume.

## Caption problems

**One sentence, then nothing — or long sparse stretches — while speech is
obvious.**
The STT service is congested or your session landed on a sick backend. The
app detects ~15 s of speech with no transcripts and prints
`recycling session/connection (service may be congested)`, then reconnects.
If recycles repeat, switch engines (`--engine deepgram` / `--engine
gemini`) — congestion is per-provider. Verify audio is genuinely flowing
with `uv run subset --no-obs --verbose --seconds 20`.

**Captions stopped after plugging/unplugging a display.**
Display hot-plugs churn the audio device list and can kill the capture
stream. The app self-heals within seconds (`audio input stalled — reopening
capture device`). If you're on a version without that watchdog, restart the
app.

**Doubled or dueling captions.**
Two instances are running. `pkill -f subset`, then start one.

**Captions vanish mid-sentence on reconnects.**
Fixed: pending interim text is promoted into the finalized tail whenever a
session ends. If you see it, you're on an old build.

**A lone word appears on the bottom line, then grows.**
Not a bug — live roll-up can't look ahead to balance line breaks. It's the
one visual tell that captions are generated in real time.

**`lost OBS — reconnecting` repeats.**
OBS quit or its WebSocket server is off/wrong password. The app reconnects
and rebuilds automatically once OBS is back; check `OBS_WS_PASSWORD` in
`.env` against Tools → WebSocket Server Settings.

## Key & startup problems

**`API key file not found` / `Could not find an API key token`.**
Put the key in `gemini-key.txt` or `deepgram-key.txt` at the repo root —
either the bare key on its own line, or the provider console's export block
(a line starting `API Key:`). The loader never prints file contents, and
`*-key.txt` is git-ignored.

**`No audio input device matching 'USB3.0 Audio'`.**
The card is unplugged, or yours enumerates under a different name — run
`uv run subset --list-devices` and pass `--audio-device "<substring>"`
(likewise `--video-device` for OBS's dropdown name).

**Gemini `1011 internal error` in the logs.**
Server-side; the app reconnects with backoff and keeps your text on screen.
Frequent 1011s usually accompany congestion — consider the other engine.
