# OBS Studio setup

Subset drives OBS over obs-websocket and **builds its own scene** — you
never add sources by hand. But a few one-time settings live in OBS's GUI and
can't be automated. Five minutes, once.

Tested with OBS Studio 30/31 on macOS (obs-websocket ships built-in since
OBS 28).

## 1. Install OBS

```sh
brew install --cask obs
```

Run it once and dismiss the auto-configuration wizard (any answers are fine;
Subset overrides the video settings it cares about).

## 2. Enable the WebSocket server

Tools → **WebSocket Server Settings**:

- ✅ Enable WebSocket server
- Port: `4455` (default)
- Authentication on, and copy the server password

Put the password in `.env` at the repo root:

```
OBS_WS_PASSWORD=<the password>
```

(Alternative for the automation-minded: with OBS **closed**, edit
`~/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json`
— set `"server_enabled": true` and your `"server_password"`. OBS rewrites
this file on quit, so never edit it while OBS runs.)

## 3. Audio settings

Settings → Audio:

- **Sample Rate: 48 kHz** (matches the capture card; mismatches cause
  periodic crackle on the monitoring path).
- **Global Audio Devices: set every entry to Disabled** — Desktop Audio and
  all Mic/Auxiliary slots. Otherwise OBS quietly captures your laptop
  microphone into the mix. Subset adds the one audio source it needs.

Settings → Audio → Advanced:

- **Monitoring Device: your TV's HDMI audio output** (it appears once the TV
  is connected as a display). This is the path the show's sound takes to the
  TV.
- Leave **Low Latency Audio Buffering Mode unchecked** — it shrinks buffers
  and causes dropouts with capture-card sources.

## 4. What Subset builds automatically

On startup the app connects to the WebSocket, asserts a 1920×1080@60 canvas,
and creates (or repairs) a scene named **Subset**:

| Item | What it is | Settings applied |
|---|---|---|
| `Subset Video` | the capture card's video | device matched by name, buffering off, scaled to fit the canvas |
| `Subset Audio` | the capture card's audio | device matched by name, **monitor only** (plays to the monitoring device, i.e. the TV) |
| `Subset Caption Line 1..N` | one text source per caption line | Helvetica Neue Bold, white, drop shadow, centered on the canvas |

Don't rename these inputs — the app finds them by name on every start (and
removes obsolete ones from older layouts). Caption geometry is controlled by
`--font-size`, `--max-lines`, and `--max-chars`, not by editing the sources.

The app re-selects the Subset scene as the program scene on each start, and
if OBS restarts mid-run, reconnects and re-asserts everything by itself.

## 5. Send the picture to the TV

Right-click the preview → **Fullscreen Projector (Preview)** → choose the TV
display. That's the output surface — no streaming, no recording.

- If the TV isn't listed, macOS isn't detecting it; see
  [hardware.md](hardware.md#wiring).
- Move the mouse pointer back to the laptop display so it doesn't sit over
  the movie.
- The projector stays until closed (Esc while it's focused). Reopen it after
  reconnecting the TV.

## 6. Sanity checks

- OBS's mixer should show the `Subset Audio` meter moving while content
  plays.
- If the TV has picture but no sound: re-check the Monitoring Device
  setting, and confirm OBS has microphone permission (System Settings →
  Privacy & Security → Microphone).
- If captions render but video is black while the source plays, see the
  HDCP section in [hardware.md](hardware.md#about-hdcp).
