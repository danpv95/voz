"""Generate long test audios (1, 3, 5 min) for voz live/file stress tests.

    python3 make_long.py           → long1_pausas.wav, long3_continuo.wav, long5_mixto.wav (16 kHz mono)
    python3 make_long.py 10 30 60  → además long10_mixto.wav, long30_mixto.wav, long60_mixto.wav
Original sentences (orders + technical dictation). Requires macOS `say` + ffmpeg.
"""
import random
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
random.seed(7)
ITEMS = ["galones de salsa rosada", "kilos de queso mozzarella", "paquetes de salchicha americana",
         "cajas de pan para perro", "bolsas de papas a la francesa", "frascos de salsa de piña",
         "kilos de tocineta", "paquetes de servilletas", "litros de aceite", "bolsas de cebolla crispy"]
NUMS = ["dos", "tres", "cuatro", "cinco", "seis", "diez", "doce", "quince"]
TECH = ["Necesito que revises el módulo de facturación antes del viernes",
        "El webhook de pagos tiene que validar la firma con HMAC",
        "Hay que separar el texto parcial del texto confirmado en la interfaz",
        "Probemos el detector de pausas con ruido de fondo moderado",
        "La migración de base de datos debe correr antes del despliegue",
        "Revisa que el pedido quede confirmado por una persona antes de despacharlo"]
EN = ["Please check the invoice module before Friday",
      "The payment webhook must validate the signature",
      "Let us test the pause detector with background noise",
      "The order has to be confirmed by a person before dispatch",
      "Send me a short summary of today's sales by voice"]

def order():
    return f"Para mañana necesito {random.choice(NUMS)} {random.choice(ITEMS)} y {random.choice(NUMS)} {random.choice(ITEMS)}"

def say(text, voice, out):
    subprocess.run(["say", "-v", voice, "-o", str(out), text], check=True)

def to_wav(parts, out, target_s):
    """parts: list of (aiff_path or None for silence, seconds_of_silence). Loops until target length."""
    lst = HERE / "_list.txt"
    lines, total = [], 0.0
    i = 0
    while total < target_s:
        p, sil = parts[i % len(parts)]
        dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                             "-of", "csv=p=0", str(p)]).decode())
        lines.append(f"file '{p}'")
        total += dur
        if sil:
            s = HERE / f"_sil{sil}.wav"
            if not s.exists():
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-t", str(sil), "-i",
                                "anullsrc=r=22050:cl=mono", str(s)], check=True)
            lines.append(f"file '{s}'")
            total += sil
        i += 1
    lst.write_text("\n".join(lines))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-ar", "16000", "-ac", "1", "-t", str(target_s), str(out)], check=True)
    lst.unlink()

def build():
    tmp = HERE / "_seg"; tmp.mkdir(exist_ok=True)
    es, en, cont = [], [], []
    for k in range(12):
        p = tmp / f"es{k}.aiff"; say(order() + ". " + random.choice(TECH) + ".", "Paulina", p); es.append(p)
    for k, t in enumerate(EN):
        p = tmp / f"en{k}.aiff"; say(t + ".", "Daniel", p); en.append(p)
    for k in range(6):  # run-on speech, no sentence pauses (the freeze case)
        p = tmp / f"cont{k}.aiff"
        say(", y además ".join(order() + " " + random.choice(TECH).lower() for _ in range(5)), "Paulina", p)
        cont.append(p)
    to_wav([(p, 1.5) for p in es], HERE / "long1_pausas.wav", 60)
    to_wav([(p, 0) for p in cont], HERE / "long3_continuo.wav", 180)
    mix = []
    for k in range(12):
        mix.append((es[k % len(es)], 1.0))
        if k % 3 == 2:
            mix.append((en[k % len(en)], 1.0))
        if k % 4 == 3:
            mix.append((cont[k % len(cont)], 1.0))
    to_wav(mix, HERE / "long5_mixto.wav", 300)
    for minutes in EXTRA:  # python3 make_long.py 10 30 60 → longN_mixto.wav
        to_wav(mix, HERE / f"long{minutes}_mixto.wav", minutes * 60)
    for f in tmp.iterdir(): f.unlink()
    tmp.rmdir()
    for f in HERE.glob("_sil*.wav"): f.unlink()

EXTRA: list[int] = []

if __name__ == "__main__":
    import sys
    EXTRA = [int(x) for x in sys.argv[1:]]
    build()
    for f in sorted(HERE.glob("long*.wav")):
        d = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)]).decode().strip()
        print(f"{f.name}: {float(d):.0f} s")
