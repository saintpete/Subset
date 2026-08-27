"""Roll-up caption state, in the style of live-TV captions.

Finalized text accumulates; the current interim hypothesis is appended
after it and may be rewritten wholesale until the model finalizes it.
Rendering shows the last `max_lines` wrapped lines.
"""

import time

# Finalized text older than this can never scroll back into view; cap the
# buffer so hours of playback don't grow memory or slow wrapping.
_TAIL_CHARS = 600


class RollUpCaptions:
    def __init__(self, max_lines: int = 2, max_chars: int = 46) -> None:
        self.max_lines = max_lines
        self.max_chars = max_chars
        self._finals = ""
        self._interim = ""
        self.version = 0
        self.last_activity = time.monotonic()

    def _bump(self) -> None:
        self.version += 1
        self.last_activity = time.monotonic()

    def on_final(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self._finals = (self._finals + " " + text).strip()
        if len(self._finals) > _TAIL_CHARS:
            cut = self._finals[-_TAIL_CHARS:]
            self._finals = cut[cut.index(" ") + 1 :] if " " in cut else cut
        self._interim = ""
        self._bump()

    def on_interim(self, text: str) -> None:
        text = " ".join(text.split())
        if text == self._interim:
            return
        self._interim = text
        self._bump()

    def promote_interim(self) -> None:
        """Fold the pending interim into the finalized tail.

        Called when a live session ends (rotation or error) so speculative
        text already on screen survives the swap instead of vanishing.
        """
        if self._interim:
            self.on_final(self._interim)

    def clear(self) -> None:
        if self._finals or self._interim:
            self._finals = ""
            self._interim = ""
            self.version += 1  # deliberate: clearing is not "activity"

    @property
    def empty(self) -> bool:
        return not (self._finals or self._interim)

    def render(self) -> str:
        full = (self._finals + " " + self._interim).strip()
        if not full:
            return ""
        lines: list[str] = []
        current = ""
        for word in full.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) <= self.max_chars or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return "\n".join(lines[-self.max_lines :])
