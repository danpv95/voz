"""Live dictation: transcribe while you talk, then ask whether to copy.

The mic stream is cut into chunks at natural pauses (Segmenter, pure numpy, unit
tested). A single worker thread owns the model: it warms up while you press the
first Enter, then transcribes each chunk as it arrives and prints it right away.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from collections import deque

import numpy as np

from record import SAMPLE_RATE, adaptive_threshold, open_input, prompt, stop_stream, voice_band_sos
from stt import LangState

FRAME_MS = 30
PAUSE_MS = 700        # silence that closes a chunk
MIN_CHUNK_S = 0.6     # shorter chunks wait for more audio (avoids 1-word chunks)
MAX_CHUNK_S = 12.0    # live: bounded chunks keep every partial re-transcription cheap
SOFT_CUT_S = 1.5      # at the limit, cut at the quietest frame of the last 1.5 s
PREROLL_MS = 300      # audio kept before the first voiced frame (soft onsets)
HISTORY_S = 5.0       # rolling window for the adaptive threshold
MIN_HISTORY_MS = 300  # don't call anything "voice" before this much context
FINISH_TIMEOUT_S = 20.0  # how long closing waits for pending chunks to be transcribed
# Silence gate (issue #7, relative to the room since v0.1.2): a chunk reaches Whisper only
# when its loudest 30 ms frame (raw RMS, chunk_peak) stands out of the session noise floor:
# p10 of the raw levels of the frames without voice in the last NOISE_WINDOW_S, measured
# when the chunk is cut (causal: only audio already heard). Short chunks are where Whisper invents sentences, so
# they need a louder peak:
#   threshold = max(MIN_CHUNK_PEAK, snr × max(floor, NOISE_FLOOR_MIN))
#   snr = MIN_SNR for chunks ≥ SHORT_CHUNK_S, MIN_SNR_SHORT for shorter ones
# The floor only counts frames without voice: with continuous speech a floor over every
# frame rose 3× and gated real chunks of up to 11 s. Measured on 9 real sessions (920 chunks:
# dictation, low voice, meetings, calls): v0.1.1 kept 782, this gate keeps 859 and drops
# none that v0.1.1 kept; a quiet-throat dictation went from 61/108 to 106/108 chunks.
# Short chunks under 8 × floor were silence or a lone filler word. Remote voices through a
# phone speaker sit at 2-3 × floor and stay gated: no energy rule separates them from the
# room. A ratio does not depend on the mic gain. Gated chunks are counted and logged
# (VOZ_DIAG) and the WAV autosave keeps their audio.
MIN_CHUNK_PEAK = 0.001      # absolute minimum (≈ -60 dBFS): below it nothing is speech
MIN_SNR = 5.0
MIN_SNR_SHORT = 8.0
SHORT_CHUNK_S = 1.5
NOISE_WINDOW_S = 30.0
NOISE_MIN_FRAMES = 33       # ~1 s of pauses before the floor ignores voiced frames
NOISE_FLOOR_MIN = 1e-4      # digital silence must not make every click look loud
DIAG_ENV = "VOZ_DIAG"  # =1: one line per chunk (no text) appended to <state dir>/diag.log


def chunk_peak(chunk: np.ndarray) -> float:
    """Loudest FRAME_MS frame of `chunk` as raw RMS (same measure as Segmenter.peak_level)."""
    return float(frame_levels(chunk).max())


class Chunk(np.ndarray):
    """A float32 audio chunk that also carries the session noise floor known when it was cut
    (Segmenter.noise_floor). Behaves as a plain ndarray for every caller."""

    noise_floor: float | None = None


def _tag(audio: np.ndarray, floor: float) -> np.ndarray:
    out = audio.view(Chunk)
    out.noise_floor = floor
    return out


def frame_levels(chunk: np.ndarray) -> np.ndarray:
    x = np.asarray(chunk, dtype="float32").ravel()
    frame = int(SAMPLE_RATE * FRAME_MS / 1000)
    n = len(x) // frame
    if n == 0:
        return np.array([float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0])
    return np.sqrt(np.mean(np.square(x[: n * frame].reshape(n, frame)), axis=1))


def chunk_floor(chunk: np.ndarray) -> float:
    """Noise floor for the gate: the one the Segmenter attached, else (a plain array) the
    p10 of the chunk's own frames (its preroll and tail are pauses)."""
    floor = getattr(chunk, "noise_floor", None)
    if floor is None:
        floor = float(np.percentile(frame_levels(chunk), 10))
    return float(floor)


