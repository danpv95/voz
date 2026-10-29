"""Keys, clipboard and exit codes of the interactive shell (liveui)."""
import os
import sys
import types

import pytest

import corrections
import flow as F
import liveui
import ui


# ---------- keys ----------
class _Pipe:
    def __init__(self):
        self.r, self.w = os.pipe()

    def fileno(self):
        return self.r

    def send(self, data: bytes):
        os.write(self.w, data)


def _keys(*chunks):
    p = _Pipe()
    k = ui.Keys(p)
    for c in chunks:
        p.send(c)
    return k


def _drain(k, n=20):
    out = []
    for _ in range(n):
        ch = k.read(0.02)
        if ch is not None:
            out.append(ch)
    return out


def test_paste_is_swallowed_whole():
    k = _keys(b"\x1b[200~r c \r\x1b[201~p")
    assert _drain(k) == ["p"]


def test_paste_end_marker_split_across_reads_does_not_eat_later_keys():
    p = _Pipe()
    k = ui.Keys(p)
    p.send(b"\x1b[200~hola\x1b[20")
    assert _drain(k, 3) == []
    p.send(b"1~r")
    assert _drain(k) == ["r"]


def test_paste_start_marker_split_across_reads():
    p = _Pipe()
    k = ui.Keys(p)
    p.send(b"\x1b[20")
    p.send(b"0~texto\r\x1b[201~")
    assert _drain(k) == []


def test_esc_then_key_and_arrows_and_utf8():
    assert _drain(_keys(b"\x1br")) == ["\x1b", "r"]
    assert _drain(_keys(b"\x1b[A\x1b[Bc")) == ["c"]
    assert _drain(_keys("ñá".encode())) == ["ñ", "á"]


# ---------- flow ----------
def test_space_is_enter_only_in_ready_and_listen():
    f = F.Flow()
    assert F.step(f, F.SPACE).action is F.Act.START
    assert F.step(f, F.SPACE).action is F.Act.FINISH
    F.finished(f, "hola", 1.0)
    assert f.state is F.St.REVIEW
    assert F.step(f, F.SPACE).action is F.Act.NOOP   # a stray space must not exit REVIEW
    assert f.state is F.St.REVIEW


def test_choose_enter_explicitly_copies_last():
    f = F.Flow(state=F.St.CHOOSE)
    assert F.step(f, F.ENTER).copy_last


# ---------- corrections ----------
def test_correction_value_is_literal_text():
    import re
    rules = [(re.compile(r"(?<!\w)carpeta dev(?!\w)", re.I), r"C:\dev\nuevo")]
    assert corrections.apply("abre carpeta dev", rules) == r"abre C:\dev\nuevo"


# ---------- shell clipboard / exit ----------
def _take(sh, n, text):
    sh.flow.takes.append(F.Take(n=n, text=text))


