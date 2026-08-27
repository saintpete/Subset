# Hardware & physical setup

## Shopping list

| Part | Tested with | Notes |
|---|---|---|
| Mac | MacBook Pro (Apple Silicon), macOS 15 | Needs one free USB-A/USB-C port and a video output to the TV (built-in HDMI port or a USB-C adapter) |
| HDMI→USB3 capture card | Generic **MacroSilicon MS2130** stick (sold as "Cam Link 4K … DigitPro") | Any UVC/UAC class-compliant card works driverless. 1080p60 is all you need |
| Video source | Apple TV 4K | Anything with HDMI out |
| TV | Samsung 4K TV | Any TV with a spare HDMI input |
| 2× HDMI cables | High Speed (or better) rated | One source→card, one Mac→TV. Cheap old cables cause the weirdest failures (see below) |

About capture cards: the MS2130 exposes itself as a USB webcam named
`USB3.0 Video` plus an audio input named `USB3.0 Audio`, delivering 1080p60
video and 48 kHz stereo PCM audio. Subset finds devices by name substring
(`--video-device` / `--audio-device`), so any UVC card works — check what
yours is called with `uv run subset --list-devices` (audio) and OBS's device
dropdown (video). Note that an authentic Elgato Cam Link 4K is *stricter*
about HDCP than most generics; see the HDCP section below.

## Wiring

```
Apple TV [HDMI out] ──cable 1──► [HDMI in] capture card [USB] ──► Mac
Mac [HDMI out] ──cable 2──► [HDMI input N] TV
```

1. Source's HDMI output → capture card's HDMI input.
2. Capture card → a USB 3 port on the Mac (a USB 2 port limits bandwidth).
3. Mac's HDMI output → a spare HDMI input on the TV.
4. Select that input on the TV.

The source is *not* connected to the TV at all anymore — the Mac is the
man-in-the-middle. Your source's remote still controls the source; expect a
couple hundred milliseconds of extra latency in menus (capture + compose).

**Order matters when connecting the TV:** some TVs only announce themselves
(assert HDMI hotplug) on the *currently selected* input. If macOS doesn't
detect the TV, select the TV input first, then unplug/replug the Mac end of
the cable. Still nothing? System Settings → Displays, hold **Option**, click
**Detect Displays**.

**The half-detected TV:** if the Mac knows the TV's name (it shows up in
`ioreg`, or flickers in and out) but never lights it up, suspect the cable —
the low-speed EDID channel works over almost anything, while the high-speed
video lanes need a genuinely High-Speed-rated cable. Swap cables before
debugging anything else.

## Source device settings (Apple TV shown)

- **Video: 1080p SDR, 60 Hz** (Settings → Video and Audio). The MS2130 caps
  at 1080p60, and HDR capture washes out badly on cards that don't tone-map.
- **Audio: if sound is garbled or static**, force PCM: Settings → Video and
  Audio → Audio Format → Change Format → **Stereo**. (Usually unnecessary —
  the card's EDID advertises stereo PCM and the source complies.)

## Display settings on the Mac

- System Settings → Displays → the TV: **Use as: Extended display** (not
  mirrored), 1080p, 60 Hz, **High Dynamic Range off**.
- Turn on the TV's **game mode** for that input if it has one — it trims the
  TV's own processing latency.
- Set display sleep generously (System Settings → Lock Screen) so the movie
  isn't interrupted.

## About HDCP

This project does **not** decrypt or circumvent HDCP. What happens with
protected content is purely a hardware negotiation between your source
device and your capture card:

- If the source demands HDCP and the card doesn't negotiate it, you get a
  black screen or "no signal" — from the source itself, before any software
  runs.
- Capture cards vary widely here, even unit to unit among generics. Sources
  also vary in *what* they protect (menus vs. playback, app by app).

Test with your own setup and content you have the right to view and process.
If protected apps black out through your card, the pipeline still works with
anything that doesn't demand HDCP (home media, many apps, AirPlay of your
own videos, game consoles, cameras).

## First-run permission prompts

macOS will ask once per app:

- **Terminal (or your shell app)** → microphone access, the first time
  `subset` opens the card's audio input. Audio capture devices count as
  "microphones" even when they're HDMI audio.
- **OBS** → camera and microphone access, the first time it opens the card.

If you dismissed a prompt, re-enable under System Settings → Privacy &
Security.
