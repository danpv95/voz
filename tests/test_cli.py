import pytest

from stt import ACCURATE_MODEL, DEFAULT_MODEL
from voz import parse_args


def test_defaults_to_turbo_model():
    assert parse_args([]).model == DEFAULT_MODEL


def test_accurate_uses_large_model():
    assert parse_args(["--accurate"]).model == ACCURATE_MODEL


def test_explicit_model_is_kept():
    assert parse_args(["--model", "x/y"]).model == "x/y"


def test_accurate_and_model_are_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--accurate", "--model", "x/y"])


@pytest.mark.parametrize("argv", [["--srt"], ["--json", "--loop"], ["doctor", "--srt"]])
def test_srt_json_only_for_files(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_srt_with_file_is_ok():
    args = parse_args(["audio.m4a", "--srt", "--json"])
    assert args.srt and args.json and args.target == "audio.m4a"


def test_loop_not_with_file():
    with pytest.raises(SystemExit):
        parse_args(["audio.m4a", "--loop"])


def test_yes_and_no_copy_conflict():
    with pytest.raises(SystemExit):
        parse_args(["-y", "--no-copy"])


def test_no_trim_requires_no_live():
    with pytest.raises(SystemExit):
        parse_args(["--no-trim"])
    assert parse_args(["--no-trim", "--no-live"]).no_trim


def test_short_error_picks_ffmpeg_reason():
    from voz import _short_error

    e = RuntimeError("Failed to load audio: ffmpeg version 8\n  built with clang\nError opening input files: Invalid data found")
    assert _short_error(e) == "Error opening input files: Invalid data found"


def test_lang_defaults_to_auto():
    assert parse_args([]).lang == "auto"


def test_pick_language_only_among_allowed():
    from stt import _pick_language

    assert _pick_language({"pt": 0.6, "es": 0.3, "en": 0.1}) == "es"
    assert _pick_language({"en": 0.7, "es": 0.2}) == "en"


def test_version_flag(capsys):
    from voz import __version__

    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"voz {__version__}"


def test_silence_warning_does_not_reset_every_app(capsys):
    from voz import _warn_silence

    _warn_silence()
    err = capsys.readouterr().err
    assert "tccutil reset Microphone com.apple.Terminal" in err
    assert "tccutil reset Microphone\n" not in err
    assert "La terminal" in err


def _deny_write(monkeypatch):
    import output

    def boom(text, path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(output, "append_to_doc", boom)


def test_out_without_permission_is_reported_in_live_ui(monkeypatch, capsys):
    import liveui
    import voz

    _deny_write(monkeypatch)
    monkeypatch.setattr(liveui, "run", lambda *a, **k: (0, "texto dictado"))
    code = voz._live_ui(parse_args(["--out", "sin/permiso.md"]), [])
    err = capsys.readouterr().err
    assert code == 1
    assert "No se pudo escribir en sin/permiso.md" in err and "Permission denied" in err
    assert "no se perdió" in err and "portapapeles" in err


def test_out_without_permission_is_reported_in_simple_flow(monkeypatch, capsys):
    import live
    import output
    import voz

    _deny_write(monkeypatch)
    session = type("S", (), {"error": None, "finish": lambda self: "texto dictado"})()
    monkeypatch.setattr(live, "record_live", lambda s: 1.0)
    monkeypatch.setattr(live, "decide_copy", lambda a: True)
    monkeypatch.setattr(output, "to_clipboard", lambda t: True)
    code = voz._live_once(parse_args(["--out", "sin/permiso.md"]), [], session)
    err = capsys.readouterr().err
    assert code == 1
    assert "Copiado al portapapeles" in err and "No se pudo escribir en sin/permiso.md" in err


def test_out_ok_keeps_exit_zero(monkeypatch, tmp_path, capsys):
    import liveui
    import voz

    monkeypatch.setattr(liveui, "run", lambda *a, **k: (0, "texto dictado"))
    doc = tmp_path / "notas.md"
    assert voz._live_ui(parse_args(["--out", str(doc)]), []) == 0
    assert doc.read_text(encoding="utf-8") == "texto dictado\n"


def test_leaving_without_recording_does_not_touch_out(monkeypatch, tmp_path):
    import liveui
    import voz

    monkeypatch.setattr(liveui, "run", lambda *a, **k: (0, ""))
    doc = tmp_path / "notas.md"
    assert voz._live_ui(parse_args(["--out", str(doc)]), []) == 0
    assert not doc.exists()


def test_help_is_in_spanish_and_lists_keys_and_paths(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("uso: voz")
    assert "argumentos:" in out and "opciones:" in out
    assert "positional arguments" not in out and "options:" not in out.replace("opciones:", "")
    assert "-h, --help     muestra esta ayuda y sale" in out
    assert "muestra la versión y sale" in out
    for needle in ("↵ hablar", "p reanudar", "c añadir", "⌫ descartar", "esc descartar",
                   "~/.config/voz/config.toml", "~/.config/voz/corrections.toml",
                   "~/.local/state/voz", "VOZ_UI=simple"):
        assert needle in out, needle


def test_yes_help_says_it_only_applies_to_the_simple_flow(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "solo flujo simple" in out and "modo en vivo)" not in out


def test_ctrl_c_keeps_exit_130_and_still_writes_the_text_to_out(monkeypatch, tmp_path):
    import liveui
    import voz

    monkeypatch.setattr(liveui, "run", lambda *a, **k: (130, "texto dictado"))
    doc = tmp_path / "notas.md"
    assert voz._live_ui(parse_args(["--out", str(doc)]), []) == 130
    assert doc.read_text(encoding="utf-8") == "texto dictado\n"


def test_ctrl_c_with_a_failing_out_still_exits_130(monkeypatch):
    import liveui
    import voz

    _deny_write(monkeypatch)
    monkeypatch.setattr(liveui, "run", lambda *a, **k: (130, "texto dictado"))
    assert voz._live_ui(parse_args(["--out", "sin/permiso.md"]), []) == 130
