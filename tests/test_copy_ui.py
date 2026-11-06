"""Copy without leaving the screen (`s`, history from READY/NOVOICE, honest
backup line, the replaced-clipboard note once, narrow history)."""
import threading
import types
from pathlib import Path

import pytest

import flow as F
import liveui
import ui


def plain(lines):
    return [ui._ANSI.sub("", ln) for ln in lines]


def text(sh):
    return "\n".join(plain(sh.lines()))


def _review(sh, copied, mode="auto"):
    sh.copy_mode = mode
    sh.flow.takes.append(F.Take(n=1, text="uno dos tres", seconds=5))
    sh.flow.state = F.St.REVIEW
    sh.copied = copied


# ---------- `s` is always there when there is text ----------
@pytest.mark.parametrize("mode,copied,label", [
    ("auto", True, "s copiar de nuevo"),        # already copied
    ("auto", False, "s copiar"),                # the copy failed
    ("preguntar", None, "s copiar"),            # not copied yet
    ("auto", None, "s copiar"),                 # e.g. after copying something else from the history
])
def test_s_is_offered_with_text_in_every_copy_mode(shell, monkeypatch, mode, copied, label):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 120)
    _review(sh, copied, mode=mode)
    shown = text(sh)
    assert label in shown
    if label == "s copiar":
        assert "s copiar de nuevo" not in shown


def test_s_copies_again_in_auto_and_after_a_failed_copy(shell, monkeypatch):
    sh, clip = shell()
    _review(sh, copied=True)
    clip["v"] = "otra cosa"
    sh.perform(F.step(sh.flow, "s"), None)
    assert clip["v"] == "uno dos tres" and sh.copied is True


def test_s_is_not_offered_with_no_copy_or_without_valid_text(shell, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 120)
    _review(sh, None)
    sh.args.no_copy = True
    assert "s copiar" not in text(sh)
    sh.args.no_copy = False
    sh.flow.current.discarded = True
    sh.audio.rec = types.SimpleNamespace(wav_path=Path("/x/sesion-1-g1.wav"))
    assert "s copiar" not in text(sh)


# ---------- history from READY and NOVOICE ----------
def _seed(state, run, n, *lines):
    (state / f"sesion-{run}-g{n}.txt").write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")


def test_flow_opens_and_closes_the_history_from_ready_and_novoice():
    for origin in (F.St.READY, F.St.NOVOICE):
        f = F.Flow(state=origin, takes=[F.Take(n=1, novoice=True)] if origin is F.St.NOVOICE else [])
        assert F.step(f, "h").action is F.Act.HISTORY_OPEN and f.state is F.St.HISTORY
        F.history_loaded(f, 3)
        F.step(f, F.ESC)
        assert f.state is origin
        F.step(f, "h"); F.history_loaded(f, 3)
        assert F.step(f, F.ENTER).action is F.Act.HISTORY_COPY and f.state is origin
        F.step(f, "h"); F.history_loaded(f, 0)       # nothing saved: straight back
        assert f.state is origin


def test_history_from_ready_copies_and_returns_to_ready(shell, tmp_path, monkeypatch):
    sh, clip = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    sh.next_session = types.SimpleNamespace(ready=threading.Event(), error=None)
    sh.next_session.ready.set()
    _seed(tmp_path, "20261103-090000", 1, "nota guardada de ayer")
    assert "h historial" in text(sh) and "↵ hablar" in text(sh)
    sh.on_key("h", None)
    assert sh.flow.state is F.St.HISTORY and plain(sh.lines())[0].startswith("Historial")
    sh.on_key("\r", None)
    assert sh.flow.state is F.St.READY and clip["v"] == "nota guardada de ayer"
    assert "✓  Copiado · sesión 2026-11-03 09:00" in text(sh)
    sh.on_key("x", None)                              # the one-line feedback lasts until the next key
    assert "Copiado" not in text(sh)