def test_discard_restores_previous_take_in_clipboard(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    _take(sh, 2, "dos"); sh._copy("dos", 2)
    sh.flow.state = F.St.REVIEW
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "uno"


def test_discard_of_only_take_restores_clipboard_from_before_voz(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    sh.flow.state = F.St.REVIEW
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "lo que tenía antes"


def test_no_copy_never_touches_clipboard(shell):
    sh, clip = shell(); sh.args.no_copy = True
    sh.copy_mode = "nunca"
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    _take(sh, 2, "dos")
    sh.flow.state = F.St.CHOOSE
    sh.perform(F.step(sh.flow, "a"), None)
    assert clip["v"] == "lo que tenía antes"


def test_choose_enter_copies_last_even_in_ask_mode(shell):
    sh, clip = shell(); sh.copy_mode = "preguntar"
    _take(sh, 1, "uno"); _take(sh, 2, "dos")
    sh.flow.state = F.St.CHOOSE
    code, text = sh.perform(F.step(sh.flow, F.ENTER), None)
    assert (code, text, clip["v"]) == (0, "dos", "dos")


def test_choose_esc_writes_nothing_to_stdout(shell, capsys, monkeypatch):
    sh, clip = shell()
    monkeypatch.setattr(liveui.sys.stdout, "isatty", lambda: False, raising=False)
    _take(sh, 1, "uno"); _take(sh, 2, "dos")
    sh.flow.state = F.St.CHOOSE
    sh.perform(F.step(sh.flow, F.ESC), None)
    assert capsys.readouterr().out == ""


def test_esc_in_ready_is_a_normal_exit(shell):
    sh, _ = shell()
    assert sh.perform(F.step(sh.flow, F.ESC), None) == (0, "")


def test_short_audio_language_is_not_counted(shell, monkeypatch):
    import numpy as np
    sh, _ = shell()
    monkeypatch.setattr(liveui.stt, "last_language", "en", raising=False)
    sh.transcribe(np.zeros(16000, dtype="float32"), language="auto")   # 1 s
    assert sh.langs == []
    sh.transcribe(np.zeros(48000, dtype="float32"), language="auto")   # 3 s
    assert sh.langs == ["en"]


def test_ctrl_c_between_r_and_new_engine_does_not_revive_discarded_text(shell):
    sh, clip = shell()
    sh.flow.takes.append(F.Take(n=1, text="secreto", discarded=True))
    sh.flow.state = F.St.REVIEW
    sh.audio.take = 1
    sh.audio.session = types.SimpleNamespace(committed="secreto", finish=lambda **k: None)
    F.step(sh.flow, "r")                                   # take 2 exists, engine still g1's
    code, text = sh._interrupted()
    assert text == "" and "secreto" not in clip["v"]
    assert [t.n for t in sh.flow.takes] == [1]


# ---------- READY while the model loads ----------
def _fake_session(ready, error=None):
    import threading

    ev = threading.Event()
    if ready:
        ev.set()
    return types.SimpleNamespace(ready=ev, error=error)


def _plain(lines):
    return "\n".join(ui._ANSI.sub("", ln) for ln in lines)


def test_ready_hint_says_wait_while_the_model_loads(shell):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=False)
    text = _plain(sh.lines())
    assert "Cargando modelo…" in text and "esc salir" in text and "↵ hablar" not in text


def test_loading_line_counts_whole_seconds_since_the_load_began(shell, monkeypatch):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=False)
    sh.load_t0 = 100.0
    for now, shown in ((100.4, "0 s"), (103.9, "3 s"), (172.0, "72 s")):
        monkeypatch.setattr(liveui.time, "monotonic", lambda now=now: now)
        text = _plain(sh.lines())
        assert f"Cargando modelo… {shown}" in text and text.endswith("esc salir")


def test_enter_while_loading_is_ignored_with_a_visible_notice(shell):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=False)
    assert sh.on_key("\r", None) is None
    assert sh.flow.state is F.St.READY and sh.flow.takes == []     # nothing started
    assert "↵ ignorado" in _plain(sh.lines())
    sh.next_session.ready.set()                                    # the model finished loading
    text = _plain(sh.lines())
    assert "↵ hablar" in text and "ignorado" not in text


def test_model_load_failure_shows_in_ready_without_pressing_enter(shell):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=True, error=RuntimeError("sin pesos\ndetalle largo"))
    text = _plain(sh.lines())
    assert "✖" in text and "No se pudo cargar el modelo: sin pesos" in text
    assert "detalle largo" not in text and "↵ hablar" not in text


# ---------- key vocabulary ----------
def _review(sh, discarded=False):
    sh.flow.takes.append(F.Take(n=1, text="uno dos", seconds=3, discarded=discarded))
    sh.flow.state = F.St.REVIEW


@pytest.mark.parametrize("cols", [100, 60])
def test_review_hint_uses_the_new_words(shell, monkeypatch, cols):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    _review(sh)
    text = _plain(sh.lines())
    assert "c añadir" in text and "⌫ descartar" in text
    assert "seguir" not in text and "borrar" not in text and "continuar" not in text


def _recording(sh, state, cols, monkeypatch):
    import time
    from live import Segmenter

    monkeypatch.setattr(ui, "width", lambda: cols)
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = state
    sh.listen_t0 = time.monotonic()
    sh.audio.seg = Segmenter()
    sh.audio.session = types.SimpleNamespace(committed="", partial="", failed_chunks=0)
    sh.audio.rec = None
    return _plain(sh.lines()).splitlines()[0]


@pytest.mark.parametrize("cols, tail", [
    (100, "↵ terminar · p pausa · esc descartar"),
    (70, "↵ fin · p pausa · esc descartar"),
    (62, "↵ fin · p pausa · esc"),         # too narrow for the word: the short form
])
def test_listening_hint_adapts_to_the_width(shell, monkeypatch, cols, tail):
    sh, _ = shell()
    head = _recording(sh, F.St.LISTEN, cols, monkeypatch)
    assert head.endswith(tail) and len(head) <= cols - 1


@pytest.mark.parametrize("cols", [100, 80, 66])
def test_paused_hint_says_resume_never_seguir(shell, monkeypatch, cols):
    sh, _ = shell()
    head = _recording(sh, F.St.PAUSED, cols, monkeypatch)
    assert "p reanudar" in head and "seguir" not in head and len(head) <= cols - 1
    assert "esc" in head


