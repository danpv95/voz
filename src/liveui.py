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
import sys
import textwrap
import threading
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


def summary_line(copied, seconds: float, text: str, langs: list[str], lang_arg: str,
                 compact: bool | None = None, untranscribed: str = "") -> str:
    """e.g. '✓  Copiado (reemplazó tu portapapeles)  ·  42 s  ·  31 palabras  ·  es'.
    Compact (narrow terminals) drops the parenthesis so duration and words survive.
    `untranscribed` ("2 tramos sin transcribir") turns it into the incomplete-text warning:
    '⚠  Texto incompleto  ·  2 tramos sin transcribir  ·  copiado'."""
    if compact is None:
        compact = ui.width() < NARROW
    if untranscribed:
        tail = {True: ["copiado"], False: ["no se pudo copiar"]}.get(copied, [])
        return (ui.paint(ui.AMBER, "⚠") + "  Texto incompleto"
                + ui.paint(ui.GRAY, "  ·  " + "  ·  ".join([untranscribed, *tail])))
    if copied is True:
        head = f"{ui.paint(ui.GREEN, '✓')}  Copiado" + ("" if compact else " " + ui.paint(ui.GRAY, "(reemplazó tu portapapeles)"))
    elif copied is False:
        head = f"{ui.paint(ui.AMBER, '⚠')}  No se pudo copiar (pbcopy no disponible)"
    else:
        head = f"{ui.paint(ui.GRAY, '·')}  Sin copiar"
    used = sorted(set(langs), key=langs.index) if lang_arg == "auto" else [lang_arg]
    parts = [_dur(seconds), f"{len(text.split())} palabras"]
    if used:
        parts.append("+".join(used))
    return head + ui.paint(ui.GRAY, "  ·  " + "  ·  ".join(parts))


