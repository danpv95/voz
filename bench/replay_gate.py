"""Replay a WAV through the real live Segmenter and report, per chunk, what the issue #7
fixes would do: peak, the session noise floor the chunk was cut with, the effective gate
threshold and verdict (live.gate; "v0.1.1" = the old absolute peak >= 0.015) and, with
--detect, the sticky language chosen by the OLD rule (no duration floor) vs the NEW one
(SWITCH_MIN_S).
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


OLD_MIN_PEAK = 0.015   # v0.1.1 absolute gate, shown for comparison


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
        verdicts = [live.gate(c) for c in chunks]
        kept = [c for c, v in zip(chunks, verdicts) if v[0]]
        old_kept = sum(v[1] >= OLD_MIN_PEAK for v in verdicts)
        print(f"{Path(path).name}: {len(audio) / SAMPLE_RATE:.1f} s, gain {a.gain}, "
              f"{len(chunks)} chunks, {len(kept)} accepted, {len(chunks) - len(kept)} gated "
              f"(v0.1.1: {old_kept} accepted)")
        for i, (c, (ok, peak, floor, thr)) in enumerate(zip(chunks, verdicts), 1):
            secs = len(c) / SAMPLE_RATE
            was = "ok" if peak >= OLD_MIN_PEAK else "gated"
            row = (f"  {i:3d}  {secs:5.1f} s  peak {peak:.4f}  floor {floor:.4f}  "
                   f"thr {thr:.4f}  {'accepted' if ok else 'GATED   '}  v0.1.1 {was:5}")
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
