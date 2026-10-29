"""Shell screens and messages: microphone failures, narrow terminals, review hints,
Ctrl-C wording and incomplete transcriptions."""
import time
import types
from pathlib import Path

import pytest

import flow as F
import record
import ui
from live import Segmenter


def _plain(lines):
    return "\n".join(ui._ANSI.sub("", ln) for ln in lines)


def _rec(**kw):
    base = dict(mark=lambda n: None, close=lambda clean=True: None, audio_failed=None, text_failed=None,
                wav_path=Path.home() / ".local/state/voz/sesion-20261029-100000-g1.wav")
    return types.SimpleNamespace(**{**base, **kw})


def _listening(sh, state, cols, monkeypatch, **session):
    monkeypatch.setattr(ui, "width", lambda: cols)
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = state
    sh.listen_t0 = time.monotonic()
    sh.audio.seg = Segmenter()
    sh.audio.session = types.SimpleNamespace(committed="", partial="", failed_chunks=0, **session)
    sh.audio.rec = _rec()
    return [ui._ANSI.sub("", ln) for ln in sh.lines()]


# ---------- narrow terminals ----------
@pytest.mark.parametrize("cols", [40, 59, 80])
def test_listening_keeps_every_key_at_any_width(shell, monkeypatch, cols):
    sh, _ = shell()
    lines = _listening(sh, F.St.LISTEN, cols, monkeypatch)
    text = "\n".join(lines)
    assert all(len(ln) <= cols - 1 for ln in lines)
    assert "↵" in text and "p pausa" in text and "esc" in text


@pytest.mark.parametrize("cols", [40, 59, 80])
def test_paused_keeps_every_key_at_any_width(shell, monkeypatch, cols):
    sh, _ = shell()
    lines = _listening(sh, F.St.PAUSED, cols, monkeypatch)
    text = "\n".join(lines)
    assert all(len(ln) <= cols - 1 for ln in lines)
    assert "p reanudar" in text and "↵" in text and "esc" in text


def test_narrow_listening_puts_the_keys_on_their_own_line_and_drops_the_meter_first(shell, monkeypatch):
    sh, _ = shell()
    sh.langs = ["es"]
    sh.args.lang = "auto"
    sh.flow.takes.append(F.Take(n=2))        # the "grabación 2" tag makes the status line longer
    lines = _listening(sh, F.St.LISTEN, 40, monkeypatch)
    assert lines[0].startswith("●") and "Escuchando" in lines[0] and "↵" not in lines[0]
    assert lines[1] == "↵ terminar · p pausa · esc descartar"
    assert "00:0" in lines[0]                 # the clock survives; the meter is what went


def test_wide_listening_stays_on_one_line(shell, monkeypatch):
    sh, _ = shell()
    lines = _listening(sh, F.St.LISTEN, 120, monkeypatch)
    assert len(lines) == 1 and "↵ terminar · p pausa · esc descartar" in lines[0]


@pytest.mark.parametrize("cols", [40, 80])
def test_audio_loss_warnings_have_their_own_line(shell, monkeypatch, cols):
    sh, _ = shell()
    sh.audio.overflows = 3
    _listening(sh, F.St.LISTEN, cols, monkeypatch)
    sh.audio.session.failed_chunks = 2
    sh.audio.rec = _rec(audio_failed=OSError("disco lleno"))
    lines = [ui._ANSI.sub("", ln) for ln in sh.lines()]
    assert all(len(ln) <= cols - 1 for ln in lines)
    status = [ln for ln in lines if "Escuchando" in ln]
    assert len(status) == 1 and "⚠" not in status[0]            # nothing crowds the status line
    joined = " ".join(" ".join(lines).split())
    for warning in ("⚠ audio perdido ×3", "⚠ el audio no se está guardando en disco", "⚠ tramos fallidos ×2"):
        assert warning in joined
        assert any(ln.strip().startswith(warning.split(" ×")[0][:12]) for ln in lines)   # each starts a line
    keys = next(i for i, ln in enumerate(lines) if "p pausa" in ln)
    assert keys <= 1                                              # the keys stay right under the status


@pytest.mark.parametrize("cols", [40, 59, 100])
def test_review_hints_wrap_instead_of_losing_keys(shell, monkeypatch, cols):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    sh.flow.takes.append(F.Take(n=1, text="uno dos", seconds=3))
    sh.flow.state = F.St.REVIEW
    sh.copied = True
    lines = [ui._ANSI.sub("", ln) for ln in sh.lines()][1:]      # the hint lines, not the summary
    text = "\n".join(lines)
    assert all(len(ln) <= cols - 1 for ln in lines)
    for key in ("↵ salir", "r ", "c añadir", "⌫ descartar"):
        assert key in text


