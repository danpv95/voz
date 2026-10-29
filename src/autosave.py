"""Crash-safe recording: audio and committed text hit the disk while you talk.

Before this, a live session kept everything in memory until the end, so a freeze,
a kill or a power cut lost a whole real meeting (2026-10-07). Now each session
writes, as it goes:

    ~/.local/state/voz/sesion-YYYYmmdd-HHMMSS-gN.wav   recording N of that session (16 kHz mono PCM)
    ~/.local/state/voz/sesion-YYYYmmdd-HHMMSS-gN.txt   one line per committed chunk
    ~/.local/state/voz/sesion-YYYYmmdd-HHMMSS-gN.lock  exists only while open (holds the pid)

A .lock left behind by a dead process means the recording did not end cleanly; the
next `voz` reports it. Only the last KEEP *sessions* (all their recordings) are kept,
and the session in progress is never pruned (a 6th `r` must not delete the 1st).

The audio callback only enqueues; a writer thread does the file I/O, so disk
latency can never make the microphone drop samples.

Failures are tracked apart: a broken .txt never stops the WAV, nor the other way
round (`text_failed` / `audio_failed`, kept until close). The .lock is removed only
when the writer thread ended cleanly and nothing failed; otherwise it stays so the
next `voz` offers the session as recoverable.

Privacy: the state directory is 0700 and every file 0600, whatever the umask.
Two `voz` running at once never share a name: the .lock and the .wav are created
with O_EXCL, and on a collision the session takes the next free suffix.
"""
from __future__ import annotations

import fcntl
import os
import queue
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

STATE_DIR = Path(os.environ.get("VOZ_STATE_DIR", Path.home() / ".local" / "state" / "voz"))
KEEP = 5
SAMPLE_RATE = 16000
_RUN_RE = re.compile(r"sesion-\d{8}-\d{6}(-\d+)?")   # what new_run_id() produces


def new_run_id() -> str:
    """Prefix shared by every recording of one `voz` run. Unique even when two runs
    start in the same second (else the 2nd would truncate the 1st's .wav)."""
    base = time.strftime("sesion-%Y%m%d-%H%M%S")
    rid, k = base, 1
    while STATE_DIR.exists() and any(STATE_DIR.glob(f"{rid}-g*")):
        k += 1
        rid = f"{base}-{k}"
    return rid


def _run_of(stem_name: str) -> str:
    """'sesion-20261008-101500-g3' → 'sesion-20261008-101500' (legacy names map to themselves)."""
    head, sep, tail = stem_name.rpartition("-g")
    return head if sep and tail.isdigit() else stem_name


MAX_NAME_TRIES = 50


_PERM_WARNED = False


def _warn_permissions(what: str, err: OSError) -> None:
    """Tightening permissions failed (odd filesystem): say so ONCE per run, never abort."""
    global _PERM_WARNED
    if _PERM_WARNED:
        return
    _PERM_WARNED = True
    print(f"⚠  No se pudieron restringir los permisos de {what} ({err}): "
          "tus grabaciones podrían ser legibles por otros usuarios.", file=sys.stderr)


def _private_state_dir() -> None:
    """The state directory holds raw recordings: owner only, whatever the umask."""
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError as e:
        _warn_permissions(str(STATE_DIR), e)


def _open_private(path: Path, flags: int) -> int:
    """os.open with mode 0600 that also holds when the file already existed (umask-proof)."""
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError as e:
        _warn_permissions(str(path), e)
    return fd


def _candidates(base: Path | None, run_id: str | None, take: int):
    """Names to try, in order: the wanted one, then the same with a -2, -3… suffix."""
    if base is not None:
        yield base
        for k in range(2, MAX_NAME_TRIES + 1):
            yield base.with_name(f"{base.name}-{k}")
        return
    run = run_id or new_run_id()
    yield STATE_DIR / f"{run}-g{take}"
    for k in range(2, MAX_NAME_TRIES + 1):
        yield STATE_DIR / f"{run}-{k}-g{take}"


