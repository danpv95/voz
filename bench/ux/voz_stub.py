"""voz with a fixed fake transcriber: lets pty_flows check the screens without the model.

    python bench/ux/voz_stub.py --sim bench/long/long1_pausas.wav
"""
import os
import runpy
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
sys.path.insert(0, SRC)

import stt  # noqa: E402

stt.transcribe = lambda audio, **kw: "texto simulado para probar el historial"
sys.argv[0] = os.path.join(SRC, "voz.py")
runpy.run_path(sys.argv[0], run_name="__main__")
