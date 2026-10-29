"""Push-to-talk microphone capture using the system default input device.

sounddevice bundles PortAudio in its macOS wheels, so no `brew install portaudio`
is needed. It follows the system default input device dynamically (AirPods, etc.),
so we never have to discover device indices.
"""
from __future__ import annotations

import sys
import threading

import numpy as np

SAMPLE_RATE = 16000  # what Whisper expects; CoreAudio resamples transparently


STOP_TIMEOUT_S = 1.5
_STREAM_STUCK = threading.Event()
_HARD_EXIT: list[str] = []          # reasons to skip interpreter teardown (os._exit)
_STOPPING: set[int] = set()         # streams with a stop already in flight


MIC_PROBLEM = "No llega audio del micrófono"
MIC_HELP = "Revisa la entrada y el permiso en Ajustes del Sistema → Privacidad y seguridad → Micrófono"


class MicError(RuntimeError):
    """The input stream could not be opened (permission denied, no input device, busy)."""


def open_input(callback, samplerate: int = SAMPLE_RATE, refresh: bool = False):
    """Open + start a mono float32 input stream on the default device.
    `refresh` makes PortAudio look the devices up again first (a retry after the user
    plugged a microphone in or changed the input), unless a stream is stuck in the driver.
    Any failure is raised as MicError, never a raw PortAudio exception."""
    import sounddevice as sd

    if refresh and not _STREAM_STUCK.is_set():
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001 - private API: if it is gone, opening anyway is still right
            pass
    try:
        stream = sd.InputStream(samplerate=samplerate, channels=1, dtype="float32", callback=callback)
        stream.start()
    except Exception as e:  # noqa: BLE001 - PortAudioError, ValueError (no device), OSError…
        raise MicError(str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__) from e
    return stream