def _flock_retry(fd: int) -> bool:
    for _ in range(5):                       # a probe from another voz may hold it for ms
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            time.sleep(0.02)
    return False


def _reserve(base: Path | None, run_id: str | None, take: int):
    """Atomically claim a session name: the .lock and the .wav are created with O_EXCL,
    so two `voz` starting together (or a leftover from a crashed run) can never share
    or truncate each other's files. Returns (lock_fd, wav_file, base)."""
    for cand in _candidates(base, run_id, take):
        lock, wav = cand.with_suffix(".lock"), cand.with_suffix(".wav")
        try:
            lock_fd = _open_private(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            continue
        # the OS releases a flock when the process dies, however it dies: that (not the
        # pid, which gets reused) is what says "this recording is still open"
        wav_fd = None
        if _flock_retry(lock_fd):
            try:
                wav_fd = _open_private(wav, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                pass
            except OSError:
                os.close(lock_fd)
                lock.unlink(missing_ok=True)
                raise
        if wav_fd is None:                   # taken by someone else: leave their files alone
            os.close(lock_fd)
            lock.unlink(missing_ok=True)
            continue
        return lock_fd, os.fdopen(wav_fd, "wb"), cand
    raise OSError(f"no se pudo reservar un nombre de sesión libre en {STATE_DIR}")


class Session:
    """One recording on disk. Thread-safe: write_audio() from the audio thread,
    commit() from the transcription worker, close() from the main thread."""

    def __init__(self, base: Path | None = None, sr: int = SAMPLE_RATE,
                 run_id: str | None = None, take: int = 1):
        _private_state_dir()
        self._lock_fd, self._wav_file, self.base = _reserve(base, run_id, take)
        self.run_id = _run_of(self.base.name)   # may differ from `run_id` after a collision
        self.wav_path = self.base.with_suffix(".wav")
        self.txt_path = self.base.with_suffix(".txt")
        self.lock_path = self.base.with_suffix(".lock")
        os.write(self._lock_fd, str(os.getpid()).encode())
        self._wav = wave.open(self._wav_file, "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(sr)
        self._txt = os.fdopen(_open_private(self.txt_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND),
                              "a", encoding="utf-8")
        self._q: queue.Queue = queue.Queue()
        self._closed = False
        # disk full / I/O error. Kept apart: the audio is the irreplaceable part and must
        # keep flowing when only the .txt breaks (and the text when the audio breaks)
        self.audio_failed: Exception | None = None   # stop queueing audio, tell the UI
        self.text_failed: Exception | None = None
        self._writer = threading.Thread(target=self._run, name="voz-autosave", daemon=True)
        self._writer.start()

    def _run(self) -> None:
        last_sync = time.monotonic()
        while True:
            block = self._q.get()
            if block is None:
                break
            if self.audio_failed is not None:
                continue                    # keep draining so the queue never grows
            try:
                pcm = (np.clip(block, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                self._wav.writeframesraw(pcm)
                if time.monotonic() - last_sync > 1.0:  # keep the header (length) current
                    self._sync_header()
                    last_sync = time.monotonic()
            except Exception as e:  # noqa: BLE001 - disk full, I/O error…
                self.audio_failed = e
        # the writer owns the wav: it closes it (never two threads on one wave object)
        try:
            self._wav.close()
        except Exception as e:  # noqa: BLE001
            self.audio_failed = self.audio_failed or e
        try:
            self._wav_file.close()          # wave does not close a file object it was handed
        except OSError as e:
            self.audio_failed = self.audio_failed or e

    @property
    def failed(self) -> Exception | None:
        """First failure of either kind (the UI shows a warning while it is set)."""
        return self.audio_failed or self.text_failed

    def _sync_header(self) -> None:
        f = self._wav._file  # rewrite the RIFF sizes so a crash still leaves a valid .wav
        pos = f.tell()
        self._wav._patchheader()
        f.seek(pos)
        f.flush()

    def write_audio(self, block) -> None:
        """Called from the audio callback: never blocks."""
        if not self._closed and self.audio_failed is None:
            self._q.put(np.asarray(block, dtype="float32").ravel().copy())

    def commit(self, text: str) -> None:
        if text and not self._txt.closed:
            try:
                self._txt.write(text.strip() + "\n")
                self._txt.flush()
            except (OSError, ValueError) as e:     # ValueError: closed file
                self.text_failed = self.text_failed or e

    def close(self, clean: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._q.put(None)
        self._writer.join(timeout=5)   # if it times out, the writer still closes the wav itself
        writer_done = not self._writer.is_alive()
        try:
            self._txt.close()
        except OSError as e:
            self.text_failed = self.text_failed or e
        # the lock goes only when everything ended well: a writer still alive or any
        # failure leaves it, so the next `voz` reports the session as recoverable
        if clean and writer_done and self.failed is None:
            self.lock_path.unlink(missing_ok=True)
        try:
            os.close(self._lock_fd)        # releases the flock
        except OSError:
            pass
        prune(current_run=_run_of(self.base.name))

    def mark(self, note: str) -> None:
        """Append a marker line (e.g. "[descartada]") - works even after close()."""
        try:
            with os.fdopen(_open_private(self.txt_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND),
                           "a", encoding="utf-8") as f:
                f.write(note.strip() + "\n")
        except OSError as e:
            self.text_failed = self.text_failed or e


def _lock_held(lock: Path) -> bool | None:
    """True if some process holds the flock (recording open). None = no lock file."""
    try:
        fd = os.open(lock, os.O_RDONLY)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _locked_by_live_process(stem: Path) -> bool:
    return bool(_lock_held(stem.with_suffix(".lock")))


def prune(keep: int = KEEP, current_run: str | None = None) -> None:
    """Keep the newest `keep` runs with ALL their recordings. Never touches
    `current_run` nor any run that still has a recording open by a live process."""
    if not STATE_DIR.exists():
        return
    stems = {p.with_suffix("") for p in STATE_DIR.glob("sesion-*.wav")}
    runs: dict[str, list[Path]] = {}
    for st in stems:
        run = _run_of(st.name)
        if _RUN_RE.fullmatch(run):           # a hand-named "sesion-manual.wav" is not ours
            runs.setdefault(run, []).append(st)
    for run in sorted(runs, reverse=True)[keep:]:
        if run == current_run or any(_locked_by_live_process(st) for st in runs[run]):
            continue
        for st in runs[run]:
            for ext in (".wav", ".txt", ".lock"):
                try:
                    st.with_suffix(ext).unlink(missing_ok=True)
                except OSError:
                    pass


def unfinished() -> list[Path]:
    """Sessions whose recorder died without closing (lock left by a dead pid)."""
    if not STATE_DIR.exists():
        return []
    out = []
    for lock in sorted(STATE_DIR.glob("sesion-*.lock")):
        if _lock_held(lock) is False:      # nobody holds it: its recorder is gone
            out.append(lock.with_suffix(""))
    return out


def report_unfinished(stream=None) -> None:
    """Print a short notice about sessions that ended badly, then mark them seen."""
    stream = stream or sys.stderr
    for stem in unfinished():
        wav, txt = stem.with_suffix(".wav"), stem.with_suffix(".txt")
        secs = 0.0
        try:
            with wave.open(str(wav)) as w:
                secs = w.getnframes() / w.getframerate()
        except Exception:  # noqa: BLE001 - a truncated file is still worth pointing at
            pass
        if wav.exists():
            print(f"⚠  La sesión {stem.name} no terminó bien ({secs / 60:.1f} min guardados).\n"
                  f"   Audio: {wav}\n   Texto: {txt}\n"
                  f"   Re-transcribir completo:  voz \"{wav}\"", file=stream)
        stem.with_suffix(".lock").unlink(missing_ok=True)  # report once
