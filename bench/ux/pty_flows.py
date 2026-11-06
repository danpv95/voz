"""End-to-end checks of the v2 interaction in a pseudo-terminal.

    python3 bench/ux/pty_flows.py            (uses --sim audio, fake pbcopy, temp state dir)
"""
import fcntl, glob, os, pty, re, select, shutil, struct, sys, tempfile, termios, time, wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VOZ = os.environ.get("VOZ_BIN", os.path.join(ROOT, "bin/voz"))   # this checkout, not whatever ~/.local/bin points to
SPEECH = os.path.join(ROOT, "bench/long/long1_pausas.wav")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
results = []


def setup():
    tmp = tempfile.mkdtemp(prefix="vozux-")
    os.makedirs(f"{tmp}/bin")
    with open(f"{tmp}/bin/pbcopy", "w") as f:
        f.write(f"#!/bin/sh\ncat > {tmp}/clipboard.txt\n")
    os.chmod(f"{tmp}/bin/pbcopy", 0o755)
    import random
    silent = f"{tmp}/room.wav"          # room tone: low noise, no speech (what a real mic hears)
    rnd = random.Random(1)
    frames = b"".join(int(rnd.gauss(0, 60)).to_bytes(2, "little", signed=True) for _ in range(16000 * 20))
    with wave.open(silent, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(frames)
    zeros = f"{tmp}/zeros.wav"           # digital silence: what a mic WITHOUT permission returns
    with wave.open(zeros, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 10)
    env = dict(os.environ, VOZ_STATE_DIR=f"{tmp}/state", VOZ_CONFIG=f"{tmp}/none.toml",
               PATH=f"{tmp}/bin:" + os.environ["PATH"])
    return tmp, env, silent, zeros


def session(env, wav, script, cols=100, cmd=None):
    pid, fd = pty.fork()
    if pid == 0:
        cmd = cmd or [VOZ]
        os.execve(cmd[0], [*cmd, "--sim", wav], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, cols, 0, 0))
    buf = bytearray()

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try: buf.extend(os.read(fd, 65536))
                except OSError: return

    def until(text, timeout=90):
        end = time.time() + timeout
        while time.time() < end:
            if text in ANSI.sub("", buf.decode(errors="ignore")):
                return True
            pump(0.2)
        return False

    pump(1)
    status = None
    for kind, val in script:
        if kind == "wait": pump(val)
        elif kind == "until": assert until(val), f"no apareció «{val}»; pantalla: …{ANSI.sub('', buf.decode(errors='ignore'))[-300:]!r}"
        elif kind == "key":
            try: os.write(fd, val)
            except OSError: break          # the process already exited
            pump(0.3)
        elif kind == "sig": os.kill(pid, val); pump(0.5)
    end = time.time() + 30
    while time.time() < end:
        pump(0.2)
        w = os.waitpid(pid, os.WNOHANG)
        if w[0]:
            status = os.waitstatus_to_exitcode(w[1]); break
    if status is None:
        os.kill(pid, 9)
    return ANSI.sub("", buf.decode(errors="ignore")), status


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("✓" if cond else "✖"), name, ("· " + detail) if detail else "")


def clip(tmp):
    p = f"{tmp}/clipboard.txt"
    return open(p).read() if os.path.exists(p) else None


def reset(tmp):
    for p in glob.glob(f"{tmp}/clipboard.txt"): os.remove(p)
    shutil.rmtree(f"{tmp}/state", ignore_errors=True)


def wav_secs(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


tmp, env, silent, zeros = setup()
E, ESC, BS = b"\r", b"\x1b", b"\x7f"
ready = ("until", "Listo")
review = ("until", "palabras")

# 1 - daily flow: ↵ ↵ ↵
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 6), ("key", E), review, ("key", E)])
c = clip(tmp)
files = sorted(os.path.basename(p) for p in glob.glob(f"{tmp}/state/*"))
check("1 diario: 3 Enter → sale 0 y queda copiado", st == 0 and bool(c and c.strip()), f"exit={st} copiado={bool(c)}")
check("1 diario: grabación en el historial", "── grabación 1" in out)
check("1 diario: archivos -g1 sin lock", any(f.endswith("-g1.wav") for f in files) and not any(f.endswith(".lock") for f in files), ", ".join(files))
reset(tmp)