def finishing_label(seconds: float, pending: int) -> str:
    """'Terminando… 3 s · 2 tramos pendientes' (no tail when nothing is pending)."""
    label = f"Terminando… {int(max(0, seconds))} s"
    if pending > 0:
        label += f" · {pending} tramo{'' if pending == 1 else 's'} pendiente{'' if pending == 1 else 's'}"
    return label


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
        self.retry_mic = False           # the last recording got no audio: re-query the device on retry
        # --no-copy wins over the config: nothing is ever copied, `s` is not offered
        self.copy_mode = "nunca" if getattr(args, "no_copy", False) else self.cfg["copy"]
        self.clip_before: str | None = None   # clipboard before voz first touched it
        self.clip_owner: int | str | None = None  # take n (or "all") now in the clipboard

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
            a.rec.close(clean=not (self.incomplete or a.session.abandoned))
            self.incomplete, self.lost = False, 0
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
        self.lost = a.session.failed_chunks + (a.session.pending if a.session.abandoned else 0)
        F.finished(self.flow, a.session.committed, self.seconds)
        self._flush_keys()
        cur = self.flow.current
        if cur.novoice:
            if not self.incomplete_text and a.seg.peak_level < self.silence_rms:
                cur.nomic = self.retry_mic = True    # digital silence: the mic, not the voice
            return
        self._print_take(cur)
        if cur.discarded:
            a.rec.mark("[descartada]")
        elif self.copy_mode == "auto":
            self._copy(cur.text, cur.n)

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

    def _copy(self, text: str, owner: int | str) -> None:
        import output

        if getattr(self.args, "no_copy", False):
            return
        if self.clip_owner is None and self.clip_before is None:
            self.clip_before = output.from_clipboard()   # to give it back on a discard
        self.copied = output.to_clipboard(text)
        if self.copied:
            self.clip_owner = owner

    def _uncopy(self, n: int) -> None:
        """A discarded recording must not stay in the clipboard: put back the previous
        valid recording or, if none, what the user had before running voz."""
        import output

        if self.clip_owner != n:
            return
        prev = self.flow.valid_takes()
        if prev and self.copy_mode == "auto":
            self._copy(prev[-1].text, prev[-1].n)
        else:
            if self.clip_before is not None:
                output.to_clipboard(self.clip_before)
            self.clip_owner = None
        self.copied = None

    def _discard_notice(self, cur: F.Take) -> str:
        where = self.audio.rec.wav_path if ui.width() >= NARROW else self.audio.rec.wav_path.name
        return (f"{ui.paint(ui.RED, '✖')}  Grabación {cur.n} descartada "
                + ui.paint(ui.GRAY, f"(el audio sigue en {where})"))

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
            self._listen()
        elif act is F.Act.PAUSE:
            self._stop_listening()
            a.session.hold()
        elif act is F.Act.RESUME:
            self._listen()
        elif act is F.Act.FINISH:
            self._finish(keys)
        elif act is F.Act.DISCARD:
            a.rec.mark("[descartada]")
            self._uncopy(cur.n)
        elif act is F.Act.UNDO:
            a.rec.mark("[restaurada]")
            self.notice = f"{ui.paint(ui.GREEN, '↺')}  Grabación {cur.n} restaurada"
            if self.copy_mode == "auto":
                self._copy(cur.text, cur.n)
        elif act is F.Act.COPY:
            self._copy(cur.text, cur.n)
        elif act is F.Act.EXIT:
            return self._exit(st)
        return None

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
                    st.copy_last or self.copy_mode == "auto"):
                # ELEGIR ↵ promises "la última" in every copy mode; in auto it also covers
                # "the last recording was discarded: keep a valid one copied"
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
            # chunks given up on keep the .lock: the next voz offers to re-transcribe the WAV
            a.rec.close(clean=not (self.incomplete or getattr(a.session, "abandoned", False)))

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
            out = _status(ui.paint(ui.GREEN, "●"), "Listo", [("", "↵ hablar · esc salir")])
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
            if cur.discarded:
                return [self._discard_notice(cur),
                        *_hint(["u deshacer", "r grabar otra", "↵ salir"], ["u deshacer", "r otra", "↵ salir"])]
            copy_key = self.copy_mode != "auto" and not getattr(self.args, "no_copy", False)
            leave = "↵ salir · Cmd-V para pegar" if self.copied else "↵ salir"   # only when it IS copied
            items = [leave, "r grabar otra", "c añadir", "⌫ descartar"] + (["s copiar"] if copy_key else [])
            short = [leave, "r otra", "c añadir", "⌫ descartar"] + (["s copiar"] if copy_key else [])
            gaps = _untranscribed(self.lost) if self.incomplete_text else ""
            out = [summary_line(self.copied, cur.seconds, cur.text, self.langs, self.args.lang, untranscribed=gaps)]
            if gaps:
                out += self._recovery_lines()
            if self.notice:
                out.append(self.notice)
            return out + _hint(items, short)
        if s is F.St.NOVOICE:
            cur = f.current
            if self.incomplete_text:             # chunks existed but none could be transcribed
                saved = getattr(a.rec, "audio_failed", None) is None
                out = [f"{ui.paint(ui.RED, '✖')}  No se pudo transcribir"
                       + (ui.paint(ui.GRAY, " · el audio quedó guardado") if saved else "")]
                if saved:
                    out += self._recovery_lines()
                return out + _hint(["r intentar de nuevo", "↵ salir"], ["r reintentar", "↵ salir"])
            if cur.nomic:
                return [f"{ui.paint(ui.RED, '✖')}  {record.MIC_PROBLEM}",
                        *[ui.paint(ui.GRAY, ln) for ln in _wrapped(record.MIC_HELP)],
                        *_hint(["↵ reintentar", "esc salir"], ["↵ reintentar", "esc salir"])]
            msg = f"{ui.paint(ui.AMBER, '⚠')}  No se detectó voz " + ui.paint(ui.GRAY, "· el audio quedó guardado por si acaso")
            return [msg, *_hint(["r intentar de nuevo", "↵ salir"], ["r reintentar", "↵ salir"])]
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
        with ui.Keys() as keys:
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
