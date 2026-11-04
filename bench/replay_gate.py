"""Replay a WAV through the real live Segmenter and report, per chunk, what the issue #7
fixes would do: peak, gate verdict (MIN_CHUNK_PEAK) and, with --detect, the sticky
language chosen by the OLD rule (no duration floor) vs the NEW one (SWITCH_MIN_S).
Never opens the microphone. Prints no transcript text, only counts.

  python bench/replay_gate.py a.wav [b.wav ...] [--gain 0.1] [--detect] [--transcribe]

--gain scales the audio (0.1 emulates quiet speech). --detect needs the Whisper model in
the local cache (runs language detection only). --transcribe also runs Whisper on the
accepted chunks with the real LangState and reports the character count.
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import live  # noqa: E402
from record import SAMPLE_RATE  # noqa: E402


def load(path: str) -> np.ndarray:
    try:
        with wave.open(path) as w:
            if w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1 and w.getsampwidth() == 2:
                return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768
    except wave.Error:
        pass
    from mlx_whisper.audio import load_audio   # any format, via ffmpeg

    return np.asarray(load_audio(path), dtype="float32")


def segment(audio: np.ndarray) -> list[np.ndarray]:
    seg, chunks = live.Segmenter(), []
    for i in range(0, len(audio), 1024):
        chunks += seg.feed(audio[i:i + 1024])
    last = seg.flush()
    return chunks + ([last] if last is not None else [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wavs", nargs="+")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--transcribe", action="store_true")
    a = ap.parse_args()

    import stt

    old, new = stt.LangState(primary="es"), stt.LangState(primary="es")
    for path in a.wavs:
        audio = np.clip(load(path) * a.gain, -1.0, 1.0)
        chunks = segment(audio)
        kept = [c for c in chunks if live.chunk_peak(c) >= live.MIN_CHUNK_PEAK]
        print(f"{Path(path).name}: {len(audio) / SAMPLE_RATE:.1f} s, gain {a.gain}, "
              f"{len(chunks)} chunks, {len(kept)} accepted, {len(chunks) - len(kept)} gated "
              f"(peak < {live.MIN_CHUNK_PEAK})")
        for i, c in enumerate(chunks, 1):
            secs, peak = len(c) / SAMPLE_RATE, live.chunk_peak(c)
            ok = peak >= live.MIN_CHUNK_PEAK
            row = f"  {i:3d}  {secs:5.1f} s  peak {peak:.4f}  {'accepted' if ok else 'GATED   '}"
            if a.detect and ok:
                probs = stt.detect_language_probs(c, stt.DEFAULT_MODEL)
                lo, ln = old.choose(probs), new.choose(probs, secs)
                old.current, new.current = lo, ln
                row += f"  p(en) {probs.get('en', 0):.2f}  old={lo} new={ln}"
            print(row)
        if a.transcribe:
            st = stt.LangState(primary="es")
            chars = sum(len(stt.transcribe(c, language="auto", lang_state=st)) for c in kept)
            print(f"  transcribed {chars} chars from the accepted chunks, final language {st.current}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
