#!/usr/bin/env python3
"""voz: transcripción de voz a texto local (offline, mlx-whisper).

Modos:
  voz                dicta por micrófono → texto al portapapeles
  voz --loop         modo continuo: dicta varias veces (modelo queda cargado)
  voz <archivo>      transcribe un archivo de audio (m4a/ogg/opus/mp3/wav)
  voz devices        lista los dispositivos de audio (debug)
  voz doctor         revisa la instalación (--mic prueba el micrófono)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow flat imports (stt/record/output/corrections) when launched via bin/voz.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import corrections  # noqa: E402
import output  # noqa: E402
from stt import ACCURATE_MODEL, DEFAULT_MODEL, transcribe, transcribe_segments  # noqa: E402

__version__ = "0.1.0"

SILENCE_RMS = 1e-4
MODEL_FAILED = 2  # exit code: the model could not be loaded (live mode)
MIN_VOICE_SECONDS = 0.3
COMMANDS = ("devices", "doctor")


def _warn_silence() -> None:
    print(
        "⚠  Silencio detectado. ¿La terminal tiene permiso de micrófono?\n"
        "   Activa tu terminal en Ajustes del Sistema → Privacidad y seguridad → Micrófono.\n"
        "   Alternativa: tccutil reset Microphone com.apple.Terminal\n"
        "   (iTerm: com.googlecode.iterm2)",
        file=sys.stderr,
    )


def _write_out(text: str, out: str) -> bool:
    """Append to the --out document. A failure (no permission, missing folder, full disk)
    is reported clearly and never costs the text: it is already in the autosave folder
    and, when it was copied, in the clipboard."""
    try:
        output.append_to_doc(text, out)
    except OSError as e:
        import autosave

        print(f"✖  No se pudo escribir en {out}: {e.strerror or e}.\n"
              f"   El texto no se perdió: sigue en el portapapeles (si lo copiaste) y en {autosave.STATE_DIR}.",
              file=sys.stderr)
        return False
    print(f"📝  Añadido a {out}", file=sys.stderr)
    return True


def _emit(text: str, args, rules) -> int:
    if not text:
        print("✖  No se transcribió nada.", file=sys.stderr)
        return 1
    text = corrections.apply(text, rules)
    output.emit(text, copy=not args.no_copy, out=args.out)
    return 0


def _prepare_mic_audio(audio, args):
    """Permission check on the raw audio, then trim. None = nothing to transcribe."""
    import record

    if record.rms(audio) < SILENCE_RMS:
        _warn_silence()
        return None
    if not args.no_trim:
        audio = record.trim_silence(audio)
        if len(audio) < MIN_VOICE_SECONDS * record.SAMPLE_RATE:
            print("⚠  No se detectó voz en la grabación.", file=sys.stderr)
            return None
    return audio


def _short_error(e: Exception) -> str:
    """First meaningful line of an error (ffmpeg dumps its whole banner otherwise)."""
    text = str(e).strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):  # ffmpeg's real reason is at the end
        if "rror" in ln or "nvalid" in ln or "No such" in ln:
            return ln
    return lines[0] if lines else type(e).__name__


def cmd_devices() -> int:
    import sounddevice as sd

    print(sd.query_devices())
    return 0


def _interactive() -> bool:
    """Modern TTY UI unless VOZ_UI=simple (escape hatch for terminals that draw it badly)."""
    import os

    if os.environ.get("VOZ_UI", "").lower() == "simple":
        return False
    return sys.stdin.isatty() and sys.stderr.isatty()


def _require_tty_for_sim(args) -> None:
    """--sim only feeds the TTY UI; elsewhere it would silently record the real mic."""
    if args.sim and (args.no_live or not _interactive()):
        print("✖  --sim solo funciona con la interfaz de terminal (TTY, sin --no-live ni VOZ_UI=simple).",
              file=sys.stderr)
        raise SystemExit(2)


def _live_ui(args, rules, warm: bool = True) -> int:
    """Modern TTY flow: live meter + partial text + single-key controls."""
    import liveui

    code, text = liveui.run(args, rules, transcribe, corrections.apply, warm=warm,
                            silence_rms=SILENCE_RMS, sim=args.sim)
    if code in (0, 130) and text and args.out and not _write_out(text, args.out):   # 130: Ctrl-C keeps the text
        return code or 1                                  # a failed --out never hides the 130
    return code


def _live_once(args, rules, session) -> int:
    """One live dictation: text streams to stdout, then ask whether to copy."""
    import live

    peak = live.record_live(session)
    if peak is None:  # cancelled, or the model could not load
        session.finish()
        return MODEL_FAILED if session.error is not None else 130
    print("⏳  Terminando...", file=sys.stderr)
    text = session.finish()
    if peak < SILENCE_RMS:
        _warn_silence()
        return 1
    if not text:
        print("⚠  No se detectó voz.", file=sys.stderr)
        return 1
    if live.decide_copy(args):
        if output.to_clipboard(text):
            print("📋  Copiado al portapapeles.", file=sys.stderr)
        else:
            print("⚠  No se pudo copiar (pbcopy no disponible).", file=sys.stderr)
    if args.out and not _write_out(text, args.out):
        return 1
    return 0


def _new_session(args, rules, warm: bool = True):
    import live

    return live.LiveSession(transcribe, rules, corrections.apply, args.model, args.lang,
                            warm=warm)


def cmd_mic(args, rules) -> int:
    _require_tty_for_sim(args)
    if not args.no_live:
        if _interactive():
            code = _live_ui(args, rules)
        else:
            code = _live_once(args, rules, _new_session(args, rules))
        return 1 if code == MODEL_FAILED else code

    import record

    audio = record.record_push_to_talk()
    if audio is None:
        return 130
    audio = _prepare_mic_audio(audio, args)
    if audio is None:
        return 1
    print("⏳  Transcribiendo...", file=sys.stderr)
    text = transcribe(audio, model=args.model, language=args.lang)
    return _emit(text, args, rules)


def cmd_loop(args, rules) -> int:
    _require_tty_for_sim(args)
    if not args.no_live:
        if _interactive():  # v2: recording again is built in (r), so --loop is the normal flow
            code = _live_ui(args, rules)
            return 1 if code == MODEL_FAILED else code
        print("🔁  Modo loop en vivo. Ctrl-C en la espera para salir.", file=sys.stderr)
        first = True
        while True:
            import record

            if record.stream_stuck():  # reopening the mic now would block in CoreAudio
                print("⚠  El micrófono quedó trabado en el driver; salgo del modo loop.", file=sys.stderr)
                return 0
            # mlx-whisper keeps the model cached in-process: warm it up only once
            if _interactive():
                code = _live_ui(args, rules, warm=first)
            else:
                code = _live_once(args, rules, _new_session(args, rules, warm=first))
            first = False
            if code == 130:
                print("👋  Fin.", file=sys.stderr)
                return 0
            if code == MODEL_FAILED:  # same failure every round: stop instead of looping
                return 1

    import numpy as np
    import record

    # Warm-up: preload the model on 0.5s of silence so the first real dictation
    # is already fast (mlx-whisper's ModelHolder caches it for the whole loop).
    print("⏳  Cargando modelo...", file=sys.stderr)
    transcribe(np.zeros(8000, dtype="float32"), model=args.model, language=args.lang)
    print("🔁  Modo loop listo. Dicta cuantas veces quieras · Ctrl-C para salir.",
          file=sys.stderr)

    while True:
        audio = record.record_push_to_talk()
        if audio is None:  # Ctrl-C → salir del loop
            print("\n👋  Fin.", file=sys.stderr)
            return 0
        audio = _prepare_mic_audio(audio, args)
        if audio is None:
            continue
        print("⏳  Transcribiendo...", file=sys.stderr)
        text = transcribe(audio, model=args.model, language=args.lang)
        _emit(text, args, rules)


def cmd_file(path: str, args, rules) -> int:
    # absolute: ffmpeg reads "nota:10.wav" as protocol "nota:" otherwise
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"✖  No existe: {p}", file=sys.stderr)
        return 1
    print(f"⏳  Transcribiendo {p.name}...", file=sys.stderr)
    # Files may have genuine pauses - don't tighten the dictation thresholds.
    try:
        if args.lang == "auto":  # per-piece language detection (mixed es/en files)
            import stt

            segments = stt.transcribe_file_segments(str(p), model=args.model)
        else:
            segments = transcribe_segments(
                str(p), model=args.model, language=args.lang, dictation=False
            )
    except Exception as e:  # noqa: BLE001 - show a readable reason, not a traceback
        print(f"✖  No se pudo transcribir {p.name}: {_short_error(e)}", file=sys.stderr)
        return 1
    # Corrections per segment: a multi-word rule spanning two segments won't apply.
    segments = [{**s, "text": corrections.apply(s["text"], rules)} for s in segments]
    text = " ".join(s["text"] for s in segments).strip()
    if not text:
        print("✖  No se transcribió nada.", file=sys.stderr)
        return 1
    # the transcript first (a 60 min file is ~8 min of work): a read-only folder or a
    # bad --out must cost the side file, never the text
    output.emit(text, copy=not args.no_copy, out=args.out)
    for flag, suffix, render in ((args.srt, ".srt", output.to_srt), (args.json, ".json", output.to_json)):
        if flag:
            try:
                output.write_sidecar(p, suffix, render(segments))
            except OSError as e:
                print(f"⚠  No se pudo escribir {suffix}: {e}", file=sys.stderr)
    return 0


EPILOG = """\
teclas del modo en vivo:
  listo        ↵ hablar · esc salir
  escuchando   ↵ terminar · p pausa · esc descartar
  en pausa     p reanudar · ↵ terminar · esc descartar
  revisión     ↵ salir (el texto ya está copiado: Cmd-V para pegar) · r grabar otra
               · c añadir · ⌫ descartar · u deshacer · s copiar (si lo pediste a mano)
  sin audio    ↵ reintentar · esc salir (el micrófono no entrega sonido)