# ---------- review wording ----------
def test_review_says_cmd_v_only_when_the_text_is_copied(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, text="uno dos", seconds=3))
    sh.flow.state = F.St.REVIEW
    sh.copied = True
    text = _plain(sh.lines())
    assert "↵ salir · Cmd-V para pegar" in text and "↵ listo" not in text
    sh.copied = None                                   # copiar = nunca / preguntar: nothing to paste yet
    assert "Cmd-V" not in _plain(sh.lines()) and "↵ salir" in _plain(sh.lines())


# ---------- microphone ----------
def _start(sh, monkeypatch, opens):
    """Press ↵ in READY with an audio source whose open() is scripted."""
    sh.region.draw = lambda lines: None
    monkeypatch.setattr(sh.audio, "open", opens)
    sh.perform(F.step(sh.flow, F.ENTER), None)


def test_mic_that_cannot_open_shows_the_guided_screen_not_a_traceback(shell, monkeypatch):
    sh, _ = shell()

    def refuse(refresh=False):
        raise record.MicError("Error opening InputStream: Permission denied")

    _start(sh, monkeypatch, refuse)
    try:
        assert sh.flow.state is F.St.NOVOICE and sh.flow.current.nomic
        text = _plain(sh.lines())
        assert "✖  No llega audio del micrófono" in text
        assert "Revisa la entrada y el permiso en Ajustes del Sistema → Privacidad y seguridad → Micrófono" in " ".join(text.split())
        assert "↵ reintentar" in text and "esc salir" in text
        assert "Traceback" not in text and "Permission" not in text
    finally:
        sh._cleanup()


def test_retry_looks_the_default_device_up_again_and_can_succeed(shell, monkeypatch):
    sh, _ = shell()
    calls = []

    def flaky(refresh=False):
        calls.append(refresh)
        if len(calls) == 1:
            raise record.MicError("sin entrada")
        sh.audio._stop = lambda: None

    _start(sh, monkeypatch, flaky)
    try:
        assert sh.flow.state is F.St.NOVOICE
        sh.perform(F.step(sh.flow, F.ENTER), None)      # ↵ reintentar
        assert calls == [False, True]                    # the retry asks PortAudio for the devices again
        assert sh.flow.state is F.St.LISTEN and sh.audio.gate and [t.n for t in sh.flow.takes] == [1, 2]
    finally:
        sh._cleanup()


def test_esc_on_the_mic_screen_leaves_with_code_1(shell, monkeypatch):
    sh, _ = shell()

    def refuse(refresh=False):
        raise record.MicError("x")

    _start(sh, monkeypatch, refuse)
    try:
        assert sh.perform(F.step(sh.flow, F.ESC), None) == (1, "")
    finally:
        sh._cleanup()


def _finished_take(sh, peak, text=""):
    sh.audio.seg = Segmenter()
    sh.audio.seg.peak_level = peak
    sh.audio.session = types.SimpleNamespace(committed=text, partial="", pending=0, abandoned=False,
                                             failed_chunks=0, drain=lambda timeout=None: True,
                                             submit=lambda c: None)
    sh.audio.rec = _rec()
    sh.region.draw = lambda lines: None
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.FINISHING
    sh._finish(None)


def test_digital_silence_is_a_microphone_problem_and_retry_reopens_it(shell):
    sh, _ = shell()
    _finished_take(sh, peak=0.0)
    text = _plain(sh.lines())
    assert sh.flow.current.nomic and sh.retry_mic
    assert "No llega audio del micrófono" in text and "↵ reintentar" in text and "No se detectó voz" not in text
    assert F.step(sh.flow, F.ENTER).action is F.Act.START


def test_silence_with_sound_but_no_words_is_still_no_voice(shell):
    sh, _ = shell()
    _finished_take(sh, peak=0.2)
    text = _plain(sh.lines())
    assert not sh.flow.current.nomic and "No se detectó voz" in text and "micrófono" not in text


@pytest.mark.parametrize("cols", [40, 80])
def test_mic_screen_fits_narrow_terminals(shell, monkeypatch, cols):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    _finished_take(sh, peak=0.0)
    lines = [ui._ANSI.sub("", ln) for ln in sh.lines()]
    assert all(len(ln) <= cols - 1 for ln in lines)
    assert "Micrófono" in " ".join(lines) and "↵ reintentar" in "\n".join(lines)


def test_open_input_wraps_driver_errors(monkeypatch):
    import sys

    class Boom:
        def __init__(self, **kw):
            raise OSError("PortAudioError -9996 Invalid input device\ndetalle")

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Boom))
    with pytest.raises(record.MicError) as e:
        record.open_input(lambda *a: None)
    assert "Invalid input device" in str(e.value) and "detalle" not in str(e.value)