def gate_threshold(secs: float, floor: float) -> float:
    """Peak a chunk of `secs` needs over a noise floor `floor` (see the constants above)."""
    snr = MIN_SNR if secs >= SHORT_CHUNK_S else MIN_SNR_SHORT
    return max(MIN_CHUNK_PEAK, snr * max(floor, NOISE_FLOOR_MIN))


def gate(chunk: np.ndarray) -> tuple[bool, float, float, float]:
    """(accepted, peak, floor, threshold) of the silence gate for one chunk."""
    peak, floor = chunk_peak(chunk), chunk_floor(chunk)
    thr = gate_threshold(len(chunk) / SAMPLE_RATE, floor)
    return peak >= thr, peak, floor, thr


def diag(line: str) -> None:
    """Per-chunk diagnostics when VOZ_DIAG is set. File only (the TTY UI owns stderr and
    stdout is the transcript); never raises."""
    import os

    if not os.environ.get(DIAG_ENV):
        return
    try:
        import autosave

        autosave.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(autosave.STATE_DIR / "diag.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except Exception:  # noqa: BLE001 - diagnostics must never break a recording
        pass


class Segmenter:
    """Cut a mic stream into speech chunks at pauses.

    Energy is measured on a 100-4000 Hz band-passed copy (stateful IIR, so block
    boundaries are seamless) and compared with record.adaptive_threshold() over a
    rolling window - the same rule trim_silence uses. Emitted chunks are the
    ORIGINAL audio. Nothing counts as voice until MIN_HISTORY_MS of context exists
    (those frames sit in the preroll, so nothing is lost).
    Feed arbitrary-size blocks; get back finished chunks (float32 arrays).
    """

    def __init__(self, sr: int = SAMPLE_RATE, pause_ms: int = PAUSE_MS,
                 min_chunk_s: float = MIN_CHUNK_S, max_chunk_s: float = MAX_CHUNK_S):
        self.sr = sr
        self.frame = int(sr * FRAME_MS / 1000)
        self.pause_frames = max(1, pause_ms // FRAME_MS)
        self.min_len = int(sr * min_chunk_s)
        self.max_len = int(sr * max_chunk_s)
        self.history: deque[float] = deque(maxlen=int(HISTORY_S * 1000 / FRAME_MS))
        self.preroll: deque[np.ndarray] = deque(maxlen=max(1, PREROLL_MS // FRAME_MS))
        self._rest = np.zeros(0, dtype="float32")
        self._rest_band = np.zeros(0, dtype="float32")
        self._sos = voice_band_sos(sr)
        self._zi = np.zeros((self._sos.shape[0], 2))
        self.min_history = max(1, MIN_HISTORY_MS // FRAME_MS)
        self._chunk: list[np.ndarray] = []
        self._chunk_levels: list[float] = []  # band level per frame, for the soft cut
        self._len = 0                         # samples in _chunk (O(1) instead of summing)
        self.soft_frames = max(1, int(SOFT_CUT_S * 1000 / FRAME_MS))
        self._voiced = False
        self._silent_run = 0
        self.peak_level = 0.0  # for the "no mic permission" check
        self.recent_levels: deque[float] = deque(maxlen=20)  # raw RMS, for the UI meter
        window = int(NOISE_WINDOW_S * 1000 / FRAME_MS)
        self.noise_levels: deque[float] = deque(maxlen=window)  # raw RMS of frames without voice
        self.all_levels: deque[float] = deque(maxlen=window)    # every frame, until pauses exist
        self.recent_voiced: deque[bool] = deque(maxlen=20)   # same frames: over the voice threshold?
        self._lock = threading.Lock()  # feed() runs in the audio thread, current() in the worker

    def noise_floor(self) -> float:
        """Session noise floor known so far: p10 of the raw levels (same raw RMS as
        chunk_peak) of the frames without voice in the last NOISE_WINDOW_S. Continuous
        speech must not raise it: it would inflate the threshold of the next short chunk.
        Until there are NOISE_MIN_FRAMES pauses (talking from the first second), every frame
        counts. 0.0 before any frame."""
        levels = self.noise_levels if len(self.noise_levels) >= NOISE_MIN_FRAMES else self.all_levels
        if not levels:
            return 0.0
        return float(np.percentile(np.fromiter(levels, dtype="float32"), 10))

    def _threshold(self) -> float:
        return adaptive_threshold(np.fromiter(self.history, dtype="float32"))

    def _reset_chunk(self, frames=None, levels=None) -> None:
        self._chunk = list(frames or [])
        self._chunk_levels = list(levels or [])
        self._len = sum(len(f) for f in self._chunk)

    def _append(self, f: np.ndarray, level: float) -> None:
        self._chunk.append(f)
        self._chunk_levels.append(level)
        self._len += len(f)

    def _emit(self) -> np.ndarray:
        # drop the trailing silence beyond a short tail
        keep_tail = max(0, self._silent_run - 6)  # ~180 ms of tail kept
        frames = self._chunk[: len(self._chunk) - keep_tail] if keep_tail else self._chunk
        out = _tag(np.concatenate(frames), self.noise_floor())
        self._reset_chunk()
        self._voiced, self._silent_run = False, 0
        return out

    def _soft_cut(self) -> np.ndarray:
        """Chunk hit the length limit while speech continues: cut at the quietest
        frame of the last SOFT_CUT_S (a breath / gap between words, not mid-word)
        and carry the rest over as the start of the next chunk."""
        n = len(self._chunk)
        lo = max(1, n - self.soft_frames)
        cut = lo + int(np.argmin(self._chunk_levels[lo:]))
        out = _tag(np.concatenate(self._chunk[:cut]), self.noise_floor())
        self._reset_chunk(self._chunk[cut:], self._chunk_levels[cut:])
        self._silent_run = 0
        return out

    def meter_snapshot(self) -> tuple[list[float], list[bool]]:
        """Copy of the UI meter history. feed() appends to both deques from the audio
        thread: iterating them unlocked can raise "deque mutated during iteration"."""
        with self._lock:
            return list(self.recent_levels), list(self.recent_voiced)

    def current(self) -> np.ndarray | None:
        """Snapshot of the chunk in progress (for partial transcription)."""
        with self._lock:
            if not self._voiced or not self._chunk:
                return None
            return _tag(np.concatenate(list(self._chunk)), self.noise_floor())

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        with self._lock:
            return self._feed(block)

    def _feed(self, block: np.ndarray) -> list[np.ndarray]:
        from scipy.signal import sosfilt

        raw = np.asarray(block, dtype="float32").ravel()
        band, self._zi = sosfilt(self._sos, raw, zi=self._zi)
        data = np.concatenate([self._rest, raw])
        bdata = np.concatenate([self._rest_band, band.astype("float32")])
        n = len(data) // self.frame
        self._rest = data[n * self.frame:]
        self._rest_band = bdata[n * self.frame:]
        done: list[np.ndarray] = []
        for i in range(n):
            f = data[i * self.frame:(i + 1) * self.frame]
            fb = bdata[i * self.frame:(i + 1) * self.frame]
            raw_level = float(np.sqrt(np.mean(np.square(f))))
            self.peak_level = max(self.peak_level, raw_level)
            self.recent_levels.append(raw_level)
            self.all_levels.append(raw_level)
            level = float(np.sqrt(np.mean(np.square(fb))))
            self.history.append(level)
            voiced = len(self.history) >= self.min_history and level > self._threshold()
            self.recent_voiced.append(voiced)
            if not voiced:
                self.noise_levels.append(raw_level)

            if not self._voiced:
                if voiced:
                    pre = list(self.preroll)
                    self._reset_chunk(pre + [f], [0.0] * len(pre) + [level])
                    self.preroll.clear()
                    self._voiced, self._silent_run = True, 0
                else:
                    self.preroll.append(f)
                continue

            self._append(f, level)
            self._silent_run = 0 if voiced else self._silent_run + 1
            length = self._len
            if self._silent_run >= self.pause_frames and length >= self.min_len:
                done.append(self._emit())
            elif length >= self.max_len:
                done.append(self._soft_cut())
        return done

    def flush(self) -> np.ndarray | None:
        """Whatever speech is left when recording stops (None if nothing useful)."""
        with self._lock:
            return self._flush()

    def _flush(self) -> np.ndarray | None:
        if self._rest.size and self._voiced:
            self._append(self._rest, 0.0)
        self._rest = np.zeros(0, dtype="float32")
        self._rest_band = np.zeros(0, dtype="float32")
        if not self._voiced or self._len < int(self.sr * 0.3):
            self._reset_chunk()
            self._voiced, self._silent_run = False, 0
            return None
        return self._emit()


def _ask_copy() -> bool:
    try:
        answer = prompt("📋  ¿Copiar al portapapeles? [S/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return False
    return answer in ("", "s", "si", "sí", "y", "yes")


class LiveSession:
    """One worker thread owns the model; chunks go through a queue."""

    PARTIAL_MIN_S = 0.8    # don't guess on less audio than this
    PARTIAL_STEP_S = 0.6   # re-transcribe once this much new audio arrived

    def __init__(self, transcribe_fn, rules, apply_fn, model: str, lang: str,
                 warm: bool = True, echo: bool = True, partial_source=None,
                 partial_lang=None, on_commit=None, warn: bool = True):
        self._transcribe = transcribe_fn
        self._apply = apply_fn
        self._rules = rules
        self._model, self._lang = model, lang
        # --lang auto: the session remembers its language between chunks (sticky, see
        # stt.LangState); only final chunks update it
        self._lang_state = LangState() if lang == "auto" else None
        self._q: queue.Queue = queue.Queue()
        self.parts: list[str] = []
        self.error: Exception | None = None  # fatal: model could not load
        self.failed_chunks = 0
        self.gated = 0                       # chunks too quiet to transcribe (silence gate)
        self._warn = warn                    # False in the TTY UI: it shows failed_chunks instead
        self._warm = warm
        self._echo = echo                    # print committed text to stdout as it comes
        self._partial_source = partial_source  # callable -> in-progress audio or None
        # callable -> language for partial guesses of a FIXED --lang (kept for callers).
        # With --lang auto partials never detect: they use the session's sticky language,
        # or the primary one until the first chunk is committed.
        self._partial_lang = partial_lang
        self._on_commit = on_commit          # e.g. autosave: each committed chunk to disk
        self.partial = ""                    # provisional text of the chunk in progress
        self._partial_len = 0
        self._partial_cost = 0.0             # seconds the last partial took
        self.closing = False
        self.abandoned = False               # chunks left untranscribed (closing timed out / forced)
        # Held while a result is committed and while `abandoned` flips: a result that arrives
        # after the session was given up on can never land in its (closed) recording.
        self._commit_lock = threading.Lock()
        self._hold = False                   # paused / finished: no partial guesses
        self._busy = threading.Lock()        # held while a partial guess runs
        self.ready = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        # warm-up: load the model while the user is still pressing Enter. Whatever
        # happens, `ready` gets set - a failed load must never leave the UI waiting.
        try:
            if self._warm:
                # fixed language: detecting on silence just hallucinates ("Thank you.")
                warm_lang = "es" if self._lang == "auto" else self._lang
                self._transcribe(np.zeros(8000, dtype="float32"),
                                 model=self._model, language=warm_lang)
        except Exception as e:  # noqa: BLE001 - surface any load failure
            self.error = e
        finally:
            self.ready.set()
        while True:
            try:
                chunk = self._q.get(timeout=0.05)
            except queue.Empty:
                if self.error is None and not self.closing:
                    self._maybe_partial()
                continue
            if chunk is None:
                self._q.task_done()
                break
            try:
                if self.error is None and not self.abandoned:   # given up on: nothing more is committed
                    self._handle(chunk)
            except Exception as e:  # noqa: BLE001 - e.g. a broken correction rule
                self.failed_chunks += 1
                if self._warn:
                    print(f"\n⚠  Error procesando un tramo ({type(e).__name__}: {e}); sigo.", file=sys.stderr)
            finally:
                self._q.task_done()

    def _maybe_partial(self) -> None:
        if self._partial_source is None or self._hold:
            return
        with self._busy:
            try:
                self._guess()
            except Exception:  # noqa: BLE001 - a failed guess must never kill the worker
                pass

    def _guess(self) -> None:
        audio = self._partial_source()
        if audio is None:
            return
        n = len(audio)
        if not gate(audio)[0]:
            return   # same gate as final chunks: no provisional text invented on silence
        # wait for at least as much new audio as the last guess cost (×1.2), so the
        # worker never spends all its time re-guessing and finals never queue up
        step = max(self.PARTIAL_STEP_S, 1.2 * self._partial_cost)
        if n < self.PARTIAL_MIN_S * SAMPLE_RATE or n - self._partial_len < step * SAMPLE_RATE:
            return
        # auto: partials never detect (a guess on short audio invents the other language).
        # They use the session's language, or the primary one until the first committed
        # chunk sets it; the session state itself is never touched here
        if self._lang_state is not None:
            lang = self._lang_state.current or self._lang_state.primary
        elif self._partial_len == 0 or not self._partial_lang:
            lang = self._lang
        else:
            lang = self._partial_lang()
        t0 = time.perf_counter()
        try:
            text = self._transcribe(audio, model=self._model, language=lang)
        except Exception:  # noqa: BLE001 - a failed guess is not worth reporting
            return
        self._partial_cost = time.perf_counter() - t0
        self._partial_len = n
        if self.abandoned:
            return
        self.partial = self._apply(text, self._rules).strip() if text else ""

    def _handle(self, chunk) -> None:
        secs = len(chunk) / SAMPLE_RATE
        ok, peak, floor, thr = gate(chunk)
        levels = f"secs={secs:.1f} peak={peak:.4f} floor={floor:.4f} thr={thr:.4f}"
        if not ok:
            self.gated += 1
            self.partial, self._partial_len = "", 0
            diag(f"gated {levels} lang={self._current_lang()}")
            return
        chunk = np.asarray(chunk)   # Whisper gets a plain array
        prev_lang = self._lang_state.current if self._lang_state is not None else None
        try:
            extra = {"lang_state": self._lang_state} if self._lang_state is not None else {}
            text = self._transcribe(chunk, model=self._model, language=self._lang, **extra)
        except Exception as e:  # noqa: BLE001 - one bad chunk must not kill the rest
            self.failed_chunks += 1
            if self._warn:
                print(f"\n⚠  No se pudo transcribir un tramo ({type(e).__name__}: {e}); sigo.",
                      file=sys.stderr)
            return
        text = self._apply(text, self._rules).strip() if text else ""
        self.partial, self._partial_len = "", 0
        loop = bool(text and self.parts and _repeats_last_sentence(text, self.parts[-1]))
        diag(f"{'accepted' if text and not loop else 'dropped'} {levels} "
             f"lang={self._current_lang()} chars={len(text)}{' loop' if loop else ''}")
        if not text or loop:
            # stt already moved the language when it kept segments; a chunk that is not
            # accepted here (empty after corrections, or a cross-chunk loop) must not keep it
            if self._lang_state is not None:
                self._lang_state.current = prev_lang
            return  # Whisper loop across chunks (music, long silence): keep the first copy
        with self._commit_lock:
            if self.abandoned:   # the transcriber came back after the recording was closed on it
                return
            sep = " " if self.parts else ""
            self.parts.append(text)
            if self._on_commit:
                try:
                    self._on_commit(text)
                except Exception:  # noqa: BLE001 - saving must never break transcription
                    pass
            if self._echo:
                sys.stdout.write(sep + text)
                sys.stdout.flush()

    def _current_lang(self) -> str:
        return (self._lang_state.current if self._lang_state is not None else self._lang) or "-"

    @property
    def committed(self) -> str:
        return " ".join(self.parts)

    def busy(self) -> bool:
        return self._worker.is_alive()

    @property
    def pending(self) -> int:
        """Chunks submitted and not yet transcribed (the one being worked on included)."""
        return self._q.unfinished_tasks

    def submit(self, chunk: np.ndarray) -> None:
        self._q.put(chunk)

    def hold(self) -> None:
        """Stop guessing partials (pause / finish). Final chunks keep flowing."""
        self._hold = True

    def release(self) -> None:
        """Resume partial guesses (resume / continue)."""
        self._partial_len, self._partial_cost = 0, 0.0
        self._hold = False

    def _give_up(self) -> None:
        with self._commit_lock:
            self.abandoned = True

    def abandon(self) -> None:
        """Give up on the chunks still pending (the user forced the exit)."""
        self._give_up()

    def drain(self, timeout: float | None = None) -> bool:
        """Wait until every submitted chunk is transcribed, WITHOUT ending the worker
        (so the same recording can be continued later). Clears the partial text.
        Returns True when everything was transcribed. Returns False (and sets
        `abandoned`) when `timeout` seconds pass, abandon() is called, or the worker
        died: a stuck transcriber must never keep the user from leaving."""
        self.hold()
        deadline = None if timeout is None else time.monotonic() + timeout
        while self._q.unfinished_tasks and self._worker.is_alive() and not self.abandoned:
            if deadline is not None and time.monotonic() > deadline:
                self._give_up()
            time.sleep(0.02)
        if self._q.unfinished_tasks:          # timed out, abandoned, or the worker died
            self._give_up()
        # an in-flight partial guess finishes first (it can be the stuck one too)
        while not self.abandoned:
            if self._busy.acquire(timeout=0.05):
                self._busy.release()
                break
            if deadline is not None and time.monotonic() > deadline:
                self._give_up()
        self.partial = ""
        return not self.abandoned

    def close(self) -> None:
        """Stop guessing partials and let the worker drain the final chunks."""
        self.closing = True
        self._q.put(None)

    def finish(self, timeout: float | None = None) -> str:
        """Close and wait for the worker (`timeout` seconds, None = until it ends; not at
        all once the session was abandoned). A worker still alive afterwards is a daemon
        thread stuck in native code: voz.py then leaves via os._exit."""
        if not self.closing:
            self.close()
        # A warm-up still loading/downloading the model can take minutes: don't make
        # the user wait to quit. The thread is a daemon; voz.py exits hard if it lingers.
        if not self.ready.is_set():
            wait = 0.2
        else:
            wait = 0 if self.abandoned else timeout
        self._worker.join(wait)
        if self._worker.is_alive():
            from record import request_hard_exit

            if not self.ready.is_set():
                request_hard_exit("el modelo seguía cargando")
            elif wait is not None:
                self._give_up()
                request_hard_exit("quedó una transcripción pendiente sin terminar")
        if self.parts and self._echo:
            sys.stdout.write("\n")
            sys.stdout.flush()
        return " ".join(self.parts).strip()


def _loop_key(text: str) -> str:
    import re

    return re.sub(r"[^\w\s]", "", text).strip().lower()


def _repeats_last_sentence(text: str, previous: str) -> bool:
    """True when a chunk only repeats the sentence that closed the previous chunk.
    The in-chunk loop filter (stt._collapse_repeats) cannot see this case because
    each copy arrives in its own chunk ("I'm going to put it in the bag." ×20 on
    background music). Short replies said twice on purpose ("Sí." / "Sí.") and literal
    dictation ("1 1", "A A") are never treated as a loop: it needs 3+ words."""
    import re

    key = _loop_key(text)
    words = key.split()
    if len(words) < 3 or all(len(w) == 1 or w.isdigit() for w in words):
        return False
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", previous.strip()) if s]
    return bool(sentences) and _loop_key(sentences[-1]) == key


def record_live(session: LiveSession, wait_start: bool = True):
    """Record with live transcription. Returns (text, peak_level) or None if cancelled."""
    if wait_start:
        try:
            prompt("▶️  Enter para EMPEZAR a hablar  (Ctrl-C cancela) ")
        except (KeyboardInterrupt, EOFError):
            print("\n✖  Cancelado.", file=sys.stderr)
            return None
    if not session.ready.is_set():
        print("⏳  Cargando modelo...", file=sys.stderr)
        session.ready.wait()
    if session.error is not None:
        print(f"✖  No se pudo cargar el modelo: {session.error}\n"
              "   Prueba `voz doctor` para ver qué falta.", file=sys.stderr)
        return None

    import autosave

    seg = Segmenter()
    active = [True]
    rec = autosave.Session()
    session._on_commit = rec.commit

    def callback(indata, frames, time_info, status):
        if not active[0]:
            return
        if status:
            print(f"⚠  {status}", file=sys.stderr)
        block = indata[:, 0].copy()
        rec.write_audio(block)
        for chunk in seg.feed(block):
            session.submit(chunk)

    stream = open_input(callback)
    try:
        prompt("🔴 Escuchando: el texto aparece abajo. Enter para TERMINAR (Ctrl-C cancela)\n")
    except (KeyboardInterrupt, EOFError):
        active[0] = False
        stop_stream(stream)
        _flush_and_close(seg, session, rec)
        print("\n✖  Cancelado. Lo dicho quedó guardado.", file=sys.stderr)
        return None
    active[0] = False
    stop_stream(stream)
    _flush_and_close(seg, session, rec)

    return seg.peak_level


def untranscribed(pending: int) -> str:
    """'2 tramos sin transcribir' ('tramos sin transcribir' when the count is unknown)."""
    if not pending:
        return "tramos sin transcribir"
    return f"{pending} tramo{'' if pending == 1 else 's'} sin transcribir"


def incomplete_notice(wav, pending: int) -> str:
    """What to tell the user when closing gave up on untranscribed chunks."""
    return (f"⚠  Cierre incompleto: quedaron {untranscribed(pending)}. El audio está guardado en {wav}\n"
            f"   Para transcribirlo entero:  voz \"{wav}\"  (el próximo voz también te lo recordará)")


def _flush_and_close(seg, session, rec) -> bool:
    """Last chunk in, every chunk transcribed (and committed to the .txt), THEN close
    the autosave (closing first silently dropped the tail of the .txt). Waits at most
    FINISH_TIMEOUT_S (a second Ctrl-C also gives up): on a stuck transcriber the WAV is
    kept and the recording stays marked unfinished, so the next `voz` offers it.
    Returns True when everything was transcribed."""
    last = seg.flush()
    if last is not None:
        session.submit(last)
    try:
        done = session.drain(timeout=FINISH_TIMEOUT_S)
    except KeyboardInterrupt:
        session.abandon()
        done = False
    # a chunk that failed is as untranscribed as one given up on: the WAV holds audio the text lacks
    failed = getattr(session, "failed_chunks", 0)
    done = done and not failed
    pending = session.pending or failed
    rec.close(clean=done)
    if not done:
        print(incomplete_notice(rec.wav_path, pending), file=sys.stderr)
    return done


def decide_copy(args) -> bool:
    if args.no_copy:
        return False
    if args.yes or not sys.stdin.isatty():
        return True
    return _ask_copy()
