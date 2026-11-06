"""Interactive dictation v2 (TTY only) - the shell around flow.py.

flow.step() decides; this module performs: microphone, model, disk, clipboard and
the redrawable status region. Spec: docs/guia-voz.html (section 3).

    voz  ·  turbo  ·  auto  ·  🎙 MacBook Pro Microphone
    ●  Escuchando  00:42  ▂▃▅▇▅▃  es   ↵ terminar · p pausa · esc descartar
      texto confirmado… texto parcial en gris

Key design points:
- The microphone opens ONCE (first ↵) and closes only on exit: stopping it is what
  can deadlock CoreAudio (record.stop_stream). A gate drops audio while paused or
  in review - nothing is transcribed or written to disk then.
- One Segmenter + LiveSession + autosave.Session per recording; "continue" reuses
  them (LiveSession.drain() waits for the text without killing its worker).
- Each finished recording is printed to the terminal history (stderr) and copied
  automatically (config: copiar = auto | preguntar | nunca). stdout receives the
  chosen text only when it is not the terminal (pipes / redirection).
"""
from __future__ import annotations

import os
import shutil
import sys
import textwrap
import threading
import unicodedata
import time
from pathlib import Path

import numpy as np

import autosave
import config
import flow as F
import stt
import ui
import live
import record
from live import LiveSession, Segmenter
from live import untranscribed as _untranscribed
from record import SAMPLE_RATE

FRAME_S = 0.08  # redraw / key-poll period
FINISH_TIMEOUT_S = live.FINISH_TIMEOUT_S  # max wait for pending chunks when finishing
CLOSE_JOIN_S = 2.0  # worker join when a recording is closed after its chunks were drained


def _model_label(model: str) -> str:
    if model == stt.DEFAULT_MODEL:
        return "turbo"
    if model == stt.ACCURATE_MODEL:
        return "large-v3"
    name = model.rsplit("/", 1)[-1]
    return name.replace("whisper-", "").replace("-mlx", "")


MIC_MAX = 28  # characters of the device name shown in the header


def _mic_chip() -> str:
    """'🎙 <default input device>' (name cut to MIC_MAX), or '' when it can't be queried.
    🎙 is neutral width for unicodedata and wcwidth (1 cell), the same count ui.fit uses."""
    try:
        import sounddevice as sd

        name = str(sd.query_devices(kind="input")["name"]).strip()
    except Exception:  # noqa: BLE001  (no device, PortAudio missing, odd payload)
        return ""
    if not name:
        return ""
    if len(name) > MIC_MAX:
        name = name[:MIC_MAX - 1].rstrip() + "…"
    return "🎙 " + name


def header_line(model: str, lang: str, mic: str = "") -> str:
    """'voz  ·  turbo  ·  auto  ·  🎙 MacBook Pro Microphone', cut to the terminal width."""
    parts = [_model_label(model), lang] + ([mic] if mic else [])
    line = ui.paint(ui.ACCENT + ";1", "voz") + ui.paint(ui.GRAY, "  ·  " + "  ·  ".join(parts))
    return ui.fit(line, ui.width() - 1)


WELCOME_FILE = ".welcomed"
WELCOME_TEXT = "habla con pausas cortas: voz transcribe mientras hablas · voz --help para más"


def _first_run() -> bool:
    """True the first time on this machine (no marker in the state dir). The marker is
    created atomically (O_EXCL, 0600), so of two voz starting together only one shows
    the tip. If it can't be created the tip is still shown, never an error."""
    marker = autosave.STATE_DIR / WELCOME_FILE
    try:
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    except OSError:
        pass
    return True


def _dur(seconds: float) -> str:
    secs = int(round(seconds))
    return f"{secs} s" if secs < 60 else f"{secs // 60}:{secs % 60:02d} min"


MIN_LANG_SAMPLES = 2 * 16000  # language detections on shorter audio don't count in the summary
NARROW = 80  # below this many columns, hints and summary switch to their compact form


def _hint(full: list[str], short: list[str]) -> list[str]:
    """Key hints as dim lines: `full` items (3 spaces apart) from NARROW columns up, `short`
    items (" · ") below. Items that do not fit the width move to the next line: a key is
    never cut off."""
    wide = ui.width() >= NARROW
    items, sep = (full, "   ") if wide else (short, " · ")
    room = ui.width() - 1
    lines: list[str] = []
    for item in items:
        if lines and len(lines[-1]) + len(sep) + len(item) <= room:
            lines[-1] += sep + item
        else:
            lines.append(item)
    return [ui.paint(ui.GRAY, ln) for ln in lines]


def _wrapped(text: str, indent: str = "   ") -> list[str]:
    """`text` as lines that fit the terminal (never cut), continuation lines indented."""
    return textwrap.wrap(text, ui.width() - 1, subsequent_indent=indent,
                         break_long_words=False, break_on_hyphens=False) or [""]


def _status(icon: str, label: str, options: list[tuple[str, str]], poorer: tuple[str, ...] = (),
            sep: str = "   ") -> list[str]:
    """Status line(s). `options` are (detail, hint) pairs from the most to the least explicit.
    Preferred: ONE line with the first pair that fits. Otherwise TWO: the status first (the
    richest detail that fits, trying `poorer` ones last: the meter and extras are what gets
    dropped) and the keys on a line of their own (the longest hint that fits the width).
    The keys are never trimmed away."""
    room = ui.width() - 1
    for detail, hint in options:
        line = ui.status_line(icon, label, detail) + sep + ui.paint(ui.GRAY, hint)
        if ui.visible_len(line) <= room:
            return [line]
    details = list(dict.fromkeys([d for d, _ in options] + list(poorer)))
    hints = list(dict.fromkeys(h for _, h in options))
    heads = [ui.status_line(icon, label, d) for d in details]
    head = next((h for h in heads if ui.visible_len(h) <= room), heads[-1])
    key_line = next((h for h in hints if len(h) <= room), hints[-1])
    return [head, ui.paint(ui.GRAY, key_line)]


def _short_path(path) -> str:
    """The path with the home folder as ~ (voz expands it, so it can be pasted after `voz`)."""
    p = str(path)
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home + os.sep) else p


