"""Real-model stress test of the live engine (no TTY): feeds a long wav at real-time pace
through Segmenter + LiveSession (same wiring as liveui) while a fake UI loop ticks every
80 ms and records how late each tick is. Prints a JSON-ish report.

    ../../.venv/bin/python stress_live.py long3_continuo.wav [speed]
"""
import faulthandler, json, sys, threading, time
faulthandler.dump_traceback_later(60, repeat=True)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import numpy as np
import corrections, stt
from live import LiveSession, Segmenter
from mlx_whisper.audio import load_audio

wav = Path(__file__).resolve().parent / sys.argv[1]
speed = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
audio = np.asarray(load_audio(str(wav)), dtype="float32")
langs, calls = [], []
T0 = time.perf_counter()

def tracked(a, **kw):
    t = time.perf_counter()
    out = stt.transcribe(a, **kw)
    calls.append({"t": round(time.perf_counter() - T0, 1), "len_s": round(len(a) / 16000, 1),
                  "lang": kw.get("language"), "dur": round(time.perf_counter() - t, 2)})
    if out and kw.get("language") == "auto" and stt.last_language:
        langs.append(stt.last_language)
    return out

seg = Segmenter()
s = LiveSession(tracked, corrections.load(), corrections.apply, stt.DEFAULT_MODEL, "auto",
                echo=False, partial_source=seg.current,
                partial_lang=lambda: langs[-1] if langs else "es")
s.ready.wait()
T0 = time.perf_counter()
stop = threading.Event()
lock_wait = []

def feeder():
    for i in range(0, len(audio) + 16000 * 2, 1024):
        b = audio[i:i + 1024] if i < len(audio) else np.zeros(1024, dtype="float32")
        t = time.perf_counter()
        chunks = seg.feed(b)
        lock_wait.append(time.perf_counter() - t)
        for c in chunks:
            s.submit(c)
        time.sleep(1024 / 16000 / speed)
    stop.set()

th = threading.Thread(target=feeder, daemon=True); th.start()
late, last = [], time.perf_counter()
while not stop.is_set():
    _ = (list(seg.recent_levels), s.committed, s.partial)   # what the UI reads
    time.sleep(0.08)
    now = time.perf_counter(); late.append(now - last - 0.08); last = now
r = seg.flush()
if r is not None: s.submit(r)
t_end = time.perf_counter()
text = s.finish()
drain = time.perf_counter() - t_end
late_ms = np.array(late) * 1000
finals = [c for c in calls if c["lang"] == "auto"]
partials = [c for c in calls if c["lang"] != "auto"]
print(json.dumps({
  "audio_s": round(len(audio) / 16000), "speed": speed,
  "ui_tick_late_ms": {"p50": round(float(np.percentile(late_ms, 50))), "p95": round(float(np.percentile(late_ms, 95))),
                      "max": round(float(late_ms.max())), "ticks_over_1s": int((late_ms > 1000).sum())},
  "feed_ms": {"p95": round(float(np.percentile(lock_wait, 95)) * 1000, 2), "max": round(max(lock_wait) * 1000, 1)},
  "n_final_calls": len(finals), "n_partial_calls": len(partials),
  "partial_dur_by_len": [(c["len_s"], c["dur"]) for c in partials][-12:],
  "final_dur_max": max([c["dur"] for c in finals] or [0]),
  "drain_after_stop_s": round(drain, 1), "words": len(text.split()),
  "head": text[:90], "tail": text[-90:], "failed_chunks": s.failed_chunks, "session_error": repr(s.error),
}, ensure_ascii=False, indent=1))
