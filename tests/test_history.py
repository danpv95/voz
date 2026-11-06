"""Review screen extras: the discarded-silences line and the `h` history view."""
import os
import types
import wave

import pytest

import autosave
import flow as F
import liveui
import ui
from liveui import gated_text, history_lines, summary_line


def plain(lines):
    return [ui._ANSI.sub("", ln) for ln in lines]


# ---------- silences line ----------
def test_gated_text_pluralises_and_stays_quiet_at_zero():
    assert gated_text(0) == "" and gated_text(-1) == ""
    assert gated_text(1) == "1 tramo omitido por nivel bajo"
    assert gated_text(3) == "3 tramos omitidos por nivel bajo"
    assert gated_text(3, diag=True) == "3 tramos omitidos por nivel bajo (ver diag.log)"
    assert "silenc" not in gated_text(3)         # the level gate cannot tell silence from quiet speech


def test_summary_line_appends_the_silences_last():
    base = ("uno dos tres", ["es", "en"], "auto")
    line = ui._ANSI.sub("", summary_line(True, 15.2, base[0], base[1], base[2], compact=False,
                                         gated=gated_text(3)))
    assert line.endswith("15 s  ·  3 palabras  ·  es+en  ·  3 tramos omitidos por nivel bajo")
    assert "omitido" not in ui._ANSI.sub("", summary_line(True, 15.2, *base, compact=False))


def _review(shell, monkeypatch, gated, cols=120, diag=None):
    sh, clip = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    monkeypatch.delenv("VOZ_DIAG", raising=False)
    if diag:
        monkeypatch.setenv("VOZ_DIAG", diag)
    sh.flow.takes.append(F.Take(n=1, text="uno dos tres", seconds=15))
    sh.flow.state = F.St.REVIEW
    sh.audio.session = types.SimpleNamespace(gated=gated)
    sh.copied = True
    return sh


@pytest.mark.parametrize("gated,expected", [(0, None), (1, "1 tramo omitido por nivel bajo"), (3, "3 tramos omitidos por nivel bajo")])
def test_review_reports_discarded_silences(shell, monkeypatch, gated, expected):
    text = "\n".join(plain(_review(shell, monkeypatch, gated).lines()))
    assert (expected in text) if expected else ("omitido" not in text)


def test_review_points_to_diag_log_only_with_voz_diag(shell, monkeypatch):
    assert "(ver diag.log)" not in "\n".join(plain(_review(shell, monkeypatch, 2).lines()))
    assert "(ver diag.log)" in "\n".join(plain(_review(shell, monkeypatch, 2, diag="1").lines()))


def test_review_silences_move_to_their_own_line_when_narrow(shell, monkeypatch):
    lines = plain(_review(shell, monkeypatch, 3, cols=50, diag="1").lines())
    assert all(len(ln) <= 49 for ln in lines)
    assert any(ln.strip() == "3 tramos omitidos por nivel bajo (ver diag.log)" for ln in lines)
    assert "omitido" not in lines[0]


def test_incomplete_review_still_reports_silences(shell, monkeypatch):
    sh = _review(shell, monkeypatch, 3)
    sh.lost = 1
    sh.audio.rec.wav_path = __import__("pathlib").Path("/tmp/grabacion.wav")
    assert "3 tramos omitidos por nivel bajo" in "\n".join(plain(sh.lines()))


# ---------- flow: history state ----------
def review_flow():
    return F.Flow(state=F.St.REVIEW, takes=[F.Take(n=1, text="hola mundo", seconds=3)])


def test_h_opens_history_and_empty_list_returns_to_review():
    f = review_flow()
    assert F.step(f, "h").action is F.Act.HISTORY_OPEN and f.state is F.St.HISTORY
    F.history_loaded(f, 0)
    assert f.state is F.St.REVIEW


def test_history_navigation_is_clamped_and_numbers_jump():
    f = review_flow()
    F.step(f, "h")
    F.history_loaded(f, 4)
    assert (f.state, f.hist_sel) == (F.St.HISTORY, 0)
    F.step(f, F.UP)
    assert f.hist_sel == 0
    for _ in range(9):
        F.step(f, F.DOWN)
    assert f.hist_sel == 3
    F.step(f, "2")
    assert f.hist_sel == 1
    F.step(f, "9")                      # beyond the list: ignored
    F.step(f, "0")
    assert f.hist_sel == 1


def test_history_enter_copies_the_choice_and_goes_back_to_review():
    f = review_flow()
    F.step(f, "h")
    F.history_loaded(f, 3)
    F.step(f, F.DOWN)
    st = F.step(f, F.ENTER)
    assert st.action is F.Act.HISTORY_COPY and f.state is F.St.REVIEW and f.hist_sel == 1
    assert [t.text for t in f.takes] == ["hola mundo"] and not f.current.discarded