def _first_line(err: BaseException) -> str:
    lines = [ln.strip() for ln in str(err).splitlines() if ln.strip()]
    return lines[0] if lines else type(err).__name__


def gated_text(count: int, diag: bool = False) -> str:
    """'3 tramos omitidos por nivel bajo' (singular for 1, '' for 0); '(ver diag.log)' when VOZ_DIAG is on.
    The level gate cannot tell silence from quiet speech: the text never claims they were silence."""
    if count <= 0:
        return ""
    text = "1 tramo omitido por nivel bajo" if count == 1 else f"{count} tramos omitidos por nivel bajo"
    return text + (" (ver diag.log)" if diag else "")


def summary_line(copied, seconds: float, text: str, langs: list[str], lang_arg: str,
                 compact: bool | None = None, untranscribed: str = "", gated: str = "",
                 replaced: bool = True) -> str:
    """e.g. '✓  Copiado (reemplazó tu portapapeles)  ·  42 s  ·  31 palabras  ·  es'.
    Compact (narrow terminals) drops the parenthesis so duration and words survive, and so does
    `replaced=False` (the parenthesis belongs to the copy event, not to every redraw).
    `untranscribed` ("2 tramos sin transcribir") turns it into the incomplete-text warning:
    '⚠  Texto incompleto  ·  2 tramos sin transcribir  ·  copiado'.
    `gated` (see gated_text) is one more dim part at the end."""
    if compact is None:
        compact = ui.width() < NARROW
    if untranscribed:
        tail = {True: ["copiado"], False: ["no se pudo copiar"]}.get(copied, [])
        return (ui.paint(ui.AMBER, "⚠") + "  Texto incompleto"
                + ui.paint(ui.GRAY, "  ·  " + "  ·  ".join([untranscribed, *tail])))
    if copied is True:
        head = f"{ui.paint(ui.GREEN, '✓')}  Copiado" + (
            "" if compact or not replaced else " " + ui.paint(ui.GRAY, "(reemplazó tu portapapeles)"))
    elif copied is False:
        head = f"{ui.paint(ui.AMBER, '⚠')}  No se pudo copiar (pbcopy no disponible)"
    else:
        head = f"{ui.paint(ui.GRAY, '·')}  Sin copiar"
    used = sorted(set(langs), key=langs.index) if lang_arg == "auto" else [lang_arg]
    parts = [_dur(seconds), f"{len(text.split())} palabras"]
    if used:
        parts.append("+".join(used))
    if gated:
        parts.append(gated)
    return head + ui.paint(ui.GRAY, "  ·  " + "  ·  ".join(parts))


def finishing_label(seconds: float, pending: int) -> str:
    """'Terminando… 3 s · 2 tramos pendientes' (no tail when nothing is pending)."""
    label = f"Terminando… {int(max(0, seconds))} s"
    if pending > 0:
        label += f" · {pending} tramo{'' if pending == 1 else 's'} pendiente{'' if pending == 1 else 's'}"
    return label


MIN_HEAD = 12  # cells kept for the first words of a recording in the history view
HISTORY_ROWS = 9  # entries visible at once (the digits 1-9 jump to them; arrows scroll further)


def _history_fit(text: str, cols: int) -> str:
    """Truncate at a character cluster boundary, including joined emoji and accents."""
    if ui.visible_len(text) <= cols:
        return text
    clusters = []
    for ch in text:
        code = ord(ch)
        extend = (unicodedata.combining(ch) or ch == "\u200d" or 0xFE00 <= code <= 0xFE0F
                  or 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F)
        regional_pair = (0x1F1E6 <= code <= 0x1F1FF and clusters
                         and len(clusters[-1]) == 1 and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF)
        if clusters and (extend or clusters[-1].endswith("\u200d") or regional_pair):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    out, used = [], 0
    for cluster in clusters:
        width = ui.visible_len(cluster)
        if used + width > cols - 1:
            break
        out.append(cluster)
        used += width
    return "".join(out) + "…"


DISCARDED_TAG = "descartada"


