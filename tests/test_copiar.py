import wave

import pytest

import autosave
import output
import voz


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def clip(monkeypatch):
    """Fake clipboard: the real one is never touched."""
    box = {}
    monkeypatch.setattr(output, "to_clipboard", lambda t: box.__setitem__("v", t) or True)
    return box


def rec(state, run, n, *lines, secs=None):
    stem = state / f"sesion-{run}-g{n}"
    stem.with_suffix(".txt").write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    if secs is not None:
        with wave.open(str(stem.with_suffix(".wav")), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * int(16000 * secs))


def run_copiar(capsys, *argv):
    code = voz.main(["copiar", *argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_copies_the_latest_recording(state, clip, capsys):
    rec(state, "20261103-090000", 1, "viejo")
    rec(state, "20261103-120000", 1, "primero", "de la tarde")
    rec(state, "20261103-120000", 2, "el último", "tramo dos")
    code, out, err = run_copiar(capsys)
    assert code == 0 and out == ""
    assert clip["v"] == "el último tramo dos"
    assert "✓" in err and "Copiado" in err and "grabación 2" in err and "4 palabras" in err
    assert "2026-11-03 12:00" in err


def test_n_picks_older_ones_across_sessions(state, clip, capsys):
    rec(state, "20261103-090000", 1, "viejo")
    rec(state, "20261103-120000", 1, "medio")
    rec(state, "20261103-120000", 2, "nuevo")
    assert run_copiar(capsys, "2")[0] == 0 and clip["v"] == "medio"
    assert run_copiar(capsys, "3")[0] == 0 and clip["v"] == "viejo"


def test_n_out_of_range_fails_without_copying(state, clip, capsys):
    rec(state, "20261103-090000", 1, "solo una")
    code, _, err = run_copiar(capsys, "5")
    assert code == 1 and "v" not in clip
    assert "Solo hay 1 grabación" in err and "--lista" in err


def test_todas_joins_the_last_session_only(state, clip, capsys):
    rec(state, "20261103-090000", 1, "otra sesión")
    rec(state, "20261103-120000", 1, "uno")
    rec(state, "20261103-120000", 2, "dos", "y medio")
    run_copiar(capsys, "--todas")
    assert clip["v"] == "uno\n\ndos y medio"


def test_discarded_is_skipped_unless_restored(state, clip, capsys):
    rec(state, "20261103-120000", 1, "buena")
    rec(state, "20261103-120000", 2, "mala", "[descartada]")
    rec(state, "20261103-120000", 3, "rescatada", "[descartada]", "[restaurada]")
    rec(state, "20261103-120000", 4, "otra mala", "[descartada]", "[restaurada]", "[descartada]")
    run_copiar(capsys)
    assert clip["v"] == "rescatada"
    run_copiar(capsys, "2")
    assert clip["v"] == "buena"
    run_copiar(capsys, "--todas")
    assert clip["v"] == "buena\n\nrescatada"


def test_nothing_saved_is_a_clear_error(state, clip, capsys):
    code, out, err = run_copiar(capsys)
    assert code == 1 and out == "" and "No hay grabaciones guardadas" in err
    rec(state, "20261103-120000", 1, "solo descartada", "[descartada]")
    rec(state, "20261103-120000", 2)                       # empty .txt: no voice
    assert run_copiar(capsys)[0] == 1 and run_copiar(capsys, "--lista")[0] == 1
    assert "v" not in clip


def test_missing_state_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path / "nope")
    assert run_copiar(capsys)[0] == 1


def test_stdout_prints_and_does_not_copy(state, clip, capsys):
    rec(state, "20261103-120000", 1, "para la tubería")
    code, out, err = run_copiar(capsys, "--stdout")
    assert code == 0 and out == "para la tubería\n"
    assert "v" not in clip and "Impreso" in err and "3 palabras" in err


def test_g10_sorts_after_g9(state, clip, capsys):
    for n in (1, 2, 9, 10, 11):
        rec(state, "20261103-120000", n, f"toma {n}")
    run_copiar(capsys)
    assert clip["v"] == "toma 11"
    run_copiar(capsys, "3")
    assert clip["v"] == "toma 9"
    run_copiar(capsys, "--todas")
    assert clip["v"].split("\n\n") == [f"toma {n}" for n in (1, 2, 9, 10, 11)]


def test_legacy_name_without_g_and_foreign_files_are_handled(state, clip, capsys):
    (state / "sesion-20261001-101500.txt").write_text("antigua\n", encoding="utf-8")
    (state / "sesion-manual.txt").write_text("a mano\n", encoding="utf-8")
    run_copiar(capsys)
    assert clip["v"] == "antigua"


def test_lista_shows_numbered_summary(state, clip, capsys):
    rec(state, "20261103-090000", 1, "hola equipo buenos días hoy vamos a revisar el plan", secs=75)
    rec(state, "20261103-120000", 1, "dos palabras", secs=None)
    code, out, err = run_copiar(capsys, "--lista")
    lines = out.splitlines()
    assert code == 0 and err == "" and len(lines) == 2 and "v" not in clip
    assert lines[0].lstrip().startswith("1") and "dos palabras" in lines[0] and "2 palabras" in lines[0]
    assert lines[1].lstrip().startswith("2") and "1:15" in lines[1] and "2026-11-03 09:00" in lines[1]
    assert "«hola equipo buenos días hoy vamos…»" in lines[1] and "10 palabras" in lines[1]


def test_clipboard_failure_is_reported(state, monkeypatch, capsys):
    rec(state, "20261103-120000", 1, "texto")
    monkeypatch.setattr(output, "to_clipboard", lambda t: False)
    code, _, err = run_copiar(capsys)
    assert code == 1 and "No se pudo copiar" in err and "--stdout" in err


@pytest.mark.parametrize("argv", [["0"], ["x"], ["-1"], ["--lista", "2"], ["--lista", "--todas"], ["--todas", "2"]])
def test_bad_combinations_are_rejected(argv):
    with pytest.raises(SystemExit):
        voz.parse_args(["copiar", *argv])


@pytest.mark.parametrize("argv", [["--todas"], ["--lista"], ["--stdout"], ["doctor", "2"], ["nota.m4a", "2"]])
def test_copiar_flags_only_with_copiar(argv):
    with pytest.raises(SystemExit):
        voz.parse_args(argv)


def test_help_mentions_copiar(capsys):
    with pytest.raises(SystemExit):
        voz.parse_args(["--help"])
    assert "voz copiar" in capsys.readouterr().out