@pytest.mark.parametrize("key", [F.ESC, "h"])
def test_history_esc_and_h_go_back_without_touching_the_session(key):
    f = review_flow()
    F.step(f, "h")
    F.history_loaded(f, 3)
    F.step(f, F.DOWN)
    assert F.step(f, key).action is F.Act.NOOP and f.state is F.St.REVIEW
    assert len(f.takes) == 1 and f.current.valid


def test_history_ignores_the_other_keys():
    f = review_flow()
    F.step(f, "h")
    F.history_loaded(f, 3)
    for k in ("r", "c", "u", "p", "s", F.BACKSPACE, F.SPACE):
        assert F.step(f, k).action is F.Act.NOOP and f.state is F.St.HISTORY


def test_h_does_nothing_while_recording_or_choosing():
    for st in (F.St.LISTEN, F.St.PAUSED, F.St.FINISHING, F.St.CHOOSE):
        f = F.Flow(state=st, takes=[F.Take(n=1, text="a", seconds=1)])
        assert F.step(f, "h").action is F.Act.NOOP and f.state is st


def test_history_is_capped():
    f = review_flow()
    F.step(f, "h")
    F.history_loaded(f, 500)
    assert f.hist_size == F.HISTORY_MAX


# ---------- arrow keys ----------
class _Pipe:
    def __init__(self):
        self.r, self.w = os.pipe()

    def fileno(self):
        return self.r


def _read_all(*chunks):
    p = _Pipe()
    k = liveui.ArrowKeys(p)
    for c in chunks:
        os.write(p.w, c)
    out = []
    for _ in range(12):
        ch = k.read(0.02)
        if ch is not None:
            out.append(ch)
    return out


def test_arrow_keys_are_named_and_bare_esc_still_works():
    assert _read_all(b"\x1b[A\x1b[B\x1bOA\x1bOB") == [F.UP, F.DOWN, F.UP, F.DOWN]
    assert _read_all(b"\x1b") == ["\x1b"]
    assert _read_all(b"\x1b[C\x1b[Dr") == ["r"]                 # left/right stay dropped
    assert _read_all(b"\x1b[200~\x1b[A\x1b[201~r") == ["r"]     # inside a paste they are text