def stop_stream(stream, timeout: float = STOP_TIMEOUT_S) -> bool:
    """Stop and close an input stream without ever hanging the caller.

    On macOS, Pa_StopStream can deadlock inside CoreAudio: the main thread waits in
    AudioOutputUnitStop → HALC StopIOProc → HALB_Mutex::Lock while CoreAudio's IO
    thread sits in PortAudio's startStopCallback → AudioUnitGetProperty, which needs
    that same mutex (seen with sample(1) on a frozen `voz`, 2026-10-07). It only
    happens when stopping a real mic, never with --sim. So: stop in a daemon thread
    and give up after `timeout`; the transcript is unaffected. Returns False if the
    driver did not let go (then the process must exit via os._exit, see voz.py).
    """
    if id(stream) in _STOPPING:      # never two concurrent stop()s on one PortAudio stream
        return False
    _STOPPING.add(id(stream))

    def run():
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001 - a failing close must not take the app down
            pass
        finally:
            # done: forget the id (CPython reuses ids of freed objects, and a new stream
            # with a recycled id would otherwise be skipped). A hung stop keeps it - and
            # keeps the object alive, so its id can't be reused.
            _STOPPING.discard(id(stream))

    t = threading.Thread(target=run, name="voz-stop-mic", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        _STREAM_STUCK.set()
        return False
    return True


def stream_stuck() -> bool:
    """True if some stream never finished stopping (PortAudio would hang at exit)."""
    return _STREAM_STUCK.is_set()


def request_hard_exit(reason: str) -> None:
    """Something (e.g. a model still loading in a native thread) would make normal
    interpreter teardown hang or crash: voz.py will leave via os._exit."""
    _HARD_EXIT.append(reason)


def hard_exit_reasons() -> list[str]:
    out = list(_HARD_EXIT)
    if _STREAM_STUCK.is_set():
        out.insert(0, "el driver de audio de macOS no soltó el micrófono")
    return out


def prompt(msg: str) -> str:
    """input() that writes its prompt to stderr: stdout carries ONLY the transcript,
    so `voz | pbcopy` never captures UI text."""
    print(msg, end="", file=sys.stderr, flush=True)
    return input()


def record_push_to_talk(samplerate: int = SAMPLE_RATE, wait_start: bool = True):
    """Explicit record/stop toggle. Both stages are one Enter; Ctrl-C cancels.

    Flow:  ▶️  Enter → 🔴 recording → Enter → stop.
    Returns a float32 mono np.ndarray, or None if cancelled / nothing captured.
    """
    if wait_start:
        try:
            prompt("▶️  Enter para EMPEZAR a grabar  (Ctrl-C cancela) ")
        except (KeyboardInterrupt, EOFError):
            print("\n✖  Cancelado.", file=sys.stderr)
            return None

    import autosave

    chunks: list[np.ndarray] = []
    active = [True]  # once False, late callbacks are ignored while the stream stops
    rec = autosave.Session()

    def callback(indata, frames, time_info, status):
        if not active[0]:
            return
        if status:
            print(f"⚠  {status}", file=sys.stderr)
        chunks.append(indata.copy())
        rec.write_audio(indata[:, 0])

    stream = open_input(callback, samplerate)
    try:
        prompt("🔴 GRABANDO... Enter para PARAR y transcribir  (Ctrl-C cancela) ")
    except (KeyboardInterrupt, EOFError):
        active[0] = False
        stop_stream(stream)
        rec.close()
        print("\n✖  Cancelado.", file=sys.stderr)
        return None
    active[0] = False
    stop_stream(stream)
    rec.close()

    if not chunks:
        return None
    return np.concatenate(chunks)[:, 0]


# Voice-activity threshold, shared by trim_silence (whole recording) and
# live.Segmenter (rolling). Tuned on a noise bench (white/pink/street at 30-5 dB
# SNR): the old max(1e-3, min(p10*3, p99*0.02)) sat below moderate background
# noise, so nothing ever looked silent. Energy is measured on a 100-4000 Hz
# band-passed copy; Whisper always receives the original audio.
#
# Two profiles of the same rule, because the two callers fail differently:
#  - LIVE (Segmenter): a threshold below the noise merges everything into one
#    chunk or cuts mid-sentence at 25 s → must be noise-robust.
#  - TRIM (trim_silence): a threshold below the noise just trims nothing - the
#    safe failure - so it keeps the soft-onset-friendly profile.
BAND_HZ = (100, 4000)
LIVE_PROFILE = (3e-3, 1.5, 0.08)   # (min_abs, floor_mult, peak_frac)
TRIM_PROFILE = (1e-3, 3.0, 0.02)


def adaptive_threshold(levels, profile=LIVE_PROFILE) -> float:
    min_abs, floor_mult, peak_frac = profile
    levels = np.asarray(levels, dtype="float32")
    if levels.size == 0:
        return min_abs
    floor = float(np.percentile(levels, 10))
    peak = float(np.percentile(levels, 99))
    return max(min_abs, min(floor * floor_mult, peak * peak_frac))


def voice_band_sos(sr: int = SAMPLE_RATE):
    from scipy.signal import butter

    return butter(4, BAND_HZ, btype="band", fs=sr, output="sos")


def trim_silence(audio, sr: int = SAMPLE_RATE, win_ms: int = 20, pad_ms: int = 300):
    """Trim leading/trailing silence using a threshold relative to this recording.

    Windows are measured on the voice band (100-4000 Hz) and compared with
    adaptive_threshold() - see the constants above. Only the edges are trimmed,
    never the middle; the 300 ms pad protects soft onsets. Returns an empty array
    when no window is voice.
    """
    win = max(1, int(sr * win_ms / 1000))
    n = len(audio) // win
    if n == 0:
        return audio
    from scipy.signal import sosfilt

    band = sosfilt(voice_band_sos(sr), np.asarray(audio, dtype="float32"))
    frames = band[: n * win].reshape(n, win)
    levels = np.sqrt(np.mean(np.square(frames), axis=1))
    threshold = adaptive_threshold(levels, TRIM_PROFILE)
    voiced = np.nonzero(levels > threshold)[0]
    if len(voiced) == 0:
        return audio[:0]
    pad = int(sr * pad_ms / 1000)
    start = max(0, int(voiced[0]) * win - pad)
    end = min(len(audio), (int(voiced[-1]) + 1) * win + pad)
    return audio[start:end]


def split_on_silence(audio, sr: int = SAMPLE_RATE, max_s: float = 25.0,
                     min_gap_s: float = 0.4, win_ms: int = 30,
                     every_gap: bool = False) -> list[tuple[int, int]]:
    """Split a long recording into (start, end) sample ranges of at most `max_s`,
    cutting in the middle of silences (≥ min_gap_s) - never mid-word unless a
    stretch of speech is longer than `max_s`, in which case it cuts at the quietest
    window of its last 3 s. With every_gap=True it cuts at EVERY silence (one piece
    per phrase), still never exceeding max_s. Used for per-phrase language detection.
    """
    n = len(audio)
    if n == 0:
        return []
    win = max(1, int(sr * win_ms / 1000))
    from scipy.signal import sosfilt

    band = sosfilt(voice_band_sos(sr), np.asarray(audio, dtype="float32"))
    k = n // win
    levels = np.sqrt(np.mean(np.square(band[: k * win].reshape(k, win)), axis=1)) if k else np.zeros(0)
    silent = levels <= adaptive_threshold(levels, LIVE_PROFILE) if k else np.zeros(0, bool)
    gap = max(1, int(min_gap_s * 1000 / win_ms))
    # candidate cut points = centres of silent runs of at least `gap` windows
    cuts, run = [], 0
    for i, sil in enumerate(silent):
        run = run + 1 if sil else 0
        if run == gap:
            cuts.append(i - gap // 2)
    max_w = max(1, int(max_s * 1000 / win_ms))
    look = max(1, int(3000 / win_ms))
    ranges, start = [], 0
    if every_gap:
        for c in cuts:
            if c > start:
                while c - start > max_w:  # a phrase longer than max_s
                    lo = max(start + 1, start + max_w - look)
                    cut = lo + int(np.argmin(levels[lo:start + max_w]))
                    ranges.append((start * win, cut * win))
                    start = cut
                ranges.append((start * win, c * win))
                start = c
    while (k - start) > max_w:
        inside = [c for c in cuts if start < c <= start + max_w]
        if inside:
            cut = inside[-1]
        else:  # continuous speech: quietest window of the last 3 s before the limit
            lo = max(start + 1, start + max_w - look)
            cut = lo + int(np.argmin(levels[lo:start + max_w]))
        ranges.append((start * win, cut * win))
        start = cut
    ranges.append((start * win, n))
    return [(a, b) for a, b in ranges if b > a]


def rms(audio) -> float:
    """Root-mean-square level - near zero means silence (likely no mic permission)."""
    if audio is None or len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))
