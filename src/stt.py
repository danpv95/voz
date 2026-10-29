"""mlx-whisper wrapper: transcribe audio (np.ndarray or file path) to text.

Fully local, offline, no API keys. The heavy import is deferred so `voz devices`
and `--help` stay instant.
"""
from __future__ import annotations

import functools
import os
import sys
import threading
import time
from pathlib import Path

# Default model: turbo is ~5x faster than large-v3 with ~0.4pp worse WER - the
# right trade-off for dictation. Escalate to whisper-large-v3-mlx for critical
# meeting recordings via --model.
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
ACCURATE_MODEL = "mlx-community/whisper-large-v3-mlx"

# Approximate download size in bytes (decimal), shown by the UI and `voz doctor`.
MODEL_SIZES = {DEFAULT_MODEL: 1_614_000_000, ACCURATE_MODEL: 3_000_000_000}
WEIGHT_FILES = ("weights.safetensors", "weights.npz")   # what mlx-whisper loads, in this order

# Whisper's classic Spanish silence hallucinations. Filtered ONLY when a whole
# segment IS one of these and reads as non-speech, so a real dictated "Gracias."
# survives.
JUNK_PHRASES = {
    "gracias",
    "gracias por ver el video",
    "gracias por ver el vídeo",
    "suscríbete",
    "subtítulos realizados por la comunidad de amara.org",
    "subtítulos por la comunidad de amara.org",
    "subtitulado por la comunidad de amara.org",
    "amara.org",
    "www.amara.org",
    # English ones (--lang auto detects silence as English and Whisper says these)
    "thank you",
    "thanks for watching",
    "thank you for watching",
    "you",
    "bye",
}
_JUNK = {p.lower().strip(" ¡!¿?.") for p in JUNK_PHRASES}


# --lang auto: detect only among these (short chunks otherwise get mistaken for
# Portuguese/Galician). Live sessions keep a sticky language (LangState); files are
# detected per phrase (transcribe_file_segments). Detection reuses the cached model.
AUTO_LANGUAGES = ("es", "en")
last_language: str | None = None  # language of the most recent transcription (for the UI)

CACHE_LIMIT = 1 << 30  # 1 GB of cached MLX buffers before freeing them (long files)

SILENT_INPUT_RMS = 0.01  # measured: real speech chunk RMS ~0.1, silence hallucination ~0.001
NO_VOICE_RMS = 0.003     # a file piece below this is room tone / digital silence: never transcribed


def _is_silent(audio) -> bool:
    """True only for in-memory audio that is near-silent. File paths → unknown → False."""
    import numpy as np

    if not isinstance(audio, np.ndarray) or audio.size == 0:
        return False
    return float(np.sqrt(np.mean(np.square(audio)))) < SILENT_INPUT_RMS


LOOP_COMPRESSION = 2.4  # Whisper's own repetition signal (gzip ratio of the segment text)


def _norm_piece(p: str) -> str:
    import re

    return re.sub(r"[^\w\s]", "", p).strip().lower()


def _collapse_repeats(text: str, min_repeats: int = 3) -> str:
    """Whisper loop hallucination: the same sentence N times in a row ("I'm going to
    put a little bit on the bag." ×12 on background noise, seen 2026-10-07).
    Keep one copy when a sentence repeats ≥ min_repeats times consecutively
    (compared without punctuation: "Gracias. Gracias! Gracias." is one loop), then
    do the same for word runs without sentence marks ("hola hola hola hola",
    "una caja, una caja, una caja")."""
    import re

    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p]
    keys = [_norm_piece(p) for p in parts]
    out, run = [], 0
    for i, p in enumerate(parts):
        same = i and keys[i] and keys[i] == keys[i - 1]
        run = run + 1 if same else 1
        if not same:
            out.append(p)
        elif run < min_repeats and (i + 1 >= len(parts) or keys[i + 1] != keys[i]):
            out.append(p)  # a genuine 2× repetition is kept
    return _collapse_word_runs(" ".join(out), min_repeats)


def _is_literal_token(key: str) -> bool:
    """Single characters and numbers are dictated literally ("1 1 1 1", "A A A A"),
    never a Whisper loop, so they are exempt from word-run collapsing."""
    return len(key) == 1 or key.isdigit()


