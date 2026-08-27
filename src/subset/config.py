"""Configuration, .env loading, and secret redaction."""

import os
import re
from pathlib import Path

DEFAULT_MODEL = "gemini-3.5-transcribe-live"
DEFAULT_KEY_FILE = "gemini-key.txt"
DEFAULT_VIDEO_DEVICE = "USB3.0 Video"
DEFAULT_AUDIO_DEVICE = "USB3.0 Audio"
DEFAULT_OBS_URL = "ws://127.0.0.1:4455"

# Live sessions cap at 10 minutes; rotate early, at a silence if possible.
ROTATE_AFTER_S = 8.5 * 60
HARD_ROTATE_S = 9.5 * 60

_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value:
        _SECRETS.add(value)


def redact(obj: object) -> str:
    """Render any object as a string with known secrets masked."""
    text = str(obj)
    for secret in _SECRETS:
        text = text.replace(secret, "<redacted>")
    return text


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader; real env vars win over file values."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# A bare key token: no whitespace, header-safe. AI Studio keys are long
# base64url-ish strings, sometimes dotted (e.g. "AQ.…").
_KEY_TOKEN = re.compile(r"^[A-Za-z0-9._\-]{20,}$")
_LABELED_KEY = re.compile(r"(?i)^api\s*key\s*[:=]\s*(\S+)$")


def load_api_key(path: str) -> str:
    """Extract the key from the file, tolerating a pasted AI Studio export.

    The file may be the bare key, or the full "Name / API Key / Project"
    blob AI Studio offers to copy. Every line is registered for redaction;
    only a validated single token is ever sent anywhere. A malformed value
    must never reach an HTTP header — that's how secrets end up echoed in
    library tracebacks.
    """
    p = Path(path)
    if not p.is_file():
        raise SystemExit(
            f"API key file not found: {p.resolve()}\n"
            "Put your Gemini API key (from aistudio.google.com) in that file. "
            "It is git-ignored and its contents are never printed."
        )
    labeled: list[str] = []
    bare: list[str] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        register_secret(line)
        match = _LABELED_KEY.match(line)
        if match and _KEY_TOKEN.fullmatch(match.group(1)):
            labeled.append(match.group(1))
        elif _KEY_TOKEN.fullmatch(line):
            bare.append(line)
    key = (labeled or bare or [None])[0]
    if key is None:
        raise SystemExit(
            f"Could not find an API key token in {p.resolve()} "
            "(contents not shown). Expected either the bare key on its own "
            'line or an "API Key: …" line from the AI Studio export.'
        )
    register_secret(key)
    return key
