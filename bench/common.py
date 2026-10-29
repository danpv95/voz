"""Shared helpers for the VAD benchmark. Read-only w.r.t. the repo; this is a
COPY of Segmenter (from src/live.py) so variants can be tried without
touching the real code."""
from __future__ import annotations

import time

import numpy as np
import mlx_whisper.audio as mlxa

SR = 16000
FRAME_MS = 30
PAUSE_MS = 700
MIN_CHUNK_S = 0.6
MAX_CHUNK_S = 25.0
PREROLL_MS = 300
HISTORY_S = 5.0


def load_wav(path: str) -> np.ndarray:
    return np.array(mlxa.load_audio(path, sr=SR), dtype="float32")


def gt_mask(clean: np.ndarray, win_ms: int = 10, floor_db: float = -45.0) -> np.ndarray:
    """Ground-truth voiced/silent mask on the CLEAN tts signal (no added noise).
    say's digital silence is near -inf dBFS, speech sits far above -45 dBFS,
    so an absolute threshold is safe here (this is offline oracle labeling,
    not the thing under test)."""
    win = int(SR * win_ms / 1000)
    n = len(clean) // win
    frames = clean[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    voiced = db > floor_db
    out = np.repeat(voiced, win)  # sample-resolution mask
    if len(out) < len(clean):
        out = np.concatenate([out, np.zeros(len(clean) - len(out), dtype=bool)])
    return out


def gt_regions(mask: np.ndarray, sr: int = SR, min_gap_ms: int = PAUSE_MS):
    """Merge voiced runs separated by < min_gap_ms of silence -> expected
    phrase regions (same grouping rule the Segmenter is supposed to apply)."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    gap = int(sr * min_gap_ms / 1000)
    regions = []
    start = idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i - prev > gap:
            regions.append((start, prev))
            start = i
        prev = i
    regions.append((start, prev))
    return regions


def make_noise(kind: str, n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n).astype("float32")
    if kind == "white":
        return white
    if kind == "pink":
        # simple 1/f shaping via cumulative-sum + high-pass (Voss-ish cheap approx)
        from scipy.signal import lfilter
        b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        a = [1, -2.494956002, 2.017265875, -0.522189400]
        pink = lfilter(b, a, white).astype("float32")
        return pink / (np.std(pink) + 1e-9)
    if kind == "street":
        # stationary pink bed + random traffic bursts (cars/horns) amplitude-modulated
        from scipy.signal import lfilter
        b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
        a = [1, -2.494956002, 2.017265875, -0.522189400]
        bed = lfilter(b, a, white).astype("float32")
        bed /= (np.std(bed) + 1e-9)
        env = np.ones(n, dtype="float32")
        n_bursts = max(1, n // SR)  # ~1 burst/sec
        for _ in range(n_bursts):
            c = rng.integers(0, n)
            w = rng.integers(int(0.05 * SR), int(0.3 * SR))
            amp = rng.uniform(2.0, 5.0)
            lo, hi = max(0, c - w), min(n, c + w)
            t = np.linspace(-1, 1, hi - lo)
            env[lo:hi] += amp * np.exp(-4 * t ** 2)
        return bed * env
    raise ValueError(kind)


def mix_at_snr(clean: np.ndarray, mask: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    sig_rms = np.sqrt(np.mean(clean[mask.astype(bool)] ** 2) + 1e-12) if mask.any() else np.sqrt(np.mean(clean ** 2))
    noise_rms = np.sqrt(np.mean(noise ** 2) + 1e-12)
    target_noise_rms = sig_rms / (10 ** (snr_db / 20))
    noise = noise * (target_noise_rms / (noise_rms + 1e-12))
    return (clean + noise[: len(clean)]).astype("float32")


class Timed:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.dt = time.perf_counter() - self.t0
