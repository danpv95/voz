"""voz v2 interaction as a pure state machine.

`step(flow, key)` returns the action the shell (liveui.py) must perform; it never
touches the microphone, the model or the terminal, so every transition is unit
tested. The shell reports the result of finishing a recording with `finished()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class St(Enum):
    READY = "listo"
    LISTEN = "escuchando"
    PAUSED = "pausado"
    FINISHING = "terminando"
    REVIEW = "revision"
    NOVOICE = "sin_voz"
    CHOOSE = "elegir"
    DONE = "fin"


class Act(Enum):
    NOOP = "noop"
    START = "start"            # open a new recording (mic opens on the first one)
    CONTINUE = "continue"      # reopen the current recording
    PAUSE = "pause"
    RESUME = "resume"
    FINISH = "finish"          # close the chunk in progress, wait for its text
    DISCARD = "discard"
    UNDO = "undo"
    COPY = "copy"              # copy the current recording (copy mode != auto)
    EXIT = "exit"              # leave; `Step.copy_all` says what to copy


@dataclass
class Take:
    n: int
    text: str = ""
    seconds: float = 0.0
    discarded: bool = False
    novoice: bool = False
    nomic: bool = False        # no audio arrived at all (microphone failed to open, or digital silence)

    @property
    def valid(self) -> bool:
        return bool(self.text.strip()) and not self.discarded


@dataclass
class Flow:
    state: St = St.READY
    takes: list[Take] = field(default_factory=list)
    discard_on_finish: bool = False   # esc while recording: finish, then mark discarded

    @property
    def current(self) -> Take | None:
        return self.takes[-1] if self.takes else None

    def valid_takes(self) -> list[Take]:
        return [t for t in self.takes if t.valid]


@dataclass
class Step:
    action: Act
    copy_all: bool = False         # EXIT: copy every valid take joined
    keep_clipboard: bool = False   # EXIT: leave the clipboard as it is
    copy_last: bool = False        # EXIT: explicitly copy the last valid take


ENTER = "enter"
SPACE = "space"   # acts as ENTER only where the spec says so (LISTO, ESCUCHANDO)
ESC = "esc"
BACKSPACE = "backspace"


def norm(raw: str | None) -> str | None:
    """Raw key from ui.Keys → logical key. Case-insensitive letters."""
    if raw is None:
        return None
    if raw in ("\r", "\n"):
        return ENTER
    if raw == " ":
        return SPACE
    if raw == "\x1b":
        return ESC
    if raw in ("\x7f", "\x08"):
        return BACKSPACE
    return raw.lower()


def _leave(flow: Flow) -> Step:
    if len(flow.valid_takes()) >= 2:
        flow.state = St.CHOOSE
        return Step(Act.NOOP)
    flow.state = St.DONE
    return Step(Act.EXIT)


def step(flow: Flow, key: str | None) -> Step:
    """Apply one logical key. Unknown keys are ignored (NOOP)."""
    s = flow.state
    if key is None:
        return Step(Act.NOOP)
    if key == SPACE:
        if s not in (St.READY, St.LISTEN):
            return Step(Act.NOOP)
        key = ENTER

    if s is St.READY:
        if key == ENTER:
            flow.takes.append(Take(n=len(flow.takes) + 1))
            flow.state = St.LISTEN
            return Step(Act.START)
        if key == ESC:
            flow.state = St.DONE
            return Step(Act.EXIT, keep_clipboard=True)

    elif s in (St.LISTEN, St.PAUSED):
        if s is St.LISTEN and key == "p":
            flow.state = St.PAUSED
            return Step(Act.PAUSE)
        if s is St.PAUSED and key == "p":
            flow.state = St.LISTEN
            return Step(Act.RESUME)
        if key in (ENTER, ESC):
            flow.discard_on_finish = key == ESC
            flow.state = St.FINISHING
            return Step(Act.FINISH)

    elif s is St.REVIEW:
        cur = flow.current
        if key == "r":
            flow.takes.append(Take(n=len(flow.takes) + 1))
            flow.state = St.LISTEN
            return Step(Act.START)
        if key == "c" and cur and not cur.discarded:
            flow.state = St.LISTEN
            return Step(Act.CONTINUE)
        if key == BACKSPACE and cur and not cur.discarded:
            cur.discarded = True
            return Step(Act.DISCARD)
        if key == "u" and cur and cur.discarded and not cur.novoice:
            cur.discarded = False
            return Step(Act.UNDO)
        if key == "s" and cur and cur.valid:
            return Step(Act.COPY)
        if key in (ENTER, ESC):
            return _leave(flow)

    elif s is St.NOVOICE:
        cur = flow.current
        if key == "r" or (key == ENTER and cur and cur.nomic):   # ↵ is "reintentar" when the mic is the problem
            flow.takes.append(Take(n=len(flow.takes) + 1))
            flow.state = St.LISTEN
            return Step(Act.START)
        if key in (ENTER, ESC):
            return _leave(flow)

    elif s is St.CHOOSE:
        if key == ENTER:
            flow.state = St.DONE
            return Step(Act.EXIT, copy_last=True)  # the hint promises "dejar copiada la última"
        if key == "a":
            flow.state = St.DONE
            return Step(Act.EXIT, copy_all=True)
        if key == ESC:
            flow.state = St.DONE
            return Step(Act.EXIT, keep_clipboard=True)

    return Step(Act.NOOP)


def finished(flow: Flow, text: str, seconds: float) -> None:
    """The shell finished a recording (FINISHING → REVIEW / NOVOICE)."""
    cur = flow.current
    cur.text, cur.seconds = text.strip(), seconds
    cur.novoice = not cur.text
    if flow.discard_on_finish and cur.text:
        cur.discarded = True
    flow.discard_on_finish = False
    flow.state = St.NOVOICE if cur.novoice else St.REVIEW


def mic_failed(flow: Flow) -> None:
    """The microphone could not be opened for the current recording (NOVOICE, nomic)."""
    cur = flow.current
    cur.novoice = cur.nomic = True
    flow.discard_on_finish = False
    flow.state = St.NOVOICE


def joined(takes: list[Take]) -> str:
    return "\n\n".join(t.text for t in takes if t.valid)