def test_open_input_refresh_reinitialises_portaudio(monkeypatch):
    import sys

    calls = []

    class Stream:
        def __init__(self, **kw):
            calls.append("open")

        def start(self):
            calls.append("start")

    fake = types.SimpleNamespace(InputStream=Stream, _terminate=lambda: calls.append("terminate"),
                                 _initialize=lambda: calls.append("initialize"))
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    record.open_input(lambda *a: None, refresh=True)
    assert calls == ["terminate", "initialize", "open", "start"]
    calls.clear()
    record.open_input(lambda *a: None)
    assert calls == ["open", "start"]


def test_main_turns_a_mic_error_into_a_message_and_exit_1(monkeypatch, capsys):
    import voz

    def boom(args, rules):
        raise record.MicError("sin entrada")

    monkeypatch.setattr(voz, "cmd_mic", boom)
    monkeypatch.setattr(voz.corrections, "load", lambda: [])
    monkeypatch.setattr("autosave.report_unfinished", lambda: None)
    assert voz.main([]) == 1
    err = capsys.readouterr().err
    assert "No llega audio del micrófono" in err and "Privacidad y seguridad" in err and "Traceback" not in err


# ---------- Ctrl-C wording ----------
def test_ctrl_c_with_nothing_recorded_says_cancelado(shell):
    sh, _ = shell()
    assert sh._interrupt_message("") == "Cancelado"


def test_ctrl_c_after_dictating_says_the_text_was_copied(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, text="lo dictado", seconds=4))
    sh.flow.state = F.St.REVIEW
    code, text = sh._interrupted()
    assert code == 130 and text == "lo dictado"           # the exit code is kept
    assert sh._interrupt_message(text) == "Interrumpido · texto copiado"


def test_ctrl_c_without_copy_points_to_the_saved_audio(shell):
    sh, _ = shell()
    sh.copy_mode = "nunca"
    sh.flow.takes.append(F.Take(n=1, text="lo dictado", seconds=4))
    sh.audio.rec = _rec()
    message = sh._interrupt_message("lo dictado")
    assert message == "Interrumpido · audio guardado en ~/.local/state/voz/sesion-20261029-100000-g1.wav"


def test_ctrl_c_never_promises_a_backup_that_failed(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, seconds=4))
    sh.audio.rec = _rec(audio_failed=OSError("disco lleno"))
    assert sh._interrupt_message("") == "Interrumpido"
    sh.copied = False                                    # pbcopy failed: not "copiado" either
    assert sh._interrupt_message("texto") == "Interrumpido"


# ---------- incomplete transcription ----------
def test_review_warns_when_chunks_were_not_transcribed(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, text="parte uno", seconds=9))
    sh.flow.state = F.St.REVIEW
    sh.audio.rec = _rec()
    sh.copied, sh.lost = True, 2
    text = " ".join(_plain(sh.lines()).split())
    assert "⚠ Texto incompleto · 2 tramos sin transcribir · copiado" in text
    assert 'Para transcribirlo entero: voz "~/.local/state/voz/sesion-20261029-100000-g1.wav"' in text
    assert "✓" not in text
    sh.lost = 1
    assert "1 tramo sin transcribir" in " ".join(_plain(sh.lines()).split())


def test_review_of_a_complete_text_has_no_warning(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, text="todo bien", seconds=3))
    sh.flow.state = F.St.REVIEW
    sh.audio.rec = _rec()
    sh.copied = True
    assert "incompleto" not in _plain(sh.lines()) and "✓" in _plain(sh.lines())


def test_when_everything_failed_it_is_not_reported_as_no_voice(shell):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, seconds=9, novoice=True))
    sh.flow.state = F.St.NOVOICE
    sh.audio.rec = _rec()
    sh.audio.seg = Segmenter()
    sh.audio.seg.peak_level = 0.3
    sh.lost = 3
    text = " ".join(_plain(sh.lines()).split())
    assert "✖ No se pudo transcribir · el audio quedó guardado" in text
    assert "No se detectó voz" not in text and "voz \"~/.local/state/voz/" in text
    sh.audio.rec = _rec(audio_failed=OSError("disco lleno"))      # and no promise the audio is there
    assert "quedó guardado" not in _plain(sh.lines())


def test_failed_chunks_during_finish_mark_the_recording_incomplete(shell):
    sh, _ = shell()
    _finished_take(sh, peak=0.3, text="algo")
    assert sh.lost == 0
    sh.flow.state = F.St.FINISHING
    sh.audio.session.failed_chunks = 2
    sh.flow.discard_on_finish = False
    sh._finish(None)
    assert sh.lost == 2 and sh.incomplete_text
    assert "Texto incompleto" in _plain(sh.lines())