archivos y carpetas:
  ~/.config/voz/config.toml        ajustes (copiar = auto | preguntar | nunca · idioma principal = es | en)
  ~/.config/voz/corrections.toml   glosario de nombres propios
  ~/.local/state/voz               audio y texto de respaldo (solo tu usuario)

VOZ_UI=simple  usa el flujo simple, sin la interfaz interactiva
"""


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """Spanish 'uso:' prefix; keeps the epilog's line breaks."""

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, "uso: " if prefix is None else prefix)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voz",
        description="Transcribe voz a texto local (offline, mlx-whisper).",
        epilog=EPILOG,
        formatter_class=_Formatter,
        add_help=False,
    )
    parser._positionals.title = "argumentos"
    parser._optionals.title = "opciones"
    parser.add_argument("-h", "--help", action="help", help="muestra esta ayuda y sale")
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="archivo de audio a transcribir, 'devices' o 'doctor'. "
        "Sin argumento: dicta por micrófono.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="modo continuo: dicta varias veces, Ctrl-C para salir",
    )
    model = parser.add_mutually_exclusive_group()
    model.add_argument(
        "--model", default=None, help=f"modelo mlx-whisper (default: {DEFAULT_MODEL})"
    )
    model.add_argument(
        "--accurate",
        action="store_true",
        help=f"usa el modelo grande ({ACCURATE_MODEL}): más lento, más preciso",
    )
    parser.add_argument(
        "--lang",
        default="auto",
        help="idioma: auto (mantiene el idioma de la sesión, es/en, default), es, en, …",
    )
    parser.add_argument(
        "--out", default=None, help="además, añade el texto a este archivo/doc"
    )
    parser.add_argument(
        "--no-copy", action="store_true", help="no copiar al portapapeles"
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="micrófono sin transcripción en vivo (graba todo y transcribe al final)",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="copia al portapapeles sin preguntar (solo flujo simple; la interfaz interactiva "
        "copia según config.toml)",
    )
    parser.add_argument(
        "--no-trim",
        action="store_true",
        help="no recortar el silencio del inicio/fin del micrófono",
    )
    parser.add_argument(
        "--srt", action="store_true", help="(archivo) escribe <audio>.srt con tiempos"
    )
    parser.add_argument(
        "--json", action="store_true", help="(archivo) escribe <audio>.json con segmentos"
    )
    parser.add_argument(
        "--mic", action="store_true", help="(doctor) graba 1 s para probar el micrófono"
    )
    # Internal: feed an audio file at real-time pace instead of the mic (UI tests/demo).
    parser.add_argument("--sim", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"voz {__version__}",
                        help="muestra la versión y sale")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.model = ACCURATE_MODEL if args.accurate else (args.model or DEFAULT_MODEL)
    is_file = args.target is not None and args.target not in COMMANDS
    if (args.srt or args.json) and not is_file:
        parser.error("--srt/--json solo funcionan al transcribir un archivo")
    if args.loop and args.target is not None:
        parser.error("--loop es para el micrófono; no se combina con un archivo o comando")
    if args.yes and args.no_copy:
        parser.error("-y/--yes y --no-copy se contradicen; usa solo uno")
    if args.no_trim and args.target is None and not args.no_live:
        parser.error("--no-trim solo aplica con --no-live (el modo en vivo ya corta por pausas)")
    return args


