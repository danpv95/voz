import io
import re
import time

import numpy as np
import pytest

import ui
from live import LiveSession

strip = lambda s: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)


def test_fmt_time():
    assert ui.fmt_time(0) == "00:00" and ui.fmt_time(75.9) == "01:15"


def test_meter_has_fixed_width_and_scales():
    assert len(ui.meter([])) == 10
    quiet, loud = ui.meter([1e-6] * 10), ui.meter([0.5] * 10)
    assert set(quiet) == {"▁"} and set(loud) == {"█"}


def test_meter_silence_is_dots_and_voice_is_bars(monkeypatch):
    for color in (False, True):
        monkeypatch.setattr(ui, "_COLOR", color)
        silent = strip(ui.meter([0.0005] * 10, [False] * 10))
        assert silent == "·" * 10
        voiced = strip(ui.meter([0.05] * 10, [True] * 10))
        assert set(voiced) <= set(ui.BARS) and len(voiced) == 10
        mixed = strip(ui.meter([0.0005] * 5 + [0.2] * 5, [False] * 5 + [True] * 5))
        assert mixed[:5] == "·" * 5 and set(mixed[5:]) <= set(ui.BARS)


def test_meter_voice_is_accent_and_silence_is_gray(monkeypatch):
    monkeypatch.setattr(ui, "_COLOR", True)
    out = ui.meter([0.5, 0.0005], [True, False], n=2)
    assert f"\033[{ui.ACCENT}m" in out and f"\033[{ui.GRAY}m·" in out


def test_meter_pads_short_history_with_dots_and_keeps_old_form_without_voiced():
    assert strip(ui.meter([0.3], [True])) == "·" * 9 + "█"
    assert len(ui.meter([0.1] * 3)) == 10 and "·" not in ui.meter([0.1] * 3)


def test_wrap_keeps_last_lines_and_marks_partial():
    lines = ui.wrap_transcript("uno dos tres " * 20, "cuatro", cols=30, max_lines=3)
    assert len(lines) == 3
    last = strip(lines[-1]).strip()
    assert last.endswith("cuatro") or last.endswith("‹cuatro›")   # marked when there is no colour


def test_wrap_empty():
    assert ui.wrap_transcript("", "", 80, 5) == []


def test_region_rewinds_what_it_drew():
    out = io.StringIO()
    r = ui.Region(stream=out)
    r.draw(["a", "b", "c"])
    r.draw(["x"])
    # second draw must move up the 2 extra lines of the first one and clear
    assert "\033[2A\033[J" in out.getvalue()


def test_partial_text_appears_before_the_chunk_closes():
    audio = np.zeros(16000 * 2, dtype="float32")
    calls = []

    def fake(a, model, language):
        calls.append(len(a))
        return "" if len(calls) == 1 else "texto parcial"   # 1st = warm-up

    s = LiveSession(fake, [], lambda t, r: t, "m", "es", echo=False,
                    partial_source=lambda: audio)
    deadline = time.time() + 3
    while not s.partial and time.time() < deadline:
        time.sleep(0.02)
    assert s.partial == "texto parcial"
    s.submit(audio)                   # chunk closes → partial cleared, text committed
    assert s.finish() == "texto parcial" and s.partial == ""


def test_partial_not_recomputed_without_new_audio():
    audio = np.zeros(16000 * 2, dtype="float32")
    n = {"calls": 0}

    def fake(a, model, language):
        n["calls"] += 1
        return "x"

    s = LiveSession(fake, [], lambda t, r: t, "m", "es", echo=False,
                    partial_source=lambda: audio)
    time.sleep(0.5)
    s.finish()
    assert n["calls"] == 2  # warm-up + one partial; same audio length → no re-run


def test_partials_use_the_primary_language_not_auto():
    audio = np.zeros(16000 * 2, dtype="float32")
    seen = []

    def fake(a, model, language, lang_state=None):
        seen.append(language)
        return "x"

    grow = {"n": 16000}

    def source():
        grow["n"] += 16000          # new audio keeps arriving → more partials
        return np.zeros(grow["n"], dtype="float32")

    s = LiveSession(fake, [], lambda t, r: t, "m", "auto", echo=False,
                    partial_source=source, partial_lang=lambda: "en")
    time.sleep(0.4)
    s.submit(audio)
    s.finish()
    assert seen[0] == "es"              # warm-up never auto-detects on silence
    assert seen[1:-1] and set(seen[1:-1]) == {"es"}   # partials never detect: primary language
    assert seen[-1] == "auto"           # the final chunk detects (sticky, see LangState)


