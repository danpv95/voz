"""Terminal UI for live dictation: a redrawable region on stderr + single-key input.

No dependencies beyond the stdlib (ANSI escapes + termios). stdout is never touched
here - the final transcript is printed by the caller, once, so pipes stay clean.
Honors NO_COLOR.
"""
from __future__ import annotations

import math
import os
import re
import unicodedata
import select
import shutil
import sys
import termios
import textwrap
import tty

_COLOR = sys.stderr.isatty() and not os.environ.get("NO_COLOR")

DIM = "2"
RED = "38;5;203"
ACCENT = "38;5;80"
GREEN = "38;5;114"
AMBER = "38;5;179"
# Neutral on light and dark backgrounds: the main text keeps the terminal's own
# foreground (a fixed white vanishes on light themes) and secondary text is "dim".
GRAY = DIM
TEXT = "39"

BARS = "▁▂▃▄▅▆▇█"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def paint(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def meter(levels, voiced=None, n: int = 10) -> str:
    """Strip of the last `n` frames. With `voiced` (one bool per frame, as filled by the
    Segmenter) a silent frame is a dim `·` and a voiced one a block bar by level (RMS on a
    -60..-10 dBFS scale) in the accent colour: the shape, not the colour, tells them apart
    (NO_COLOR). Without `voiced`: plain bars for every frame, uncoloured."""
    vals = list(levels)[-n:]
    vals = [0.0] * (n - len(vals)) + vals

    def bar(v: float) -> str:
        db = 20 * math.log10(max(v, 1e-6))
        return BARS[int(round((min(max(db, -60), -10) + 60) / 50 * (len(BARS) - 1)))]

    if voiced is None:
        return "".join(bar(v) for v in vals)
    flags = list(voiced)[-n:]
    flags = [False] * (n - len(flags)) + flags    # aligned to the newest frame
    return "".join(paint(ACCENT, bar(v)) if f else paint(GRAY, "·") for v, f in zip(vals, flags))


def width() -> int:
    return max(20, shutil.get_terminal_size((80, 24)).columns)


def wrap_transcript(committed: str, partial: str, cols: int, max_lines: int) -> list[str]:
    """Wrap committed (normal) + partial (dim) text; keep only the last max_lines."""
    committed, partial = committed.strip(), partial.strip()
    # Only the tail is ever shown: wrap a bounded window so a 2-hour session costs the
    # same per frame as a 2-minute one (wrapping everything grew ~16 ms/frame per hour).
    budget = max(200, cols * (max_lines + 2) * 2)
    if len(committed) > budget:
        tail = committed[-budget:]
        committed = "… " + tail[tail.find(" ") + 1:] if " " in tail else "… " + tail
    if partial and not _COLOR:      # without colours, mark the provisional text explicitly
        partial = f"‹{partial}›"
    full = (committed + (" " if committed and partial else "") + partial).strip()
    if not full:
        return []
    lines = textwrap.wrap(full, max(10, cols - 4))
    # colour by character offset: textwrap replaced each break with one space
    out, pos, split = [], 0, len(committed)
    for ln in lines:
        found = full.find(ln, pos)
        start = found if found >= 0 else pos
        end = start + len(ln)
        if end <= split:
            seg = paint(TEXT, ln)
        elif start >= split:
            seg = paint(GRAY, ln)
        else:
            cut = split - start
            seg = paint(TEXT, ln[:cut]) + paint(GRAY, ln[cut:])
        out.append("  " + seg)
        pos = end
    return out[-max_lines:]


class Region:
    """A block of lines on stderr that can be redrawn in place."""

    def __init__(self, stream=None, max_text_lines: int = 6):
        self.stream = stream or sys.stderr
        self.max_text_lines = max_text_lines
        self._drawn = 0  # lines currently on screen (excluding the cursor line)
        self._cols = None

    def _rewind(self) -> str:
        up = f"\033[{self._drawn}A" if self._drawn else ""
        return "\r" + up + "\033[J"

    def draw(self, lines: list[str]) -> None:
        w = width()
        if self._cols is not None and w != self._cols and self._drawn:
            # resized: the terminal re-wrapped what we drew, so cursor-up would land in the
            # wrong row. Start a fresh region below instead of corrupting the screen.
            self.stream.write("\n")
            self._drawn = 0
        self._cols = w
        cols = w - 1
        lines = [fit(ln, cols) for ln in lines]
        self.stream.write(self._rewind() + "\n".join(lines))
        self.stream.flush()
        self._drawn = max(0, len(lines) - 1)

    def clear(self) -> None:
        self.stream.write(self._rewind())
        self.stream.flush()
        self._drawn = 0


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _cell(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_len(s: str) -> int:
    return sum(_cell(c) for c in _ANSI.sub("", s))


def fit(line: str, cols: int) -> str:
    """Cut `line` to `cols` visible cells (ANSI codes don't count) so the terminal
    never wraps it - a wrapped status line breaks Region's cursor arithmetic."""
    if visible_len(line) <= cols:
        return line
    out, used, i = [], 0, 0
    while i < len(line):
        m = _ANSI.match(line, i)
        if m:
            out.append(m.group(0)); i = m.end(); continue
        w = _cell(line[i])
        if used + w > cols - 1:
            break
        out.append(line[i]); used += w; i += 1
    return "".join(out) + "…" + ("\033[0m" if _COLOR else "")


def status_line(icon: str, label: str, detail: str = "", hint: str = "") -> str:
    parts = [icon, label]
    if detail:
        parts.append(detail)
    line = "  ".join(parts)
    if hint:
        line += "   " + paint(GRAY, hint)
    return line


class Keys:
    """cbreak-mode key reader (Ctrl-C still raises KeyboardInterrupt).

    Reads into a byte buffer and returns ONE logical key per call:
    - bracketed paste (\x1b[200~ … \x1b[201~) is swallowed whole - pasting text into
      the voz terminal must not act as r/c/↵ keystrokes;
    - arrow keys / other CSI sequences are dropped, but a bare Esc followed by a
      real key returns Esc and keeps that key for the next call;
    - multi-byte UTF-8 (ñ, á) comes back as one character.
    """

    PASTE_ON, PASTE_OFF = b"\x1b[200~", b"\x1b[201~"

    def __init__(self, stream=None):
        self.stream = stream or sys.stdin
        self.fd = self.stream.fileno()
        self._old = None
        self._buf = bytearray()
        self._pasting = False

    def __enter__(self):
        self._old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        sys.stderr.write("\033[?25l\033[?2004h")  # hide cursor · bracketed paste on
        sys.stderr.flush()
        return self

    def __exit__(self, *exc):
        if self._old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)
        sys.stderr.write("\033[?2004l\033[?25h")  # bracketed paste off · show cursor
        sys.stderr.flush()
        return False

    def _fill(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if ready:
            self._buf += os.read(self.fd, 256)
        return bool(ready)

    def flush_input(self) -> None:
        """Drop keys typed while voz was busy (they'd act in the wrong state)."""
        try:
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except termios.error:
            pass
        self._buf.clear()
        self._pasting = False

    def read(self, timeout: float) -> str | None:
        # mid-paste the buffer may hold only a partial end marker: it needs more bytes,
        # re-parsing the same leftover would never finish the paste
        if (not self._buf or self._pasting) and not self._fill(timeout) and not self._buf:
            return None
        return self._take()

    def _take(self) -> str | None:
        b = self._buf
        if self._pasting:
            i = b.find(self.PASTE_OFF)
            if i < 0:
                # keep a possible partial end marker: losing it would swallow every key after
                del b[: max(0, len(b) - (len(self.PASTE_OFF) - 1))]
                return None
            del b[: i + len(self.PASTE_OFF)]
            self._pasting = False
            return self._take() if b else None
        if b.startswith(self.PASTE_ON):
            del b[: len(self.PASTE_ON)]
            self._pasting = True
            return self._take() if b else None
        if b[0] == 0x1B:
            if len(b) == 1:
                self._fill(0.01)
            if len(b) == 1 or b[1] not in (0x5B, 0x4F):   # not '[' / 'O' → a real Esc
                del b[0]
                return "\x1b"
            # CSI / SS3 sequence (arrows, F-keys…): drop up to its final byte
            for _ in range(2):
                for j in range(2, len(b)):
                    if 0x40 <= b[j] <= 0x7E:
                        del b[: j + 1]
                        return None
                if self.PASTE_ON.startswith(bytes(b)):     # paste marker split across reads
                    self._fill(0.01)
                    if b.startswith(self.PASTE_ON):
                        return self._take()
                else:
                    self._fill(0.01)
            b.clear()
            return None
        n = 1 if b[0] < 0x80 else 2 if b[0] >> 5 == 0b110 else 3 if b[0] >> 4 == 0b1110 else 4
        if len(b) < n:
            self._fill(0.01)
        ch = bytes(b[:n]).decode("utf-8", errors="ignore")
        del b[:n]
        return ch or None