def _enable_stack_dump() -> None:
    """Ctrl-\\ (SIGQUIT) prints every thread's stack to stderr instead of killing voz.
    The way to see what a frozen session is waiting on."""
    import faulthandler
    import signal

    try:
        faulthandler.register(signal.SIGQUIT, all_threads=True, chain=False)
    except (AttributeError, ValueError, RuntimeError):  # not on this platform / not main thread
        pass


def main(argv: list[str] | None = None) -> int:
    _enable_stack_dump()
    args = parse_args(argv)

    if args.target == "devices":
        return cmd_devices()
    if args.target == "doctor":
        import doctor

        return doctor.run(mic=args.mic)

    rules = corrections.load()
    if not args.target:  # mic modes: a previous session that died left its files behind
        import autosave

        autosave.report_unfinished()
    import record

    try:
        if args.target:
            return cmd_file(args.target, args, rules)
        if args.loop:
            return cmd_loop(args, rules)
        return cmd_mic(args, rules)
    except record.MicError:       # the flows without the TTY UI: a message, never a traceback
        print(f"✖  {record.MIC_PROBLEM}\n   {record.MIC_HELP}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\n✖  Cancelado.", file=sys.stderr)
        code = 130
    import record

    reasons = record.hard_exit_reasons()
    if reasons:
        # e.g. CoreAudio never released the mic (PortAudio's atexit Pa_Terminate would
        # hang the same way) or the model was still loading in a native thread: skip
        # interpreter teardown. Output is already written/copied/saved.
        print(f"⚠  Cierre forzado: {reasons[0]} (lo ya transcrito y el audio guardado no se afectan).", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        import os

        os._exit(code or 0)
    raise SystemExit(code)