# ---------- header ----------
def _fake_sounddevice(monkeypatch, name=None, error=None):
    def query_devices(kind=None):
        if error:
            raise error
        return {"name": name}

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(query_devices=query_devices))


def test_model_label_abbreviates_the_two_known_models():
    assert liveui._model_label("mlx-community/whisper-large-v3-turbo") == "turbo"
    assert liveui._model_label("mlx-community/whisper-large-v3-mlx") == "large-v3"
    assert liveui._model_label("org/whisper-small-mlx") == "small"


def test_header_shows_model_lang_and_mic(monkeypatch):
    _fake_sounddevice(monkeypatch, name="MacBook Pro Microphone")
    monkeypatch.setattr(ui, "width", lambda: 100)
    line = ui._ANSI.sub("", liveui.header_line("mlx-community/whisper-large-v3-turbo", "auto", liveui._mic_chip()))
    assert line == "voz  ·  turbo  ·  auto  ·  🎙 MacBook Pro Microphone"


def test_header_omits_the_mic_chip_when_the_query_fails(monkeypatch):
    _fake_sounddevice(monkeypatch, error=RuntimeError("no input device"))
    assert liveui._mic_chip() == ""
    monkeypatch.setattr(ui, "width", lambda: 100)
    assert ui._ANSI.sub("", liveui.header_line("m", "es", liveui._mic_chip())) == "voz  ·  m  ·  es"


def test_long_mic_name_is_cut_to_28_chars_with_ellipsis(monkeypatch):
    _fake_sounddevice(monkeypatch, name="Micrófono externo USB de estudio profesional 24 bits")
    chip = liveui._mic_chip()
    assert chip.startswith("🎙 ") and chip.endswith("…") and len(chip[2:]) == 28


def test_header_is_cut_by_fit_on_a_narrow_terminal(monkeypatch):
    monkeypatch.setattr(ui, "width", lambda: 30)
    line = ui._ANSI.sub("", liveui.header_line("mlx-community/whisper-large-v3-turbo", "auto", "🎙 MacBook Pro Microphone"))
    assert ui.visible_len(line) <= 29 and line.endswith("…")


# ---------- first-run tip ----------
def test_welcome_tip_shows_once_and_leaves_a_private_marker(shell, tmp_path):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=True)
    text = _plain(sh.lines())
    assert "habla con pausas cortas: voz transcribe mientras hablas · voz --help para más" in text
    marker = tmp_path / ".welcomed"
    assert marker.exists() and (marker.stat().st_mode & 0o777) == 0o600
    assert "habla con pausas" in _plain(sh.lines())          # same run: still there while in Listo
    sh2, _ = shell()
    sh2.next_session = _fake_session(ready=True)
    text2 = _plain(sh2.lines())
    assert "↵ hablar" in text2 and "habla con pausas" not in text2


