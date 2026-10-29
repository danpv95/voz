"""Before/after bench of the REAL src/live.Segmenter with fixed noise seeds."""
import sys, time, zlib, importlib
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))
import numpy as np
from common import load_wav, gt_mask, gt_regions, make_noise, mix_at_snr, SR
import live
importlib.reload(live)

AUDIO_DIR = __import__("pathlib").Path(__file__).resolve().parent / "audio"
PHRASES = [str(AUDIO_DIR / f"f{i}.wav") for i in (1, 2, 3)]
_missing = [p for p in PHRASES if not __import__("os").path.exists(p)]
if _missing:
    sys.exit("Faltan frases de prueba (el repo no incluye audio): graba 3 frases propias "
             "en bench/audio/f1.wav, f2.wav y f3.wav (wav mono 16 kHz, ~5-10 s cada una).\n"
             "Faltan: " + ", ".join(_missing))
NOISES = ["white", "pink", "street"]
SNRS = ["clean", 30, 20, 10, 5]

def run(audio, block=1024):
    seg = live.Segmenter(); n = 0; first = None
    t = time.perf_counter()
    for i in range(0, len(audio), block):
        for c in seg.feed(audio[i:i + block]):
            n += 1; first = first if first is not None else len(c)
    last = seg.flush()
    if last is not None:
        n += 1; first = first if first is not None else len(last)
    us = (time.perf_counter() - t) / max(1, len(audio) // seg.frame) * 1e6
    return n, first, us

res = {s: [0, 0] for s in SNRS}; onset = {s: [] for s in SNRS}; cpu = []
for p in PHRASES:
    clean = load_wav(p); mask = gt_mask(clean); regions = gt_regions(mask)
    ideal0 = (regions[0][1] - regions[0][0]) + int(SR * 0.3)
    for snr in SNRS:
        kinds = ["none"] if snr == "clean" else NOISES
        for k in kinds:
            if snr == "clean":
                audio = clean
            else:
                noise = make_noise(k, len(clean), seed=zlib.crc32(f"{p}{k}{snr}".encode()))
                audio = mix_at_snr(clean, mask, noise, snr)
            n, first, us = run(audio.astype("float32"))
            cpu.append(us)
            res[snr][1] += 1; res[snr][0] += int(n == len(regions))
            if first is not None and n == len(regions):
                onset[snr].append(max(0.0, (ideal0 - first) / SR * 1000))
print("SNR      aciertos  perdida_inicio_ms")
for s in SNRS:
    o = f"{np.mean(onset[s]):.0f}" if onset[s] else "-"
    print(f"{str(s):<8} {res[s][0]}/{res[s][1]:<7} {o}")
print(f"CPU: {np.mean(cpu):.0f} us/frame")