# 2 - pause: 4 s recording + 4 s pause + 4 s recording
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 4), ("key", b"p"), ("until", "En pausa"),
                                ("wait", 4), ("key", b"p"), ("wait", 4), ("key", E), review, ("key", E)])
w = glob.glob(f"{tmp}/state/*-g1.wav")
secs = wav_secs(w[0]) if w else 0
check("2 pausa: muestra «En pausa» y el audio no incluye la pausa", "En pausa" in out and 6.5 <= secs <= 10.5, f"audio={secs:.1f}s (grabado ≈8 s)")
reset(tmp)

# 3 - continue + record another + copy all
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 5), ("key", E), review,
                                ("key", b"c"), ("wait", 5), ("key", E), ("until", "(continúa)"),
                                ("key", b"r"), ("wait", 5), ("key", E), ("until", "── grabación 2"), ("wait", 2),
                                ("key", E), ("until", "copiar todas"), ("key", b"a")])
c = clip(tmp) or ""
check("3 continuar: «(continúa)» en el historial", "(continúa)" in out)
check("3 grabar otra + a: portapapeles con las 2 grabaciones", c.count("\n\n") >= 1 and st == 0, f"exit={st} bloques={c.count(chr(10)*2)+1 if c else 0}")
check("3 archivos -g1 y -g2", len(glob.glob(f"{tmp}/state/*-g1.wav")) == 1 and len(glob.glob(f"{tmp}/state/*-g2.wav")) == 1)
reset(tmp)

# 4 - discard + undo
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 5), ("key", E), review, ("key", BS),
                                ("until", "descartada"), ("key", b"u"), ("until", "restaurada"), ("key", E)])
txt = glob.glob(f"{tmp}/state/*-g1.txt")
marks = open(txt[0]).read() if txt else ""
check("4 descartar + deshacer: sale 0 con texto copiado", st == 0 and bool((clip(tmp) or "").strip()), f"exit={st}")
check("4 descartar no borra el disco; marca y restaura", glob.glob(f"{tmp}/state/*-g1.wav") != [] and "[descartada]" in marks and "[restaurada]" in marks)
reset(tmp)

# 5 - esc while recording → discarded (undoable), exit without copying
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 5), ("key", ESC), ("until", "descartada"), ("key", E)])
check("5 esc grabando: queda descartada y no copia", "descartada" in out and clip(tmp) is None and st == 1, f"exit={st}")
check("5 esc grabando: el audio sigue en disco", glob.glob(f"{tmp}/state/*-g1.wav") != [])
reset(tmp)

# 6 - no voice → retry
out, st = session(env, silent, [ready, ("key", E), ("wait", 3), ("key", E), ("until", "No se detectó voz"),
                                ("key", b"r"), ("until", "Escuchando"), ("wait", 2), ("key", E),
                                ("until", "No se detectó voz"), ("key", E)])
check("6 sin voz: ofrece reintentar y r abre otra grabación", "intentar de nuevo" in out and len(glob.glob(f"{tmp}/state/*-g2.wav")) == 1, f"exit={st}")
reset(tmp)

# 6b - digital silence → "no llega audio del micrófono" screen (↵ retries, esc leaves)
out, st = session(env, zeros, [ready, ("key", E), ("wait", 3), ("key", E), ("until", "No llega audio del micrófono"), ("key", ESC)])
check("6b silencio digital: guía a revisar entrada y permiso, con reintento",
      "No llega audio del micrófono" in out and "Privacidad y seguridad" in out and "reintentar" in out, f"exit={st}")
reset(tmp)

# 7 - Ctrl-C while recording → safe exit, text kept
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 6), ("key", b"\x03"), ("wait", 6)])
check("7 Ctrl-C grabando: guarda y deja copiado", "Interrumpido" in out and bool((clip(tmp) or "").strip()), f"exit={st}")
check("7 Ctrl-C: sin locks colgados", glob.glob(f"{tmp}/state/*.lock") == [])
reset(tmp)