def test_summary_line():
    from liveui import summary_line

    line = strip(summary_line(True, 15.2, "uno dos tres", ["es", "en", "es"], "auto", compact=False))
    assert "Copiado" in line and "15 s" in line and "3 palabras" in line and "es+en" in line
    assert "1:05 min" in strip(summary_line(None, 65, "a", [], "es"))
    short = strip(summary_line(True, 15.2, "uno dos tres", ["es"], "auto", compact=True))
    assert "reemplazó" not in short and "3 palabras" in short and len(short) < 45


def test_slow_partials_space_themselves_out():
    """A guess that costs 0.3 s must wait for ≥0.36 s of new audio before the next one."""
    grow = {"n": 16000}

    def source():
        grow["n"] += 1600            # +0.1 s of audio per worker poll
        return np.zeros(grow["n"], dtype="float32")

    calls = []

    def slow(a, model, language):
        if calls or len(a) > 8000:
            time.sleep(0.3)
        calls.append(len(a))
        return "x"

    s = LiveSession(slow, [], lambda t, r: t, "m", "es", echo=False, partial_source=source)
    time.sleep(2.0)
    s.finish()
    lens = calls[1:]                  # skip warm-up
    gaps = [b - a for a, b in zip(lens, lens[1:])]
    assert gaps and min(gaps) >= int(0.36 * 16000)


def test_wrap_cost_does_not_grow_with_session_length():
    import time as _t

    short = " ".join(["uno dos tres cuatro"] * 50)
    long_ = " ".join(["uno dos tres cuatro"] * 20000)          # ~80k words
    assert strip(ui.wrap_transcript(short, "x", 80, 6)[-1]).strip().rstrip("›").endswith("x")
    lines = ui.wrap_transcript(long_, "fin", 80, 6)
    assert len(lines) == 6 and strip(lines[-1]).strip().rstrip("›").endswith("fin")
    t = _t.perf_counter()
    for _ in range(50):
        ui.wrap_transcript(long_, "fin", 80, 6)
    assert (_t.perf_counter() - t) / 50 < 0.005               # constant, a few ms at most


def test_fit_never_exceeds_width_and_keeps_colors_closed():
    line = ui.paint(ui.RED, "●") + "  Escuchando  01:13  " + ui.paint(ui.GRAY, "↵ terminar · p pausa · esc descartar")
    for cols in (20, 40, 60, 80):
        out = ui.fit(line, cols)
        assert ui.visible_len(out) <= cols
    assert ui.fit("corto", 80) == "corto"


def test_drain_waits_without_killing_the_worker():
    done = []

    def slow(a, model, language):
        time.sleep(0.2)
        done.append(1)
        return "tramo"

    s = LiveSession(slow, [], lambda t, r: t, "m", "es", echo=False)
    s.submit(np.zeros(10, dtype="float32"))
    s.drain()
    assert s.committed == "tramo" and s.busy()          # worker still alive → continue works
    s.release()
    s.submit(np.zeros(10, dtype="float32"))
    s.drain()
    assert s.committed == "tramo tramo"
    s.finish()


def test_config_copy_modes(tmp_path):
    import config

    assert config.load(tmp_path / "nope.toml")["copy"] == "auto"
    p = tmp_path / "c.toml"
    p.write_text('[portapapeles]\ncopiar = "nunca"\n')
    assert config.load(p)["copy"] == "nunca"
    p.write_text('[portapapeles]\ncopiar = "raro"\n')
    assert config.load(p)["copy"] == "auto"


def test_colors_work_on_light_and_dark_backgrounds(monkeypatch):
    monkeypatch.setattr(ui, "_COLOR", True)
    # main text: the terminal's default foreground, never a fixed white
    assert ui.paint(ui.TEXT, "x") == "\x1b[39mx\x1b[0m"
    assert ui.paint(ui.GRAY, "x") == "\x1b[2mx\x1b[0m"          # secondary text: dim
    lines = ui.wrap_transcript("confirmado", "provisional", 80, 3)
    assert "\x1b[39mconfirmado" in lines[0] and "\x1b[2m provisional" in lines[0]
    assert "38;5;255" not in "".join(lines) and "38;5;244" not in "".join(lines)


@pytest.mark.parametrize("no_color, expect", [("1", False), (None, True)])
def test_no_color_is_respected(monkeypatch, no_color, expect):
    import importlib
    import sys

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stderr", Tty())
    if no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", no_color)
    try:
        fresh = importlib.reload(ui)
        assert fresh._COLOR is expect
        assert (fresh.paint(fresh.GRAY, "x") != "x") is expect
    finally:
        monkeypatch.undo()
        importlib.reload(ui)