# ---------- history view ----------
def mk(state, run, n, text, secs=None):
    stem = state / f"sesion-{run}-g{n}"
    stem.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    if secs is not None:
        with wave.open(str(stem.with_suffix(".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * int(16000 * secs))


def _seeded(shell, monkeypatch, tmp_path, cols=100):
    mk(tmp_path, "20261103-090000", 1, "reunión de presupuesto con el equipo de compras y ventas", 75)
    mk(tmp_path, "20261103-120000", 1, "primera nota del día", 15)
    mk(tmp_path, "20261103-120000", 2, "texto descartado por el usuario")
    (tmp_path / "sesion-20261103-120000-g2.txt").write_text("x\n[descartada]\n", encoding="utf-8")
    sh, clip = shell()
    monkeypatch.setattr(ui, "width", lambda: cols)
    sh.flow.takes.append(F.Take(n=1, text="grabación actual", seconds=4))
    sh.flow.state = F.St.REVIEW
    sh.copied = True
    sh.clip_owner = 1
    return sh, clip


def _press(sh, *keys):
    for k in keys:
        sh.perform(F.step(sh.flow, k), None)


def test_history_lists_the_same_recordings_as_copiar_valid_first_then_discarded(shell, monkeypatch, tmp_path):
    sh, _ = _seeded(shell, monkeypatch, tmp_path)
    _press(sh, "h")
    lines = plain(sh.lines())
    assert lines[0].startswith("Historial") and "1 de 3" in lines[0]
    assert lines[1].startswith("▸  1  2026-11-03 12:00") and "0:15" in lines[1] and "3 palabras" not in lines[1]
    assert "4 palabras" in lines[1] and "«primera nota del día»" in lines[1]
    assert lines[2].startswith("   2  2026-11-03 09:00") and "1:15" in lines[2]
    assert "«reunión de presupuesto con el equipo…»" in lines[2]            # first 6 words
    assert lines[3].startswith("   3") and "descartada" in lines[3] and "«x»" in lines[3]    # last, same numbering for the valid ones
    assert all("descartada" not in ln for ln in lines[1:3])
    assert "↑↓ elegir" in lines[-1] and "↵ copiar" in lines[-1] and "esc/h volver" in lines[-1]


def test_history_copy_uses_the_standard_message_and_keeps_the_session(shell, monkeypatch, tmp_path):
    sh, clip = _seeded(shell, monkeypatch, tmp_path)
    _press(sh, "h", F.DOWN, F.ENTER)
    assert clip["v"].startswith("reunión de presupuesto")
    assert sh.flow.state is F.St.REVIEW and sh.flow.current.text == "grabación actual" and sh.flow.current.valid
    text = "\n".join(plain(sh.lines()))
    assert "✓  Copiado · sesión 2026-11-03 09:00 · grabación 1 · 10 palabras" in text
    assert "Cmd-V" not in text                      # the current take is no longer what is copied
    assert "Grabación actual sin copiar" in text


def test_history_feedback_when_current_is_discarded(shell, monkeypatch, tmp_path):
    sh, _ = _seeded(shell, monkeypatch, tmp_path, cols=40)
    sh.flow.current.discarded = True
    sh.audio.rec.wav_path = tmp_path / "grabacion.wav"
    _press(sh, "h", F.ENTER)
    text = "\n".join(plain(sh.lines()))
    assert "Grabación 1 descartada" in text and "✓  Copiado" in text
    assert "grabación 1" in text


def test_history_fit_keeps_emoji_and_combining_clusters_intact():
    for cluster in ("👩🏽\u200d💻", "🇨🇴", "n\u0303"):
        for width in range(1, 15):
            result = liveui._history_fit("x" + cluster + "y", width)
            assert cluster in result or not any(ch in result for ch in cluster)
            assert ui.visible_len(result) <= width


def test_history_esc_changes_nothing(shell, monkeypatch, tmp_path):
    sh, clip = _seeded(shell, monkeypatch, tmp_path)
    before = clip["v"]
    _press(sh, "h", F.DOWN, F.ESC)
    assert clip["v"] == before and sh.copied is True and sh.clip_owner == 1
    assert plain(sh.lines())[0].startswith("✓  Copiado")


def test_leaving_after_a_history_copy_keeps_the_choice(shell, monkeypatch, tmp_path):
    sh, clip = _seeded(shell, monkeypatch, tmp_path)       # copy mode is auto
    _press(sh, "h", F.DOWN, F.ENTER)
    code, _ = sh.perform(F.step(sh.flow, F.ENTER), None)
    assert code == 0 and clip["v"].startswith("reunión de presupuesto")


def test_discarding_after_a_history_copy_does_not_touch_the_clipboard(shell, monkeypatch, tmp_path):
    sh, clip = _seeded(shell, monkeypatch, tmp_path)
    _press(sh, "h", F.DOWN, F.ENTER, F.BACKSPACE)
    assert clip["v"].startswith("reunión de presupuesto")


def test_history_without_saved_recordings_says_so(shell, monkeypatch, tmp_path):
    sh, _ = shell()
    sh.flow.takes.append(F.Take(n=1, text="a", seconds=1))
    sh.flow.state = F.St.REVIEW
    _press(sh, "h")
    assert sh.flow.state is F.St.REVIEW and "No hay grabaciones guardadas" in "\n".join(plain(sh.lines()))


def test_no_copy_hides_the_history(shell, monkeypatch, tmp_path):
    sh, _ = _seeded(shell, monkeypatch, tmp_path)
    sh.args.no_copy = True
    assert "h historial" not in "\n".join(plain(sh.lines()))
    _press(sh, "h")
    assert sh.flow.state is F.St.REVIEW


def test_review_hints_offer_h(shell, monkeypatch, tmp_path):
    sh, _ = _seeded(shell, monkeypatch, tmp_path)
    assert "h historial" in "\n".join(plain(sh.lines()))
    sh.flow.current.discarded = True
    sh.audio.rec = types.SimpleNamespace(wav_path=tmp_path / "sesion-x-g1.wav")
    assert "h historial" in "\n".join(plain(sh.lines()))


@pytest.mark.parametrize("cols", [20, 30, 40, 60, 100])
def test_history_fits_any_width_and_marks_the_selection(shell, monkeypatch, tmp_path, cols):
    sh, _ = _seeded(shell, monkeypatch, tmp_path, cols=cols)
    _press(sh, "h")
    lines = plain(ui.fit(ln, cols - 1) for ln in sh.lines())     # what Region.draw does
    assert all(len(ln) <= cols - 1 for ln in lines)
    assert sum(ln.startswith("▸") for ln in lines) == 1 and "esc" in lines[-1]


def test_history_scrolls_around_the_selection():
    items = [(autosave.Recording("sesion-20261103-120000", n, None, f"nota {n}"), 5.0) for n in range(1, 16)]
    shown = lambda sel: [ln for ln in plain(history_lines(items, sel, rows=5)) if ln[2:4].strip().isdigit()]  # noqa: E731
    first, last = shown(0), shown(14)
    assert len(first) == len(last) == 5
    assert first[0].split()[1] == "1" and last[-1].split()[1] == "15"
    assert any(ln.startswith("▸") and ln.split()[1] == "15" for ln in last)


def test_autosave_helpers_format_like_copiar():
    assert autosave.when("sesion-20261103-120037") == "2026-11-03 12:00"
    assert autosave.when("raro") == "raro"
    assert (autosave.clock(75.4), autosave.clock(0), autosave.clock(None)) == ("1:15", "0:00", "-")
    assert autosave.head("a b c") == "a b c" and autosave.head("a b c d e f g") == "a b c d e f…"