def test_history_without_recordings_gives_a_short_notice_not_an_empty_screen(shell, tmp_path, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    sh.next_session = types.SimpleNamespace(ready=threading.Event(), error=None)
    sh.next_session.ready.set()
    sh.on_key("h", None)
    assert sh.flow.state is F.St.READY and "No hay grabaciones guardadas" in text(sh)
    sh.on_key("x", None)
    assert "No hay grabaciones" not in text(sh)


def test_history_from_novoice_is_offered_and_works(shell, tmp_path, monkeypatch):
    sh, clip = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    _seed(tmp_path, "20261103-090000", 1, "nota guardada de ayer")
    sh.flow.takes.append(F.Take(n=1, seconds=3, novoice=True))
    sh.flow.state = F.St.NOVOICE
    sh.audio.rec = types.SimpleNamespace(wav_path=tmp_path / "x-g1.wav", audio_failed=None)
    assert "h historial" in text(sh)
    sh.on_key("h", None)
    sh.on_key("\r", None)
    assert sh.flow.state is F.St.NOVOICE and clip["v"] == "nota guardada de ayer"
    assert "No se detectó voz" in text(sh) and "✓  Copiado" in text(sh)


def test_no_copy_offers_no_history_anywhere(shell, tmp_path, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    sh.args.no_copy = True
    _seed(tmp_path, "20261103-090000", 1, "nota")
    sh.next_session = types.SimpleNamespace(ready=threading.Event(), error=None)
    sh.next_session.ready.set()
    assert "historial" not in text(sh)
    sh.on_key("h", None)
    assert sh.flow.state is F.St.READY


# ---------- the backup line tells the truth ----------
def _rec(**kw):
    base = dict(mark=lambda n: None, close=lambda clean=True: None, audio_failed=None, text_failed=None,
                wav_path=Path.home() / ".local/state/voz/sesion-20261029-100000-g1.wav")
    return types.SimpleNamespace(**{**base, **kw})


def _backup(sh, monkeypatch, cols=120, **rec):
    monkeypatch.setattr(ui, "width", lambda: cols)
    _review(sh, True)
    sh.audio.rec = _rec(**rec)
    return [ln for ln in plain(sh.lines()) if "Respaldo" in ln]


def test_review_has_its_own_backup_line_with_the_short_path(shell, monkeypatch):
    sh, _ = shell()
    assert _backup(sh, monkeypatch) == ["Respaldo: ~/.local/state/voz/sesion-20261029-100000-g1  ·  texto y audio"]


def test_backup_line_reflects_what_failed(shell, monkeypatch):
    sh, _ = shell()
    only_text = _backup(sh, monkeypatch, audio_failed=OSError("disco lleno"))
    assert only_text == ["Respaldo: ~/.local/state/voz/sesion-20261029-100000-g1.txt  ·  el audio no se pudo guardar"]
    sh, _ = shell()
    only_audio = _backup(sh, monkeypatch, text_failed=OSError("disco lleno"))
    assert only_audio == ["Respaldo: ~/.local/state/voz/sesion-20261029-100000-g1.wav  ·  el texto no se pudo guardar"]
    sh, _ = shell()
    both = _backup(sh, monkeypatch, audio_failed=OSError("x"), text_failed=OSError("y"))
    assert both == ["⚠  Respaldo: no se pudo guardar ni el texto ni el audio"]


def test_backup_line_uses_only_the_name_on_a_narrow_terminal(shell, monkeypatch):
    sh, _ = shell()
    line = _backup(sh, monkeypatch, cols=60)
    assert len(line) == 1 and "~/" not in line[0] and "sesion-20261029-100000-g1" in line[0] and len(line[0]) <= 59


def test_incomplete_review_shows_recovery_not_a_duplicate_backup(shell, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 120)
    _review(sh, True)
    sh.audio.rec, sh.lost = _rec(), 2
    shown = text(sh)
    assert "Para transcribirlo entero" in shown and "Respaldo" not in shown
    sh.audio.rec = _rec(audio_failed=OSError("x"))          # audio gone: no recovery promise, the truth instead
    shown = text(sh)
    assert "Para transcribirlo entero" not in shown and "el audio no se pudo guardar" in shown


def test_novoice_never_says_the_audio_was_saved_when_it_failed(shell, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    sh.flow.takes.append(F.Take(n=1, seconds=3, novoice=True))
    sh.flow.state = F.St.NOVOICE
    sh.audio.rec = _rec()
    assert "el audio quedó guardado por si acaso" in text(sh)
    sh.audio.rec = _rec(audio_failed=OSError("disco lleno"))
    shown = text(sh)
    assert "quedó guardado" not in shown and "el audio no se pudo guardar" in shown


def test_discard_notice_does_not_promise_a_failed_audio(shell, monkeypatch):
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: 100)
    _review(sh, None)
    sh.flow.current.discarded = True
    sh.audio.rec = _rec(audio_failed=OSError("x"))
    assert "el audio sigue en" not in text(sh) and "el audio no se pudo guardar" in text(sh)


# ---------- "(reemplazó tu portapapeles)" belongs to the copy event ----------
def test_replaced_note_shows_once_per_copy(shell, monkeypatch, tmp_path):
    sh, _ = shell()
    _seed(tmp_path, "20261103-090000", 1, "nota guardada")
    monkeypatch.setattr(ui, "width", lambda: 120)
    sh.flow.takes.append(F.Take(n=1, text="uno dos", seconds=3))
    sh.flow.state = F.St.REVIEW
    sh._copy("uno dos", 1)                              # the copy event (auto mode, end of recording)
    assert "(reemplazó tu portapapeles)" in text(sh)
    assert "(reemplazó tu portapapeles)" in text(sh)    # redraws before any key keep it: it is the same event
    sh.on_key(None, None)
    assert "(reemplazó tu portapapeles)" in text(sh)    # idle frames are not events either
    sh.on_key("h", None)                                # any key afterwards: gone
    sh.on_key("\x1b", None)                             # esc: back from the history
    assert "Copiado" in text(sh) and "reemplazó" not in text(sh)
    sh.on_key("s", None)                                # a new copy is a new event
    assert "(reemplazó tu portapapeles)" in text(sh)


def test_summary_line_can_omit_the_note():
    kw = dict(compact=False)
    assert "reemplazó" in ui._ANSI.sub("", liveui.summary_line(True, 3, "a b", ["es"], "es", **kw))
    assert "reemplazó" not in ui._ANSI.sub("", liveui.summary_line(True, 3, "a b", ["es"], "es", replaced=False, **kw))


# ---------- narrow history keeps the first words, not the date ----------
@pytest.mark.parametrize("cols", [20, 24, 30])
def test_narrow_history_drops_the_date_before_the_first_words(shell, monkeypatch, tmp_path, cols):
    _seed(tmp_path, "20261103-090000", 1, "reunión de presupuesto con el equipo")
    sh, _ = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    sh.flow.takes.append(F.Take(n=1, text="actual", seconds=2))
    sh.flow.state = F.St.REVIEW
    sh.perform(F.step(sh.flow, "h"), None)
    rows = [ui.fit(ln, cols - 1) for ln in plain(sh.lines()) if "«" in ln]
    assert len(rows) == 1 and "«reun" in rows[0]            # identifying words survive
    assert "11-03" not in rows[0]                            # the date went first
    assert ui.visible_len(rows[0]) <= cols - 1
