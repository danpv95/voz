"""`voz doctor` - offline self-check. No network; the mic is only touched with --mic."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import corrections
import record
import stt
from stt import ACCURATE_MODEL, DEFAULT_MODEL

OK, WARN, FAIL = "✓", "⚠", "✖"


def _line(mark: str, label: str, detail: str = "") -> None:
    print(f"{mark} {label}" + (f": {detail}" if detail else ""))


def _model_line(label: str, repo: str, optional: bool = False) -> int:
    """Report one model as available, incomplete (weights missing) or absent.
    Returns 1 for a warning, 0 otherwise. An absent optional model is only informative."""
    size = stt.format_size(stt.MODEL_SIZES[repo])
    status = stt.model_status(repo)
    if status == "ready":
        _line(OK, f"{label} disponible", repo)
        return 0
    if status == "incomplete":
        _line(WARN, f"{label} incompleto (faltan los pesos)", f"se reanuda la descarga al usarlo (~{size})")
    elif optional:
        _line("·", f"{label} no descargado", f"opcional; el primer uso descarga ~{size}")
        return 0
    else:
        _line(WARN, f"{label} no está en caché", f"el primer uso descarga ~{size}")
    return 1


def run(mic: bool = False) -> int:
    critical = warnings = 0

    v = sys.version_info
    _line(OK if v[:2] == (3, 12) else WARN, "Python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")

    for mod in ("mlx_whisper", "sounddevice", "numpy"):
        try:
            __import__(mod)
            _line(OK, f"import {mod}")
        except Exception as e:  # noqa: BLE001 - report any import failure
            _line(FAIL, f"import {mod}", str(e))
            critical += 1

    warnings += _model_line("Modelo por defecto", DEFAULT_MODEL)
    warnings += _model_line("Modelo --accurate", ACCURATE_MODEL, optional=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        _line(OK, "ffmpeg", ffmpeg)
    else:   # only needed to transcribe files: dictation works without it
        _line(WARN, "ffmpeg no instalado", "el dictado funciona; para transcribir archivos: brew install ffmpeg")
        warnings += 1

    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        _line(OK, "Micrófono por defecto", dev.get("name", "?"))
    except Exception as e:  # noqa: BLE001
        _line(FAIL, "Micrófono por defecto", str(e))
        critical += 1

    path = corrections.DEFAULT_PATH
    if path.exists():
        _line(OK, "Correcciones", f"{len(corrections.load())} reglas en {path}")
    else:
        _line(WARN, "Correcciones", f"no hay {path} (opcional, ver README)")
        warnings += 1

    found = shutil.which("voz")
    if found:
        _line(OK, "Comando voz", f"{found} → {Path(found).resolve()}")
    else:
        _line(WARN, "Comando voz", "no está en el PATH (~/.local/bin)")
        warnings += 1

    if mic:
        critical += _check_mic()

    if critical:
        print(f"\n{critical} problema(s) crítico(s).")
    elif warnings:
        print(f"\nSin problemas críticos · {warnings} advertencia(s).")
    else:
        print("\nTodo en orden.")
    return 0 if critical == 0 else 1


def _check_mic() -> int:
    import time

    import numpy as np

    print("🎙️  Grabando 1 s para medir el micrófono (habla o haz ruido)...", file=sys.stderr)
    chunks = []
    try:
        stream = record.open_input(lambda indata, *a: chunks.append(indata[:, 0].copy()))
    except record.MicError as e:
        _line(FAIL, "Nivel de micrófono", f"no se pudo abrir ({e}). {record.MIC_HELP}")
        return 1
    time.sleep(1.0)
    record.stop_stream(stream)
    level = record.rms(np.concatenate(chunks)) if chunks else 0.0
    if level < 1e-4:
        _line(FAIL, "Nivel de micrófono", f"RMS {level:.1e}: {record.MIC_PROBLEM.lower()}. {record.MIC_HELP}")
        return 1
    _line(OK, "Nivel de micrófono", f"RMS {level:.1e}")
    return 0