def history_lines(items, sel: int, rows: int = HISTORY_ROWS) -> list[str]:
    """The history view. `items` are (autosave.Recording, seconds | None) in `voz copiar` order
    (valid first, discarded last and marked); `sel` is the highlighted one (0-based). Every line
    is cut to the terminal width."""
    n = len(items)
    rows = max(1, min(rows, n))
    top = max(0, min(sel - rows // 2, n - rows))
    out = [ui.paint(ui.TEXT + ";1", "Historial") + ui.paint(
        ui.GRAY, ("  ·  grabaciones guardadas" if ui.width() >= NARROW else "") + f"  ·  {sel + 1} de {n}")]
    room = ui.width() - 1 - 2                                  # minus the selection mark
    for i in range(top, top + rows):
        rec, secs = items[i]
        gone = bool(getattr(rec, "discarded", False))
        words, when, dur = len(rec.text.split()), autosave.when(rec.run), f"{autosave.clock(secs):>5}"
        num, tag = f"{i + 1:>2}", ([DISCARDED_TAG] if gone else [])
        # from the most to the least informative: the first words always keep at least MIN_HEAD
        # cells, so the date goes before they do (down to the bare number)
        tiers = [[num, when, dur, f"{words:>5} palabras", *tag],
                 [num, when[5:], dur, f"{words:>4} pal.", *tag],        # MM-DD HH:MM
                 [num, when[5:], dur, *tag],
                 [num, when[5:], *tag],
                 [num, *tag]]
        prefix = next(("  ".join(t) for t in tiers if len("  ".join(t)) + 2 + MIN_HEAD <= room), "  ".join(tiers[-1]))
        line = _history_fit(f"{prefix}  «{autosave.head(rec.text)}»", room)
        if i == sel:
            out.append(ui.paint(ui.ACCENT, "▸ ") + ui.paint((ui.GRAY if gone else ui.TEXT) + ";1", line))
        else:
            out.append("  " + (ui.paint(ui.GRAY, line) if gone else line))
    return out + _history_hints()


def _history_hints() -> list[str]:
    return _hint(["↑↓ elegir", "1-9 saltar", "↵ copiar", "esc/h volver"],
                 ["↑↓ elegir", "1-9 saltar", "↵ copiar", "esc/h volver"])


class ArrowKeys(ui.Keys):
    """ui.Keys that also reports the up/down arrows (ESC [ A/B, or ESC O A/B) as flow.UP/DOWN."""

    ARROWS = {b"\x1b[A": F.UP, b"\x1b[B": F.DOWN, b"\x1bOA": F.UP, b"\x1bOB": F.DOWN}

    def _take(self):
        b = self._buf
        if not self._pasting and b[:1] == b"\x1b":
            if len(b) < 3:
                self._fill(0.01)          # an arrow is written in one go: give the rest a moment
            key = self.ARROWS.get(bytes(b[:3]))
            if key:
                del b[:3]
                return key
        return super()._take()


class Audio:
    """The single audio source of a run (mic or --sim) plus the per-recording sinks."""

    def __init__(self, sim: str | None):
        self.sim = sim
        self.gate = False          # True only while LISTEN
        self.take: int | None = None   # recording number the current engine belongs to
        self.seg: Segmenter | None = None
        self.session: LiveSession | None = None
        self.rec: autosave.Session | None = None
        self.overflows = 0
        self._stop = None
        # Held by the audio callback for the whole feed and by Shell._stop_listening for
        # gate-off + flush: a block already past the gate can't submit its chunk after
        # the recording was drained (it lost the last phrase). Held only for milliseconds.
        self.lock = threading.Lock()

    def feed(self, block: np.ndarray, overflow: bool = False) -> None:
        if overflow:
            self.overflows += 1
        with self.lock:
            if not self.gate:
                return
            seg, session, rec = self.seg, self.session, self.rec
            if seg is None or session is None or rec is None:
                return
            rec.write_audio(block)
            for chunk in seg.feed(block):
                session.submit(chunk)

    def open(self, refresh: bool = False) -> None:
        """Start the source. For a real mic, record.MicError if it cannot be opened;
        `refresh` makes the retry look the default input device up again."""
        if self._stop is not None:
            return
        if self.sim:
            self._stop = self._open_sim()
        else:
            from record import open_input, stop_stream

            def callback(indata, frames, time_info, status):
                self.feed(indata[:, 0].copy(), bool(status and status.input_overflow))

            stream = open_input(callback, refresh=refresh)
            self._stop = lambda: stop_stream(stream)  # never hangs (CoreAudio deadlock)

    def _open_sim(self):
        from mlx_whisper.audio import load_audio

        audio = np.asarray(load_audio(self.sim), dtype="float32")
        stop_flag = threading.Event()
        block = 1024

        def run():
            i = 0
            while not stop_flag.is_set():
                b = audio[i:i + block] if i < len(audio) else np.zeros(block, dtype="float32")
                self.feed(b)
                if self.gate:                 # like a real mic: the file advances only while open
                    i += block
                time.sleep(block / SAMPLE_RATE)

        t = threading.Thread(target=run, daemon=True)
        t.start()

        def stop():
            stop_flag.set()
            t.join()

        return stop

    def close(self) -> None:
        self.gate = False
        stop, self._stop = self._stop, None    # cleared first: a re-entrant close is a no-op
        if stop is not None:
            stop()


class Shell:
    def __init__(self, args, rules, transcribe_fn, apply_fn, warm: bool, silence_rms: float):
        self.args, self.rules = args, rules
        self.apply_fn, self.silence_rms = apply_fn, silence_rms
        self.cfg = config.load()
        self.flow = F.Flow()
        self.audio = Audio(getattr(args, "sim", None))
        self.region = ui.Region()
        self.run_id = autosave.new_run_id()
        self.langs: list[str] = []       # languages detected in the current recording
        self.listen_t0 = 0.0
        self.seconds = 0.0               # recorded (not paused) time of the current recording
        self.printed = 0                 # chars of the current recording already printed
        self.copied: bool | None = None
        self.notice = ""                 # one-line feedback in REVIEW (undo)
        self.early_enter = False         # ↵ pressed in READY while the model was still loading
        self.i = 0                       # spinner frame
        self.welcome: bool | None = None  # first-run tip: decided once, on the first "Listo"
        self.keys = None
        self.force_exit = False          # second Ctrl-C while finishing: stop waiting
        self.incomplete = False          # current recording closed with chunks untranscribed
        self.lost = 0                    # chunks of the current recording that will not be transcribed
        self.lost_carry = 0              # of those, the ones lost by an engine that was replaced (see _renew_abandoned)
        self.retry_mic = False           # the last recording got no audio: re-query the device on retry
        # --no-copy wins over the config: nothing is ever copied, `s` is not offered
        self.copy_mode = "nunca" if getattr(args, "no_copy", False) else self.cfg["copy"]
        self.clip_before: str | None = None   # what the clipboard held before voz's current run of writes
        self.clip_owner: int | str | None = None  # take n (or "all"/"history"/"external") now in the clipboard
        self.clip_text: str | None = None     # the last text voz itself wrote there
        self.clip_hist_take: int | None = None  # history copy of THIS run's recording n (retired if n is discarded)
        self.copy_note = False           # the summary still carries the copy event's "(reemplazó…)"
        self.history: list = []          # (Recording, seconds) rows of the history view

        def tracked(audio, **kw):
            out = transcribe_fn(audio, **kw)
            # detection on <2 s of audio (early partials) is unreliable: don't let it
            # put a stray "en" in the summary
            if (out and kw.get("language") == "auto" and stt.last_language
                    and len(audio) >= MIN_LANG_SAMPLES):
                self.langs.append(stt.last_language)
            return out

        self.transcribe = tracked
        self.load_t0 = time.monotonic()  # when the first model load began (READY counter)
        # the first engine warms the model while the user reads "Listo"
        self.next_session = self._new_session(warm=warm)

    # ---------- engine per recording ----------
    def _new_session(self, warm: bool) -> LiveSession:
        seg = Segmenter()
        s = LiveSession(self.transcribe, self.rules, self.apply_fn, self.args.model,
                        self.args.lang, warm=warm, echo=False, partial_source=seg.current,
                        partial_lang=self._partial_lang, warn=False)  # stderr would break the region
        s._seg = seg  # travels with its session
        return s

    def _partial_lang(self) -> str:
        if self.args.lang != "auto":
            return self.args.lang
        return self.langs[-1] if self.langs else "es"

    def _start_recording(self) -> None:
        a = self.audio
        a.gate = False
        if a.session is not None:                 # "r": close the previous recording
            a.session.finish(timeout=CLOSE_JOIN_S)
            a.rec.close(clean=self._rec_clean())
            self.incomplete, self.lost, self.lost_carry = False, 0, 0
        session = self.next_session or self._new_session(warm=False)
        self.next_session = None
        rec = autosave.Session(run_id=self.run_id, take=self.flow.current.n)
        self.run_id = rec.run_id              # follows a collision suffix so the next takes stay together
        session._on_commit = rec.commit
        a.seg, a.session, a.rec = session._seg, session, rec
        a.take = self.flow.current.n          # the engine now belongs to this recording
        self.langs, self.seconds, self.printed, self.copied, self.notice = [], 0.0, 0, None, ""
        self._wait_ready()
        if session.error is None:
            self._open_mic()

    def _renew_abandoned(self) -> None:
        """`c` after closing gave up on a stuck transcriber: the old worker may still be inside
        the model and its late results are dropped, so the recording goes on with a NEW engine.
        What was already transcribed (and the chunks that were not) stays part of this recording."""
        a = self.audio
        old = a.session
        if old is None or not old.abandoned:
            return
        new = self._new_session(warm=False)
        new.parts = list(old.parts)
        new._on_commit = a.rec.commit
        a.seg, a.session = new._seg, new
        self.lost_carry = self.lost
        try:
            old.finish(timeout=0)      # stop its worker as soon as the stuck call returns
        except Exception:  # noqa: BLE001
            pass

    def _open_mic(self) -> None:
        """Open the microphone and start listening. If it cannot be opened the recording ends
        in the "no llega audio" screen (↵ retries, looking the default device up again)."""
        a = self.audio
        retry, self.retry_mic = self.retry_mic, False
        try:
            if retry:
                a.close()            # a stream that delivered only silence is replaced too
            a.open(refresh=retry)
        except Exception:  # noqa: BLE001 - MicError, or whatever the audio driver throws
            F.mic_failed(self.flow)
            self.retry_mic = True
            return
        self._listen()

    def _wait_ready(self) -> None:
        s = self.audio.session
        while not s.ready.is_set():
            self.region.draw(self._loading_lines(hint=False))   # keys are not read while waiting here
            self.i += 1
            time.sleep(FRAME_S)
        self._flush_keys()   # keys typed while loading must not act on the new state

    def _loading_lines(self, hint: bool = True) -> list[str]:
        """"Descargando modelo · 420 MB / ~1,6 GB · esc salir" while the model downloads
        (first run), then "Cargando modelo… N s". Figures only when the downloader reports
        bytes: no invented percentage."""
        icon = ui.paint(ui.AMBER, ui.SPINNER[self.i % 10])
        d, now = stt.download, time.monotonic()
        if d.active:
            if d.done > 0:
                label = f"Descargando modelo · {stt.format_size(d.done)}"
                if d.total:
                    label += f" / ~{stt.format_size(max(d.total, d.done))}"
                variants = [label, "Descargando modelo"]
            else:
                secs = int(max(0, now - d.started_at))
                size = f"~{stt.format_size(d.total)}, " if d.total else ""
                variants = [f"Descargando modelo ({size}solo la primera vez)… {secs} s",
                            f"Descargando modelo… {secs} s"]
            room = ui.width() - 1
            label = next((v for v in variants if len(v) + 3 <= room), variants[-1])
            return self._with_exit_hint(icon, label, hint, " · ")
        secs = int(max(0, now - max(self.load_t0, d.ended_at)))
        return self._with_exit_hint(icon, f"Cargando modelo… {secs} s", hint, "   ")

    @staticmethod
    def _with_exit_hint(icon: str, label: str, hint: bool, sep: str) -> list[str]:
        label = ui.paint(ui.GRAY, label)
        return _status(icon, label, [("", "esc salir")], sep=sep) if hint else [ui.status_line(icon, label)]

    def _flush_keys(self) -> None:
        flush = getattr(self.keys, "flush_input", None)
        if flush is not None:
            flush()

    def _listen(self) -> None:
        self.audio.session.release()
        self.listen_t0 = time.monotonic()
        self.audio.gate = True

    def _stop_listening(self) -> None:
        a = self.audio
        with a.lock:
            if a.gate:
                self.seconds += time.monotonic() - self.listen_t0
            a.gate = False
            last = a.seg.flush()
            if last is not None:
                a.session.submit(last)

    def _finish(self, keys) -> None:
        t0 = time.monotonic()
        self._stop_listening()
        a = self.audio
        t = threading.Thread(target=lambda: a.session.drain(timeout=FINISH_TIMEOUT_S), daemon=True)
        t.start()
        while t.is_alive():
            if self.force_exit:                   # drain() notices it within ~50 ms
                a.session.abandon()
            label = finishing_label(time.monotonic() - t0, a.session.pending)
            lines = _status(ui.paint(ui.AMBER, ui.SPINNER[self.i % 10]), ui.paint(ui.GRAY, label),
                            [("", "Ctrl-C otra vez: salir ya")])
            lines += ui.wrap_transcript(a.session.committed[self.printed:], "", ui.width(),
                                        self.region.max_text_lines)
            self.region.draw(lines)
            if keys is not None:
                keys.read(FRAME_S)
            else:
                time.sleep(FRAME_S)
            self.i += 1
        if a.session.abandoned:                   # timed out or forced: audio saved, session unfinished
            self.incomplete = True
        # chunks that errored, plus the ones given up on: REVIEW / NOVOICE say so
        self.lost = self.lost_carry + a.session.failed_chunks + (a.session.pending if a.session.abandoned else 0)
        F.finished(self.flow, a.session.committed, self.seconds)
        self._flush_keys()
        cur = self.flow.current
        if cur.novoice:
            if not self.incomplete_text and a.seg.peak_level < self.silence_rms:
                cur.nomic = self.retry_mic = True    # digital silence: the mic, not the voice
            return
        self._print_take(cur)
        if cur.discarded:
            self._discard_effects(cur)
        elif self.copy_mode == "auto":
            self._copy(cur.text, cur.n)

    def _rec_clean(self) -> bool:
        """May the current recording be closed as clean (lock removed)? Not when any chunk was
        given up on or failed: the WAV holds audio the text does not."""
        s = self.audio.session
        return not (self.incomplete_text or getattr(s, "abandoned", False) or getattr(s, "failed_chunks", 0))

    @property
    def incomplete_text(self) -> bool:
        """The current recording has chunks that were never transcribed."""
        return self.incomplete or self.lost > 0

    def _recovery_lines(self) -> list[str]:
        """How to transcribe the whole saved audio, as lines that never get cut."""
        intro, command = "Para transcribirlo entero:", f'voz "{_short_path(self.audio.rec.wav_path)}"'
        lines = [f"{intro}  {command}"] if len(intro) + len(command) + 2 <= ui.width() - 1 else [intro, "   " + command]
        return [ui.paint(ui.GRAY, ln) for ln in lines]

    def _print_take(self, cur: F.Take) -> None:
        """The recording stays in the terminal history (stderr), above the region."""
        new = cur.text[self.printed:].strip()
        if not new:
            return
        self.region.clear()
        title = f"── grabación {cur.n}{' (continúa)' if self.printed else ''} "
        sys.stderr.write(ui.paint(ui.GRAY, ui.fit(title + "─" * 60, ui.width() - 1)) + "\n" + new + "\n\n")
        sys.stderr.flush()
        self.printed = len(cur.text)

    def _write_clipboard(self, text: str, owner: int | str, hist_take: int | None = None) -> bool:
        """The one way voz writes the clipboard. Remembers what was there first (unless it is
        what voz itself wrote before: then the older snapshot still holds) and what voz wrote,
        so a later discard only touches the clipboard while it still holds voz's own text."""
        import output

        cur = output.from_clipboard()
        if cur is not None and (self.clip_text is None or cur != self.clip_text):
            self.clip_before = cur
        ok = output.to_clipboard(text)
        if ok:
            self.clip_owner, self.clip_text, self.clip_hist_take = owner, text, hist_take
        return ok

    def _copy(self, text: str, owner: int | str) -> None:
        if getattr(self.args, "no_copy", False):
            return
        self.copied = self._write_clipboard(text, owner)
        self.copy_note = bool(self.copied)

    def _holds(self, n: int) -> bool:
        """Is take `n` (or a history copy of it) what voz last put in the clipboard?"""
        return self.clip_owner == n or (self.clip_owner == "history" and self.clip_hist_take == n)

    def _uncopy(self, n: int) -> None:
        """A discarded recording must not stay in the clipboard: put back the previous valid
        recording or, if none, what the user had before running voz. Only while the clipboard
        STILL holds what voz wrote: if the user copied something else meanwhile, that is theirs
        and stays (and leaving voz will not overwrite it either)."""
        import output

        if not self._holds(n):
            return
        current = output.from_clipboard()
        if current is None:                       # cannot read it: touch nothing
            return
        self.copied = None
        if current != self.clip_text:
            self._forget_clipboard("external")
            return
        prev = self.flow.valid_takes()
        if prev and self.copy_mode == "auto":
            self._copy(prev[-1].text, prev[-1].n)
        else:
            if self.clip_before is not None:
                output.to_clipboard(self.clip_before)
            self._forget_clipboard(None)

    def _forget_clipboard(self, owner: str | None) -> None:
        """voz no longer tracks what is in the clipboard ("external": someone else wrote it)."""
        self.clip_owner, self.clip_text, self.clip_before, self.clip_hist_take = owner, None, None, None

    def _discard_effects(self, cur: F.Take) -> None:
        """What discarding a take does, whichever key did it (esc while listening, ⌫ in the
        review): mark it on disk and take it back out of the clipboard."""
        self.audio.rec.mark("[descartada]")
        self._uncopy(cur.n)

    def _discard_notice(self, cur: F.Take) -> str:
        head = f"{ui.paint(ui.RED, '✖')}  Grabación {cur.n} descartada "
        if getattr(self.audio.rec, "audio_failed", None) is not None:   # never promise a backup that failed
            return head + ui.paint(ui.GRAY, "(el audio no se pudo guardar)")
        where = self.audio.rec.wav_path if ui.width() >= NARROW else self.audio.rec.wav_path.name
        return head + ui.paint(ui.GRAY, f"(el audio sigue en {where})")

    def _backup_lines(self, problems_only: bool = False) -> list[str]:
        """Where the backup of this recording is, from what REALLY got written: a dim line of its
        own ('Respaldo: ~/…/sesion-…-g1 · texto y audio'), or an amber one when the disk refused
        the text and/or the audio. `problems_only`: say nothing when all is well."""
        rec = self.audio.rec
        wav = getattr(rec, "wav_path", None)
        if rec is None or wav is None:
            return []
        wav = Path(wav)
        audio_ok = getattr(rec, "audio_failed", None) is None
        text_ok = getattr(rec, "text_failed", None) is None
        if audio_ok and text_ok:
            if problems_only:
                return []
            color, note, path = ui.GRAY, "texto y audio", wav.with_suffix("")
        elif text_ok:
            color, note, path = ui.AMBER, "el audio no se pudo guardar", wav.with_suffix(".txt")
        elif audio_ok:
            color, note, path = ui.AMBER, "el texto no se pudo guardar", wav
        else:
            return [ui.paint(ui.AMBER, "⚠  Respaldo: no se pudo guardar ni el texto ni el audio")]
        line = f"Respaldo: {_short_path(path)}  ·  {note}"
        if len(line) > ui.width() - 1:                         # long path on a narrow terminal: just the name
            line = f"Respaldo: {path.name}  ·  {note}"
        return [ui.paint(color, line)]

    def _notice_lines(self) -> list[str]:
        if not self.notice:
            return []
        plain = ui._ANSI.sub("", self.notice)
        lines = _wrapped(plain)
        color = ui.GREEN if plain.startswith(("✓", "↺")) else ui.AMBER
        return [ui.paint(color, lines[0][:1]) + lines[0][1:],
                *[ui.paint(ui.GRAY, line) for line in lines[1:]]]

    # ---------- actions ----------
    def perform(self, st: F.Step, keys):
        a, cur = self.audio, self.flow.current
        act = st.action
        if act is F.Act.START:
            self._start_recording()
            if a.session.error is not None:
                return self._model_failed()
        elif act is F.Act.CONTINUE:
            self.copied, self.notice = None, ""
            self._renew_abandoned()
            self._listen()
        elif act is F.Act.PAUSE:
            self._stop_listening()
            a.session.hold()
        elif act is F.Act.RESUME:
            self._listen()
        elif act is F.Act.FINISH:
            self._finish(keys)
        elif act is F.Act.DISCARD:
            self._discard_effects(cur)
        elif act is F.Act.UNDO:
            a.rec.mark("[restaurada]")
            self.notice = f"{ui.paint(ui.GREEN, '↺')}  Grabación {cur.n} restaurada"
            if self.copy_mode == "auto":
                self._copy(cur.text, cur.n)
        elif act is F.Act.COPY:
            self._copy(cur.text, cur.n)
            self.notice = ""
        elif act is F.Act.HISTORY_OPEN:
            self._open_history()
        elif act is F.Act.HISTORY_COPY:
            self._copy_history()
        elif act is F.Act.EXIT:
            return self._exit(st)
        return None

    @property
    def history_allowed(self) -> bool:
        """--no-copy: nothing is ever copied, so there is nothing to pick from the history either."""
        return not getattr(self.args, "no_copy", False)

    @property
    def hist_keys(self) -> list[str]:
        return ["h historial"] if self.history_allowed else []

    def _open_history(self) -> None:
        """Read-only: the saved recordings, same list and numbering as `voz copiar --lista`
        (valid ones first, discarded ones last and marked)."""
        recs = autosave.listing()[:F.HISTORY_MAX] if self.history_allowed else []
        mine = getattr(self.audio.rec, "base", None)
        cur = self.flow.current
        # the WAV header of the recording in progress lags up to a second behind: use our own clock
        self.history = [(r, cur.seconds if mine is not None and cur is not None and r.stem == mine
                         else autosave.duration(r)) for r in recs]
        F.history_loaded(self.flow, len(self.history))
        if not self.history:
            self.notice = f"{ui.paint(ui.AMBER, '⚠')}  No hay grabaciones guardadas"

    def _copy_history(self) -> None:
        """Copy the highlighted saved recording. The current one is left as it was (its summary
        stops saying "Copiado": the clipboard now holds something else)."""
        rec, _ = self.history[self.flow.hist_sel]
        mine = rec.n if rec.run == self.run_id else None      # a recording of THIS run: discarding it retires the copy
        if not self._write_clipboard(rec.text, "history", mine):
            self.notice = f"{ui.paint(ui.AMBER, '⚠')}  No se pudo copiar (pbcopy no disponible)"
            return
        self.copied, self.copy_note = None, False
        where = f"sesión {autosave.when(rec.run)} · grabación {rec.n} · {len(rec.text.split())} palabras"
        if getattr(rec, "discarded", False):
            where += " · descartada"
        self.notice = f"{ui.paint(ui.GREEN, '✓')}  Copiado" + ui.paint(ui.GRAY, f" · {where}")   # as `voz copiar`

    def _model_failed(self):
        self.region.clear()
        print(f"{ui.paint(ui.RED, '✖')}  No se pudo cargar el modelo: {self.audio.session.error}\n"
              "   Prueba `voz doctor` para ver qué falta.", file=sys.stderr)
        self._cleanup()
        return 2, ""

    def _exit(self, st: F.Step):
        valid = self.flow.valid_takes()
        if st.copy_all:
            text = F.joined(self.flow.takes)
            self._copy(text, "all")
        else:
            text = valid[-1].text if valid else ""
            if text and not st.keep_clipboard and self.clip_owner != valid[-1].n and (
                    self.clip_owner not in ("history", "external") or st.copy_last) and (
                    st.copy_last or self.copy_mode == "auto"):
                # ELEGIR ↵ promises "la última" in every copy mode; in auto it also covers
                # "the last recording was discarded: keep a valid one copied". Not over a
                # recording the user picked from the history (or copied elsewhere): that is the last word
                self._copy(text, valid[-1].n)
        self._cleanup()
        self.region.clear()
        # esc in ELEGIR = "salir sin cambiar": nothing chosen, nothing on stdout either
        if text and not st.keep_clipboard and not sys.stdout.isatty():
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        if not self.flow.takes:
            return 0, ""                  # esc in LISTO: the user chose to leave (Ctrl-C → 130)
        return (0, text) if text else (1, "")

    def _on_second_sigint(self, signum, frame) -> None:
        self.force_exit = True

    def _interrupted(self):
        """Ctrl-C: finish what is being recorded and leave the last valid text copied.
        The exit code is always 130, whatever text there is (kept in the state, the
        clipboard per the config, and stdout when it is not a terminal)."""
        cur = self.flow.current
        recording = self.flow.state in (F.St.LISTEN, F.St.PAUSED, F.St.FINISHING)
        if recording and self.audio.session and self.audio.take == cur.n:
            self.flow.state = F.St.FINISHING
            self._finish(None)
        elif recording and cur is not None and not cur.text:
            # Ctrl-C between `r` and the new engine: the engine still in place holds the
            # PREVIOUS recording's text (maybe a discarded one); it must not become this one
            self.flow.takes.pop()
        self.flow.state = F.St.DONE
        _, text = self._exit(F.Step(F.Act.EXIT))
        return 130, text

    def _interrupt_message(self, text: str) -> str:
        """What Ctrl-C actually left behind, never promising a backup that failed:
        'Interrumpido · texto copiado' | 'Interrumpido · audio guardado en <ruta>' |
        'Interrumpido' | 'Cancelado' (nothing had been recorded)."""
        rec = self.audio.rec
        if not any(t.seconds > 0 or t.text for t in self.flow.takes):
            return "Cancelado"
        if text and self.copied is True:
            return "Interrumpido · texto copiado"
        if rec is not None and getattr(rec, "audio_failed", None) is None and hasattr(rec, "wav_path"):
            return f"Interrumpido · audio guardado en {_short_path(rec.wav_path)}"
        return "Interrumpido"

    def _cleanup(self) -> None:
        """Idempotent: safe to call from _exit, error paths and the final `finally`."""
        a = self.audio
        a.close()
        for s in (a.session, self.next_session):
            if s is not None:
                try:
                    s.finish(timeout=CLOSE_JOIN_S)
                except Exception:  # noqa: BLE001
                    pass
        self.next_session = None
        if a.rec is not None:
            # chunks given up on (or that failed) keep the .lock: the next voz offers to re-transcribe the WAV
            a.rec.close(clean=self._rec_clean())

    # ---------- rendering ----------
    def lines(self) -> list[str]:
        f, a, s = self.flow, self.audio, self.flow.state
        n = f.current.n if f.current else 1
        tag = "  " + ui.paint(ui.GRAY, f"grabación {n}") if n > 1 else ""
        if s is F.St.READY:
            sess = self.next_session or a.session
            if sess.ready.is_set() and sess.error is not None:
                # the load already failed: say so now, do not wait for the ↵ to find out
                return [f"{ui.paint(ui.RED, '✖')}  No se pudo cargar el modelo: {_first_line(sess.error)}",
                        *_hint(["prueba `voz doctor` para ver qué falta", "esc salir"],
                               ["prueba `voz doctor`", "esc salir"])]
            if not sess.ready.is_set():
                out = self._loading_lines()
                if self.early_enter:
                    out.append(f"{ui.paint(ui.AMBER, '⚠')}  ↵ ignorado: el modelo todavía está cargando")
                return out
            self.early_enter = False
            hist = " · h historial" if self.history_allowed else ""
            out = _status(ui.paint(ui.GREEN, "●"), "Listo",
                          [("", f"↵ hablar{hist} · esc salir"), ("", "↵ hablar · esc salir")])
            out += self._notice_lines()
            if self.welcome is None:
                self.welcome = _first_run()
            if self.welcome:
                out.append(ui.paint(ui.GRAY, WELCOME_TEXT))
            return out
        if s in (F.St.LISTEN, F.St.PAUSED):
            el = self.seconds + (time.monotonic() - self.listen_t0 if s is F.St.LISTEN else 0.0)
            alerts: list[str] = []
            if s is F.St.LISTEN:
                dot = ui.paint(ui.RED, "●" if int(el * 2) % 2 == 0 else "○")
                clock = ui.paint(ui.GRAY, ui.fmt_time(el))
                bars = f"{clock}  {ui.meter(*a.seg.meter_snapshot())}"
                lang = "  " + ui.paint(ui.GRAY, self.langs[-1]) if self.args.lang == "auto" and self.langs else ""
                # data-loss warnings get their own line(s): they must not push the keys out
                warnings = [(ui.AMBER, f"⚠ audio perdido ×{a.overflows}")] if a.overflows else []
                if a.rec is not None and a.rec.audio_failed is not None:
                    warnings.append((ui.RED, "⚠ el audio no se está guardando en disco"))
                elif a.rec is not None and a.rec.text_failed is not None:
                    warnings.append((ui.RED, "⚠ el texto no se está guardando en disco"))
                if a.session.failed_chunks:
                    warnings.append((ui.AMBER, f"⚠ tramos fallidos ×{a.session.failed_chunks}"))
                alerts = [ui.paint(color, ln) for color, text in warnings for ln in _wrapped(text, "  ")]
                detail = bars + lang
                head = _status(dot, ui.paint(ui.TEXT + ";1", "Escuchando") + tag, [
                    (detail, "↵ terminar · p pausa · esc descartar"),
                    (detail, "↵ fin · p pausa · esc descartar"),
                    (detail, "↵ fin · p pausa · esc")], poorer=(bars, clock))
            else:
                clock = ui.fmt_time(el)
                long_d = ui.paint(ui.GRAY, f"{clock}   micrófono abierto, no se graba")
                short_d = ui.paint(ui.GRAY, f"{clock}  no se graba")
                head = _status(ui.paint(ui.AMBER, "‖"), ui.paint(ui.TEXT + ";1", "En pausa") + tag, [
                    (long_d, "p reanudar · ↵ terminar · esc descartar"),
                    (short_d, "p reanudar · ↵ terminar · esc descartar"),
                    (short_d, "p reanudar · ↵ fin · esc descartar"),
                    (short_d, "p reanudar · ↵ fin · esc")], poorer=(ui.paint(ui.GRAY, clock),))
            text = a.session.committed[self.printed:]
            return head + alerts + ui.wrap_transcript(text, a.session.partial, ui.width(), self.region.max_text_lines)
        if s is F.St.REVIEW:
            cur = f.current
            hist = self.hist_keys
            if cur.discarded:
                return [self._discard_notice(cur), *self._notice_lines(),
                        *_hint(["u deshacer", "r grabar otra"] + hist + ["↵ salir"],
                               ["u deshacer", "r otra"] + hist + ["↵ salir"])]
            copy_key = self.history_allowed       # `s` is offered whenever there is text to copy (not with --no-copy)
            again = "s copiar de nuevo" if self.copied else "s copiar"
            leave = "↵ salir · Cmd-V para pegar" if self.copied else "↵ salir"   # only when it IS copied
            items = [leave, "r grabar otra", "c añadir", "⌫ descartar"] + ([again] if copy_key else []) + hist
            short = [leave, "r otra", "c añadir", "⌫ descartar"] + ([again] if copy_key else []) + hist
            gaps = _untranscribed(self.lost) if self.incomplete_text else ""
            skipped = gated_text(getattr(a.session, "gated", 0), bool(os.environ.get(live.DIAG_ENV)))
            args = (self.copied, cur.seconds, cur.text, self.langs, self.args.lang)
            kw = {"replaced": self.copy_note}
            summary, extra = summary_line(*args, untranscribed=gaps, gated=skipped, **kw), []
            if gaps and skipped:
                extra = [ui.paint(ui.GRAY, skipped)]
            if skipped and ui.visible_len(summary) > ui.width() - 1:
                # tight: the compact summary, else the skipped chunks on a dim line of their own
                summary = summary_line(*args, compact=True, untranscribed=gaps, gated=skipped, **kw)
                if ui.visible_len(summary) > ui.width() - 1:
                    summary, extra = summary_line(*args, untranscribed=gaps, **kw), [ui.paint(ui.GRAY, skipped)]
            if self.clip_owner == "history" and not gaps:
                summary = summary.replace("Sin copiar", "Grabación actual sin copiar")
            out = [summary, *extra]
            audio_ok = getattr(a.rec, "audio_failed", None) is None
            if gaps and audio_ok:
                out += self._recovery_lines()
            out += self._backup_lines(problems_only=bool(gaps and audio_ok))
            out += self._notice_lines()
            return out + _hint(items, short)
        if s is F.St.HISTORY:
            room = max(1, shutil.get_terminal_size((80, 24)).lines - 1 - len(_history_hints()))
            return history_lines(self.history, f.hist_sel, min(HISTORY_ROWS, room))
        if s is F.St.NOVOICE:
            cur = f.current
            if self.incomplete_text:             # chunks existed but none could be transcribed
                saved = getattr(a.rec, "audio_failed", None) is None
                out = [f"{ui.paint(ui.RED, '✖')}  No se pudo transcribir"
                       + (ui.paint(ui.GRAY, " · el audio quedó guardado") if saved else "")]
                if saved:
                    out += self._recovery_lines()
                hist = self.hist_keys
                return out + self._notice_lines() + _hint(["r intentar de nuevo", *hist, "↵ salir"],
                                                          ["r reintentar", *hist, "↵ salir"])
            if cur.nomic:
                hist = self.hist_keys
                return [f"{ui.paint(ui.RED, '✖')}  {record.MIC_PROBLEM}",
                        *[ui.paint(ui.GRAY, ln) for ln in _wrapped(record.MIC_HELP)],
                        *self._notice_lines(),
                        *_hint(["↵ reintentar", *hist, "esc salir"], ["↵ reintentar", *hist, "esc salir"])]
            saved = getattr(a.rec, "audio_failed", None) is None   # never promise a backup that failed
            msg = f"{ui.paint(ui.AMBER, '⚠')}  No se detectó voz " + ui.paint(
                ui.GRAY, "· el audio quedó guardado por si acaso" if saved else "· el audio no se pudo guardar")
            hist = self.hist_keys
            return [msg, *self._notice_lines(),
                    *_hint(["r intentar de nuevo", *hist, "↵ salir"], ["r reintentar", *hist, "↵ salir"])]
        if s is F.St.CHOOSE:
            out = [f"{len(f.takes)} grabaciones en esta sesión:"]
            for t in f.takes:
                if t.valid:
                    snippet = t.text[:48] + ("…" if len(t.text) > 48 else "")
                    out.append(f"  {t.n}  {_dur(t.seconds):>8}  {len(t.text.split()):>4} palabras  «{snippet}»")
                else:
                    out.append(ui.paint(ui.GRAY, f"  {t.n}  ── {'descartada' if t.discarded else 'sin voz'} ──"))
            return out + _hint(["↵ dejar copiada la última", "a copiar todas juntas", "esc salir sin cambiar"],
                               ["↵ la última", "a todas", "esc no cambiar"])
        return []

    def on_key(self, raw, keys):
        """One frame: a key (or None) → the result of acting on it, None to keep going."""
        self.i += 1
        key = F.norm(raw)
        if key is not None:
            self.copy_note = False       # "(reemplazó tu portapapeles)" is for the moment of the copy only
            if self.flow.state in (F.St.READY, F.St.NOVOICE):
                self.notice = ""         # one-line feedback there lasts until the next key
        if key == "h" and self.flow.state in (F.St.READY, F.St.REVIEW, F.St.NOVOICE) and not self.history_allowed:
            return None
        if key in (F.ENTER, F.SPACE) and self.flow.state is F.St.READY:
            sess = self.next_session or self.audio.session
            if sess is not None and not sess.ready.is_set():
                self.early_enter = True      # not lost in silence: READY says it was ignored
                return None
        return self.perform(F.step(self.flow, key), keys)

    def run(self):
        mic = "" if getattr(self.args, "sim", None) else _mic_chip()   # --sim: no real mic
        sys.stderr.write(header_line(self.args.model, self.args.lang, mic) + "\n")
        import signal

        stt.download.quiet = True      # the model download is shown in the region, not printed
        with ArrowKeys() as keys:
            self.keys = keys
            try:
                while True:
                    self.region.draw(self.lines())
                    result = self.on_key(keys.read(FRAME_S), keys)
                    if result is not None:
                        return result
            except KeyboardInterrupt:
                # safe exit: finish what is being recorded, keep the last valid text copied.
                # A second Ctrl-C while waiting does not abort the cleanup: it only stops the
                # wait for pending chunks (force_exit); the WAV stays and the session is
                # marked unfinished.
                old = signal.signal(signal.SIGINT, self._on_second_sigint)
                try:
                    code, text = self._interrupted()
                finally:
                    signal.signal(signal.SIGINT, old)
                if self.incomplete_text and hasattr(self.audio.rec, "wav_path"):
                    print(live.incomplete_notice(self.audio.rec.wav_path, self.lost), file=sys.stderr)
                print(f"{ui.paint(ui.GRAY, '✖')}  {self._interrupt_message(text)}", file=sys.stderr)
                return code, text
            finally:
                # any exit path (incl. unexpected errors) stops the mic and closes files:
                # leaving the stream open makes PortAudio's atexit hit the CoreAudio deadlock
                self._cleanup()
                stt.download.quiet = False


def run(args, rules, transcribe_fn, apply_fn, *, warm: bool = True,
        silence_rms: float = 1e-4, sim: str | None = None):
    """Returns (exit_code, text). exit codes: 0 ok · 1 nothing · 2 model · 130 Ctrl-C (the text, if any, is still returned)."""
    if sim:
        args.sim = sim
    return Shell(args, rules, transcribe_fn, apply_fn, warm, silence_rms).run()