def _collapse_word_runs(text: str, min_repeats: int = 3, max_n: int = 10) -> str:
    """n-gram (n ≤ max_n words) repeated ≥ min_repeats times in a row → one copy.
    Single words need one more repeat ("no, no, no" is real speech; ×4 is a loop).
    n-grams made only of single-character or numeric tokens are never collapsed,
    and the final punctuation of the last repetition is kept."""
    import re

    words = text.split()
    keys = [_norm_piece(w) for w in words]
    out: list[str] = []
    i = 0
    while i < len(words):
        for n in range(1, min(max_n, (len(words) - i) // min_repeats) + 1):  # smallest period first
            gram = keys[i:i + n]
            if not all(gram) or all(_is_literal_token(k) for k in gram):
                continue
            reps = 1
            while keys[i + reps * n:i + (reps + 1) * n] == gram:
                reps += 1
            if reps >= (min_repeats + 1 if n == 1 else min_repeats):
                first = words[i:i + n]
                first[-1] = first[-1].rstrip(",;")
                end = re.search(r"[.!?…]+$", words[i + reps * n - 1])
                if end and not first[-1].endswith(end.group()):
                    first[-1] = first[-1].rstrip(".!?…") + end.group()
                out.extend(first)
                i += reps * n
                break
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def _filter_segments(result: dict, audio=None) -> list[dict]:
    """Drop hallucinated / non-speech segments; keep {start, end, text} for the rest.

    A lone junk phrase ("Gracias.") has the SAME confidence fingerprint whether it
    was really said or hallucinated on silence; only the input energy separates
    them. With in-memory audio (mic) we drop it only if the audio was silent;
    for file paths we keep the old conservative rule (drop it).
    """
    import numpy as np

    in_memory = isinstance(audio, np.ndarray)
    segments = result.get("segments")
    if not segments:
        text = result.get("text", "").strip()
        return [{"start": 0.0, "end": None, "text": text}] if text else []

    n = len(segments)
    kept: list[dict] = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        no_speech = s.get("no_speech_prob", 0.0)
        avg_logprob = s.get("avg_logprob", 0.0)
        # Low-confidence non-speech - mlx rescues these by logprob, we don't.
        if no_speech > 0.5 and avg_logprob < -0.4:
            continue
        # Known junk phrase when the whole segment is just that. Confident
        # silence hallucinations have LOW no_speech_prob, so a lone one is dropped
        # unless the in-memory input clearly had voice (see docstring).
        # Whisper's repetition flag. mlx stamps compression_ratio on the whole decoding
        # WINDOW (up to 30 s), not on each segment, so dropping on it alone deleted real
        # dictation that shared the window. Drop only when it also looks like non-speech;
        # otherwise keep it and let _collapse_repeats remove the loop.
        if s.get("compression_ratio", 0.0) > LOOP_COMPRESSION and (
                (in_memory and _is_silent(audio)) or no_speech > 0.3):
            continue
        lone_junk = n == 1 and (not in_memory or _is_silent(audio))
        if text.lower().strip(" ¡!¿?.") in _JUNK and (no_speech > 0.3 or lone_junk):
            continue
        kept.append({"start": s.get("start", 0.0), "end": s.get("end"), "text": _collapse_repeats(text)})
    return kept


def _clean_segments(result: dict, audio=None) -> str:
    """Drop hallucinated / non-speech segments; join the rest."""
    return " ".join(s["text"] for s in _filter_segments(result, audio)).strip()


def _is_missing_model(exc: BaseException) -> bool:
    """Walk the cause chain looking for huggingface_hub's 'not in local cache' error."""
    try:
        from huggingface_hub.errors import LocalEntryNotFoundError
    except ImportError:  # pragma: no cover
        return False
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, LocalEntryNotFoundError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def format_size(n: float) -> str:
    """Bytes for people, decimal and with a Spanish comma: 420 MB, 1,6 GB, 3 GB."""
    if n < 999.5e6:
        return f"{round(n / 1e6)} MB"
    return f"{n / 1e9:.1f}".replace(".", ",").removesuffix(",0") + " GB"


def model_status(model: str, cache_dir: str | os.PathLike | None = None) -> str:
    """"ready" (config + weights in the local cache or folder), "incomplete" (something is
    there but not the weights: an interrupted download) or "missing". Never touches the network."""
    path = Path(model)
    if path.is_dir():
        has_config = (path / "config.json").is_file()
        has_weights = any((path / f).is_file() for f in WEIGHT_FILES)
        return "ready" if has_config and has_weights else "incomplete"
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.constants import HF_HUB_CACHE
        from huggingface_hub.file_download import repo_folder_name
    except ImportError:  # pragma: no cover
        return "missing"
    root = Path(cache_dir or HF_HUB_CACHE)

    def cached(name: str) -> bool:
        return isinstance(try_to_load_from_cache(model, name, cache_dir=root), str)

    try:
        if cached("config.json") and any(cached(f) for f in WEIGHT_FILES):
            return "ready"
        return "incomplete" if (root / repo_folder_name(repo_id=model, repo_type="model")).exists() else "missing"
    except Exception:  # noqa: BLE001 - not a valid repo id (e.g. a path that does not exist): not cached
        return "missing"


class DownloadProgress:
    """What the UI reads while a model downloads (written by the download thread).
    `done` stays 0 when the downloader reports no bytes: then the UI shows no figures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.quiet = False         # True: the TTY UI shows the progress, nothing is printed
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.model = ""
        self.done = 0              # bytes received so far
        self.total = 0             # bytes expected: the known size of the model, else what the downloader reports
        self._known = False
        self.started_at = 0.0      # time.monotonic()
        self.ended_at = 0.0

    def start(self, model: str) -> None:
        with self._lock:
            self.reset()
            self.active, self.model = True, model
            self.total = MODEL_SIZES.get(model, 0)
            self._known = bool(self.total)
            self.started_at = time.monotonic()

    def add(self, n: int, total: int = 0) -> None:
        with self._lock:
            self.done += int(n)
            if not self._known:
                self.total = max(self.total, int(total or 0))

    def finish(self) -> None:
        with self._lock:
            self.active = False
            self.ended_at = time.monotonic()


download = DownloadProgress()


def _progress_bar_class():
    """tqdm subclass that draws nothing and feeds the byte bar into `download`
    (snapshot_download reports bytes through it; its file-count bar is ignored)."""
    from tqdm.auto import tqdm

    class ByteCounter(tqdm):
        def __init__(self, *args, **kwargs):
            self._counts_bytes = kwargs.get("unit") == "B"
            kwargs["disable"] = True        # stderr belongs to the UI
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            if self._counts_bytes and n:
                download.add(n, self.total or 0)
            return True

    return ByteCounter


def download_model(model: str) -> None:
    """Download the whole snapshot (a partial one resumes), reporting into `download`.
    Offline mode is lifted only for the duration and restored afterwards, so loading
    the model stays 100% local."""
    from huggingface_hub import snapshot_download
    import huggingface_hub.constants as hfc

    if not download.quiet:
        size = MODEL_SIZES.get(model)
        print(f"⏳  Modelo no está en caché; descargando{f' ~{format_size(size)}' if size else ''} "
              "(solo la primera vez)...", file=sys.stderr)
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "0"
    offline_before = getattr(hfc, "HF_HUB_OFFLINE", None)
    hfc.HF_HUB_OFFLINE = False
    download.start(model)
    try:
        snapshot_download(repo_id=model, tqdm_class=_progress_bar_class())
    finally:
        download.finish()
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous
        if offline_before is not None:
            hfc.HF_HUB_OFFLINE = offline_before


def _pick_language(probs: dict, allowed=AUTO_LANGUAGES) -> str:
    return max(allowed, key=lambda lang: probs.get(lang, 0.0))


def detect_language_probs(audio, model: str = DEFAULT_MODEL, allowed=AUTO_LANGUAGES) -> dict:
    """Detector probabilities of `audio` (first 30 s) for the `allowed` languages.
    Raw values from the full distribution (not renormalized): es + en can sum < 1."""
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    m = ModelHolder.get_model(model, mx.float16)
    mel = log_mel_spectrogram(audio, n_mels=m.dims.n_mels, padding=N_SAMPLES)
    segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
    _, probs = m.detect_language(segment)
    probs = probs[0] if isinstance(probs, list) else probs
    return {lang: float(probs.get(lang, 0.0)) for lang in allowed}


# Sticky language for live sessions (--lang auto). Far-field or multi-voice audio makes
# the per-chunk detector flip to the other language on chunks that never left the first,
# and Whisper then invents sentences in it. A session keeps its current language unless
# the evidence is strong, and it leans towards the PRIMARY language (config [idioma]
# principal, else the system locale, else es):
#  - the first chunk seeds the primary unless the other language is clearly detected;
#  - leaving the primary needs strong evidence (SWITCH_*);
#  - coming back to it needs less (BACK_*), so a spurious departure heals quickly.
SWITCH_MIN = 0.80     # leave the primary if the new language alone reaches this probability...
SWITCH_MARGIN = 0.50  # ...or if it beats the current language by this much (e.g. en 0.70 vs es 0.15)
BACK_MIN = 0.60       # return to the primary with this probability...
BACK_MARGIN = 0.25    # ...or with this margin over the current language
_EPS = 1e-9           # probabilities are floats: 0.7 - 0.2 is 0.4999999999999999


@functools.lru_cache(maxsize=1)
def default_primary() -> str:
    """The session's primary language, resolved once per process (see config.primary_language)."""
    import config

    return config.primary_language()


class LangState:
    """Language memory of one live session. `choose` is pure; `current` is set only by
    committed chunks (partial guesses, errors and filtered-out chunks must not move it)."""

    def __init__(self, allowed=AUTO_LANGUAGES, primary: str | None = None):
        self.allowed = tuple(allowed)
        primary = primary or default_primary()
        self.primary = primary if primary in self.allowed else self.allowed[0]
        self.current: str | None = None

    def choose(self, probs: dict) -> str:
        best = _pick_language(probs, self.allowed)
        p_best = probs.get(best, 0.0)
        if self.current is None:
            # a weak first chunk (far-field, noise) is not evidence for the other language
            return best if best == self.primary or p_best >= SWITCH_MIN - _EPS else self.primary
        if best == self.current:
            return best
        p_cur = probs.get(self.current, 0.0)
        need_min, need_margin = (BACK_MIN, BACK_MARGIN) if best == self.primary else (SWITCH_MIN, SWITCH_MARGIN)
        if p_best >= need_min - _EPS or p_best - p_cur >= need_margin - _EPS:
            return best
        return self.current


def transcribe(audio, **kwargs) -> str:
    """Transcribe `audio` to plain text. See transcribe_segments() for options."""
    return " ".join(s["text"] for s in transcribe_segments(audio, **kwargs)).strip()


def transcribe_segments(
    audio,
    *,
    model: str = DEFAULT_MODEL,
    language: str = "es",
    initial_prompt: str | None = None,
    condition_on_previous_text: bool = False,
    dictation: bool = True,
    lang_state: LangState | None = None,
) -> list[dict]:
    """Transcribe `audio` to a list of kept segments ({start, end, text}).

    `audio` may be a file path (any format - mlx-whisper shells out to ffmpeg to
    decode + resample) or a float32 mono 16 kHz np.ndarray (fed directly, no disk).

    `initial_prompt` defaults to None: a vocab-biasing prompt gets regurgitated
    verbatim on silent/short clips (Whisper prompt-echo). Proper-noun fixes live
    in corrections.py instead. `dictation=True` tightens the silence threshold and
    trims the temperature fallback ladder that manufactures "Subtítulos por…".
    With language="auto" and a `lang_state` (live sessions), the language is sticky
    (see LangState) and the state takes the chosen language when text was kept.
    """
    global last_language
    import mlx_whisper  # lazy: loading MLX + model is the expensive part

    opts = dict(
        path_or_hf_repo=model,
        language=language,
        initial_prompt=initial_prompt,
        condition_on_previous_text=condition_on_previous_text,
    )
    if dictation:
        opts["no_speech_threshold"] = 0.45
        opts["temperature"] = (0.0, 0.2)

    def run():
        if language == "auto":
            probs = detect_language_probs(audio, model)
            opts["language"] = lang_state.choose(probs) if lang_state else _pick_language(probs)
        return mlx_whisper.transcribe(audio, **opts)

    try:
        result = run()
    except Exception as e:
        # HF_HUB_OFFLINE=1 (set by bin/voz) fails ONLY when the model isn't cached (or only
        # half of it is: a download that was interrupted). Any other error (corrupt file,
        # ffmpeg, MLX) must surface as-is.
        if os.environ.get("HF_HUB_OFFLINE") != "1" or not (
                _is_missing_model(e) or model_status(model) == "incomplete"):
            raise
        download_model(model)
        result = run()

    last_language = opts["language"] if language == "auto" else language
    segments = _filter_segments(result, audio)
    if lang_state is not None and language == "auto" and segments:
        lang_state.current = opts["language"]   # silence/junk must not set the language
    return segments


def detect_languages(pieces, model: str = DEFAULT_MODEL, allowed=AUTO_LANGUAGES,
                     batch: int = 8) -> list[str]:
    """detect_language for many pieces, batching the encoder pass (one pass per
    `batch` pieces instead of one per piece)."""
    import mlx.core as mx
    from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.transcribe import ModelHolder

    m = ModelHolder.get_model(model, mx.float16)
    out: list[str] = []
    for i in range(0, len(pieces), batch):
        mels = [pad_or_trim(log_mel_spectrogram(p, n_mels=m.dims.n_mels, padding=N_SAMPLES),
                            N_FRAMES, axis=-2) for p in pieces[i:i + batch]]
        _, probs = m.detect_language(mx.stack(mels).astype(mx.float16))
        probs = probs if isinstance(probs, list) else [probs]
        out.extend(_pick_language(pr, allowed) for pr in probs)
    return out


def transcribe_file_segments(path, *, model: str = DEFAULT_MODEL, max_s: float = 25.0) -> list[dict]:
    """Files with --lang auto. Whisper detects the language ONCE (first 30 s), so a
    file mixing Spanish and English came out all in Spanish. Here the file is cut at
    every phrase pause, each phrase's language is detected (batched, es/en), and
    consecutive phrases of the same language are re-joined (≤ max_s) before
    transcribing. Timestamps are shifted back to the whole-file timeline and kept
    monotonic (Whisper can stamp a segment slightly past its piece's end)."""
    global last_language
    import numpy as np
    from mlx_whisper.audio import load_audio

    from record import SAMPLE_RATE, split_on_silence

    sr = SAMPLE_RATE
    audio = np.asarray(load_audio(str(path)), dtype="float32")
    # load (or download, via the retry in transcribe_segments) the model first
    transcribe_segments(np.zeros(8000, dtype="float32"), model=model, language="es")
    def rms(x) -> float:
        return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0

    # pieces with no voice at all are where Whisper invents "Thank you."
    phrases = [(a, b) for a, b in split_on_silence(audio, max_s=max_s, min_gap_s=0.7, every_gap=True)
               if b - a >= 0.3 * sr and rms(audio[a:b]) >= NO_VOICE_RMS]
    if not phrases:
        if audio.size and rms(audio) >= NO_VOICE_RMS:   # e.g. a lone 0.28 s "Sí."
            last_language = default_primary()
            return transcribe_segments(audio, model=model, language=last_language, dictation=False)
        return []
    langs = detect_languages([audio[a:b] for a, b in phrases], model)
    # short phrases (<1 s) are unreliable to detect: take the nearest long phrase's
    # language (previous first, else next), else the primary language
    long_ = [i for i, (a, b) in enumerate(phrases) if b - a >= sr]
    detected = list(langs)
    for i, (a, b) in enumerate(phrases):
        if b - a < sr:
            prev = [j for j in long_ if j < i]
            nxt = [j for j in long_ if j > i]
            langs[i] = (detected[prev[-1]] if prev else detected[nxt[0]] if nxt
                        else default_primary())
    groups: list[list] = []  # [start, end, lang]
    for (a, b), lang in zip(phrases, langs):
        if groups and groups[-1][2] == lang and b - groups[-1][0] <= max_s * sr:
            groups[-1][1] = b
        else:
            groups.append([a, b, lang])
    import mlx.core as mx

    out: list[dict] = []
    last_end = 0.0
    for a, b, lang in groups:
        offset, limit = a / sr, b / sr
        for seg in transcribe_segments(audio[a:b], model=model, language=lang, dictation=False):
            start = max(seg["start"] + offset, last_end)
            end = limit if seg["end"] is None else min(seg["end"] + offset, limit)
            end = max(end, start)
            out.append({**seg, "start": start, "end": end, "lang": lang})
            last_end = end
        # MLX keeps freed GPU buffers cached (60 min peaked at 3 GB). Clearing after
        # EVERY group cut it to 1.35 GB but made the run 5× slower (re-allocation), so
        # only clear once the cache passes CACHE_LIMIT.
        if mx.get_cache_memory() > CACHE_LIMIT:
            mx.clear_cache()
    used = [g[2] for g in groups]
    last_language = "+".join(sorted(set(used), key=used.index))
    return out
