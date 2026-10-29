"""Transcript output: clipboard (pbcopy), stdout, and optional append to a doc."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except (subprocess.CalledProcessError, OSError):  # incl. fork EAGAIN, missing pbcopy
        return False


def from_clipboard() -> str | None:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    except (subprocess.CalledProcessError, OSError):
        return None


def append_to_doc(text: str, path: str) -> None:
    p = Path(path).expanduser()
    with p.open("a", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _end_of(segments: list[dict], i: int) -> float:
    """Segment end; when unknown, the next start or start + 2s."""
    end = segments[i].get("end")
    if end is not None:
        return end
    if i + 1 < len(segments):
        return segments[i + 1]["start"]
    return segments[i]["start"] + 2.0


def to_srt(segments: list[dict]) -> str:
    blocks = []
    for i, s in enumerate(segments):
        blocks.append(
            f"{i + 1}\n{_srt_time(s['start'])} --> {_srt_time(_end_of(segments, i))}\n{s['text']}\n"
        )
    return "\n".join(blocks)


def to_json(segments: list[dict]) -> str:
    rows = [
        {"start": round(s["start"], 3), "end": round(_end_of(segments, i), 3), "text": s["text"],
         **({"lang": s["lang"]} if s.get("lang") else {})}
        for i, s in enumerate(segments)
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2) + "\n"


def unique_path(path: Path) -> Path:
    """`path` if free, else path-1, path-2… (never overwrite a previous transcript)."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def write_sidecar(audio_path: Path, suffix: str, content: str) -> Path:
    """Create the sidecar exclusively ("x"): if another process took the name between
    the existence check and the write, take the next free suffix instead of overwriting."""
    base = audio_path.with_suffix(suffix)
    for _ in range(100):
        dest = unique_path(base)
        try:
            with dest.open("x", encoding="utf-8") as f:
                f.write(content)
        except FileExistsError:
            continue
        print(f"🕒  Escrito {dest}", file=sys.stderr)
        return dest
    raise FileExistsError(f"no hay un nombre libre junto a {base}")


def emit(text: str, *, copy: bool = True, out: str | None = None) -> None:
    """Print the transcript to stdout, copy it, and optionally append to a doc.

    The transcript is the ONLY thing on stdout; all status goes to stderr so the
    output stays clean for piping.
    """
    print(text)
    if copy:
        if to_clipboard(text):
            print("📋  Copiado al portapapeles.", file=sys.stderr)
        else:
            print("⚠  No se pudo copiar (pbcopy no disponible).", file=sys.stderr)
    if out:
        try:
            append_to_doc(text, out)
            print(f"📝  Añadido a {out}", file=sys.stderr)
        except OSError as e:
            print(f"⚠  No se pudo añadir a {out}: {e}", file=sys.stderr)
