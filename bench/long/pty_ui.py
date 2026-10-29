"""Drive the real TTY UI (`voz --sim <wav>`) in a pseudo-terminal and watch for freezes.

    python3 pty_ui.py long3_continuo.wav
Freeze = the "Escuchando MM:SS" timer stops changing for >3 s → sends Ctrl-\\ (stack dump).
Prints a JSON report.
"""
import json, os, pty, re, select, sys, time
from pathlib import Path

wav = str(Path(__file__).resolve().parent / sys.argv[1])
cmd = [str(Path.home() / ".local/bin/voz"), "--sim", wav, "--no-copy"]
pid, fd = pty.fork()
if pid == 0:
    os.environ["COLUMNS"] = "110"; os.environ["LINES"] = "40"
    os.execv(cmd[0], cmd)
buf = bytearray(); strip = lambda b: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", b.decode(errors="ignore"))
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try: buf.extend(os.read(fd, 65536))
            except OSError: return False
    return True
pump(8); os.write(fd, b"\r"); t0 = time.time()
dur = float(os.popen(f"ffprobe -v error -show_entries format=duration -of csv=p=0 '{wav}'").read())
last_timer, last_change, freezes, frames_per_s, seen = None, time.time(), [], [], 0
while time.time() - t0 < dur + 6:
    mark = len(buf); pump(1.0)
    chunk = strip(bytes(buf[mark:]))
    timers = re.findall(r"Escuchando\s+(\d\d:\d\d)", chunk)
    frames_per_s.append(len(timers))
    if timers and timers[-1] != last_timer:
        last_timer, last_change = timers[-1], time.time()
    elif time.time() - last_change > 3 and not freezes:
        freezes.append({"at_s": round(time.time() - t0, 1), "timer": last_timer})
        os.write(fd, b"\x1c"); pump(2)                     # Ctrl-\ → stack dump
os.write(fd, b"\r"); t_stop = time.time()
exited = None
while time.time() - t_stop < 120:          # macOS pty gives no EOF: poll the child
    pump(0.5)
    w = os.waitpid(pid, os.WNOHANG)
    if w[0]:
        exited = w[1]; pump(0.3); break
out = strip(bytes(buf))
final = out.split("Terminando")[-1] if "Terminando" in out else ""
words = len(re.sub(r"\s+", " ", final).split())
dump = out[out.find("Thread 0x"):][:3000] if "Thread 0x" in out else ""
print(json.dumps({"audio_s": round(dur), "freezes": freezes, "fps_min": min(frames_per_s or [0]),
                  "fps_median": sorted(frames_per_s)[len(frames_per_s)//2] if frames_per_s else 0,
                  "last_timer": last_timer, "finish_s": round(time.time() - t_stop, 1), "exit_status": exited,
                  "summary_line": next((l for l in out.splitlines()[::-1] if "palabras" in l), "")[-120:],
                  "final_words_approx": words, "stack_dump": dump}, ensure_ascii=False, indent=1))
try: os.kill(pid, 9)
except Exception: pass