def test_welcome_tip_is_not_shown_while_loading(shell, tmp_path):
    sh, _ = shell()
    sh.next_session = _fake_session(ready=False)
    assert "habla con pausas" not in _plain(sh.lines())
    assert not (tmp_path / ".welcomed").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_welcome_tip_survives_an_unwritable_state_dir(shell, tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setattr(liveui.autosave, "STATE_DIR", locked)
    locked.chmod(0o500)
    try:
        sh, _ = shell()
        sh.next_session = _fake_session(ready=True)
        assert "habla con pausas" in _plain(sh.lines())      # shown anyway, no error
        assert not (locked / ".welcomed").exists()
    finally:
        locked.chmod(0o700)


# ---------- finishing ----------
@pytest.mark.parametrize("secs, pending, expected", [
    (0.2, 0, "Terminando… 0 s"),
    (3.9, 1, "Terminando… 3 s · 1 tramo pendiente"),
    (12, 3, "Terminando… 12 s · 3 tramos pendientes"),
])
def test_finishing_label(secs, pending, expected):
    assert liveui.finishing_label(secs, pending) == expected


def test_finish_redraws_with_elapsed_seconds_and_pending_chunks(shell, monkeypatch):
    import threading
    from live import Segmenter

    sh, _ = shell()
    release = threading.Event()
    clock = {"t": 50.0}
    monkeypatch.setattr(liveui.time, "monotonic", lambda: clock["t"])
    seen = []

    def draw(lines):
        seen.append(_plain(lines).splitlines()[0])
        clock["t"] += 2.0
        if len(seen) == 3:
            release.set()

    sh.region.draw = draw
    sh.audio.seg = Segmenter()
    sh.audio.session = types.SimpleNamespace(committed="", partial="", pending=2, abandoned=False,
                                             failed_chunks=0, drain=lambda timeout=None: release.wait(), submit=lambda c: None)
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.FINISHING
    sh._finish(None)
    assert "Terminando… 0 s · 2 tramos pendientes" in seen[0] and "Ctrl-C otra vez" in seen[0]
    assert "Terminando… 2 s · 2 tramos pendientes" in seen[1]


# ---------- bounded closing, exit code on Ctrl-C ----------
def _stuck_shell(shell, tmp_path, monkeypatch, timeout):
    """A Shell whose transcriber never returns, with a real recorder on disk."""
    import threading

    import autosave
    from live import LiveSession, Segmenter

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.setattr(liveui, "FINISH_TIMEOUT_S", timeout)
    monkeypatch.setattr(liveui, "CLOSE_JOIN_S", 0.1)
    sh, _ = shell()
    gate = threading.Event()
    calls = []

    def transcribe(audio, **kw):
        calls.append(1)
        if len(calls) > 1:                      # the 1st call is the warm-up
            gate.wait(30)
        return "texto"

    import numpy as np

    sh.audio.session = LiveSession(transcribe, [], lambda t, r: t, "m", "es", echo=False, warn=False)
    sh.audio.session.ready.wait(2)
    sh.audio.seg = Segmenter()
    sh.audio.rec = autosave.Session(run_id="sesion-20261029-100000", take=1)
    sh.audio.rec.write_audio(np.full(1600, 0.1, dtype="float32"))
    sh.audio.session.submit(np.zeros(1600, dtype="float32"))
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.FINISHING
    sh.region.draw = lambda lines: None
    return sh, gate


def test_finish_gives_up_after_the_deadline_and_leaves_the_session_recoverable(shell, tmp_path, monkeypatch):
    import time

    import autosave
    import record

    sh, gate = _stuck_shell(shell, tmp_path, monkeypatch, timeout=0.3)
    t0 = time.perf_counter()
    sh._finish(None)
    assert time.perf_counter() - t0 < 3
    assert sh.incomplete and sh.audio.session.abandoned
    shown = _plain(sh.lines())          # nothing was transcribed: the screen says so and how to recover
    assert "No se pudo transcribir" in shown and "No se detectó voz" not in shown
    assert str(sh.audio.rec.wav_path) in shown and "voz \"" in shown
    sh._cleanup()
    assert sh.audio.rec.lock_path.exists()                       # not removed: recoverable
    assert [p.name for p in autosave.unfinished()] == [sh.audio.rec.base.name]
    assert any("pendiente" in r for r in record.hard_exit_reasons())
    record._HARD_EXIT.clear()
    gate.set()


def test_second_ctrl_c_forces_the_exit_before_the_deadline(shell, tmp_path, monkeypatch):
    import threading
    import time

    import record

    sh, gate = _stuck_shell(shell, tmp_path, monkeypatch, timeout=60)
    threading.Timer(0.3, lambda: setattr(sh, "force_exit", True)).start()
    t0 = time.perf_counter()
    sh._finish(None)
    assert time.perf_counter() - t0 < 3
    assert sh.incomplete
    sh._cleanup()
    assert sh.audio.rec.lock_path.exists()
    record._HARD_EXIT.clear()
    gate.set()


def test_finish_that_completes_in_time_closes_the_session_clean(shell, tmp_path, monkeypatch):
    sh, gate = _stuck_shell(shell, tmp_path, monkeypatch, timeout=5)
    gate.set()                                                   # the transcriber answers
    sh._finish(None)
    assert not sh.incomplete and sh.flow.current.text == "texto"
    sh._cleanup()
    assert not sh.audio.rec.lock_path.exists()


def test_ctrl_c_exit_code_is_130_even_with_text_and_the_text_is_kept(shell):
    sh, clip = shell()
    sh.flow.takes.append(F.Take(n=1, text="lo dictado"))
    sh.flow.state = F.St.REVIEW
    code, text = sh._interrupted()
    assert (code, text) == (130, "lo dictado")
    assert clip["v"] == "lo dictado"                             # copy mode auto


def test_ctrl_c_with_nothing_recorded_is_also_130(shell):
    sh, _ = shell()
    assert sh._interrupted() == (130, "")


def test_welcome_marker_is_created_exclusively_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(liveui.autosave, "STATE_DIR", tmp_path)
    assert liveui._first_run() is True
    assert (tmp_path / ".welcomed").stat().st_mode & 0o777 == 0o600
    assert liveui._first_run() is False
    real = liveui.os.open
    seen = []

    def spy(path, flags, *a):
        seen.append(flags)
        return real(path, flags, *a)

    monkeypatch.setattr(liveui.os, "open", spy)
    (tmp_path / ".welcomed").unlink()
    liveui._first_run()
    assert seen and seen[0] & os.O_EXCL