# 8 - narrow terminal (60 cols): no status line wider than 60
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 4), ("key", b"p"), ("wait", 1), ("key", b"p"),
                                ("wait", 2), ("key", E), review, ("key", E)], cols=60)
widest = max((len(l) for l in re.split(r"[\r\n]", out) if "↵" in l or "Escuchando" in l or "pausa" in l), default=0)
check("8 terminal de 60 columnas: ninguna línea de estado se parte", widest <= 60, f"más ancha={widest}")
reset(tmp)             # the history flows below start from a state dir with only their own recordings

STUB = [sys.executable, os.path.join(ROOT, "bench/ux/voz_stub.py")]   # fixed fake text: no model needed

# 9 - history: h → ↓ → ↵ copies the older saved recording (it is what stays after leaving)
OLD = "nota guardada de otro día sobre el presupuesto de compras"


def seed_old():
    os.makedirs(f"{tmp}/state", mode=0o700, exist_ok=True)
    with open(f"{tmp}/state/sesion-20260102-103000-g1.txt", "w") as f:
        f.write(OLD + "\n")


seed_old()
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 4), ("key", E), review, ("key", b"h"), ("until", "Historial"),
                                ("key", b"\x1b[B"), ("key", E), ("until", "Copiado · sesión 2026-01-02 10:30"), ("key", E)],
                  cmd=STUB)
shown = out.split("Historial")[-1]
check("9 historial: ↓ ↵ copia la elegida con el mensaje estándar",
      f"grabación 1 · {len(OLD.split())} palabras" in shown and (clip(tmp) or "").strip() == OLD, f"copiado={bool(clip(tmp))}")
check("9 historial: salir después conserva la elegida (no vuelve a copiar la actual)", st == 0, f"exit={st}")
reset(tmp)

# 10 - history: h → esc goes back to the review, the clipboard keeps the current recording
seed_old()
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 4), ("key", E), review, ("key", b"h"), ("until", "Historial"),
                                ("key", b"\x1b[B"), ("key", ESC), ("wait", 1), ("key", E)], cmd=STUB)
back = out.split("Historial")[-1]
c = (clip(tmp) or "").strip()
check("10 historial: esc vuelve a la revisión sin cambiar nada",
      "r grabar otra" in back and "h historial" in back and st == 0 and bool(c) and c != OLD, f"exit={st}")
reset(tmp)

# 11 - history straight from READY (nothing recorded yet) copies a saved recording
seed_old()
out, st = session(env, SPEECH, [ready, ("key", b"h"), ("until", "Historial"), ("key", E),
                                ("until", "Copiado · sesión 2026-01-02 10:30"), ("key", ESC)], cmd=STUB)
check("11 historial desde Listo: ↵ copia la guardada y esc sale sin grabar",
      (clip(tmp) or "").strip() == OLD and st == 0, f"exit={st}")
reset(tmp)

# 12 - READY with nothing saved: a short notice instead of an empty history
out, st = session(env, SPEECH, [ready, ("key", b"h"), ("until", "No hay grabaciones guardadas"), ("key", ESC)], cmd=STUB)
check("12 historial vacío: aviso breve y se queda en Listo", "Historial" not in out and st == 0, f"exit={st}")
reset(tmp)

# 13 - `s` is offered in review even with copiar = auto, and copies again
out, st = session(env, SPEECH, [ready, ("key", E), ("wait", 4), ("key", E), review, ("until", "s copiar de nuevo"),
                                ("key", b"s"), ("wait", 1), ("key", E)], cmd=STUB)
check("13 revisión en auto: «s copiar de nuevo» visible y funciona", "s copiar de nuevo" in out and st == 0, f"exit={st}")
reset(tmp)

shutil.rmtree(tmp, ignore_errors=True)
ok = sum(1 for _, c, _ in results if c)
print(f"\n{ok}/{len(results)} verificaciones OK")
sys.exit(0 if ok == len(results) else 1)
