"""`voz doctor`: what is critical, what is only a warning, and how it reports the models."""
import doctor
import stt


def _run(monkeypatch, capsys, *, status=None, ffmpeg="/opt/homebrew/bin/ffmpeg", tmp_path=None):
    """Run doctor with a scripted environment; returns (exit code, output)."""
    status = status or {}
    monkeypatch.setattr(stt, "model_status", lambda repo: status.get(repo, "ready"))
    monkeypatch.setattr(doctor.stt, "model_status", lambda repo: status.get(repo, "ready"))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: ffmpeg if name == "ffmpeg" else "/usr/local/bin/" + name)
    monkeypatch.setattr(doctor.corrections, "DEFAULT_PATH", tmp_path / "corrections.toml")
    import sounddevice

    monkeypatch.setattr(sounddevice, "query_devices", lambda kind=None: {"name": "Micro de prueba"})
    code = doctor.run()
    return code, capsys.readouterr().out


def test_missing_ffmpeg_is_a_warning_not_a_critical_problem(monkeypatch, capsys, tmp_path):
    code, out = _run(monkeypatch, capsys, ffmpeg=None, tmp_path=tmp_path)
    assert code == 0
    assert "⚠ ffmpeg no instalado: el dictado funciona; para transcribir archivos: brew install ffmpeg" in out
    assert "crítico" not in out.replace("Sin problemas críticos", "")
    assert "Sin problemas críticos · " in out and "advertencia" in out


def test_everything_in_order_says_so(monkeypatch, capsys, tmp_path):
    (tmp_path / "corrections.toml").write_text("[replacements]\n", encoding="utf-8")
    code, out = _run(monkeypatch, capsys, tmp_path=tmp_path)
    assert code == 0 and "ffmpeg: /opt/homebrew/bin/ffmpeg" in out
    assert out.strip().endswith(("Todo en orden.", "advertencia(s)."))


def test_a_failing_microphone_query_is_critical(monkeypatch, capsys, tmp_path):
    import sounddevice

    monkeypatch.setattr(stt, "model_status", lambda repo: "ready")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(doctor.corrections, "DEFAULT_PATH", tmp_path / "c.toml")

    def broken(kind=None):
        raise RuntimeError("sin dispositivos")

    monkeypatch.setattr(sounddevice, "query_devices", broken)
    assert doctor.run() == 1
    out = capsys.readouterr().out
    assert "✖ Micrófono por defecto: sin dispositivos" in out and "1 problema(s) crítico(s)." in out


def test_model_absent_incomplete_and_available_are_told_apart(monkeypatch, capsys, tmp_path):
    _, out = _run(monkeypatch, capsys, tmp_path=tmp_path, status={
        stt.DEFAULT_MODEL: "incomplete", stt.ACCURATE_MODEL: "missing"})
    assert "⚠ Modelo por defecto incompleto (faltan los pesos): se reanuda la descarga al usarlo (~1,6 GB)" in out
    assert "· Modelo --accurate no descargado: opcional; el primer uso descarga ~3 GB" in out
    _, out = _run(monkeypatch, capsys, tmp_path=tmp_path, status={stt.DEFAULT_MODEL: "missing"})
    assert "⚠ Modelo por defecto no está en caché: el primer uso descarga ~1,6 GB" in out
    assert f"✓ Modelo --accurate disponible: {stt.ACCURATE_MODEL}" in out


def test_an_incomplete_optional_model_is_still_a_warning(monkeypatch, capsys, tmp_path):
    _, out = _run(monkeypatch, capsys, tmp_path=tmp_path, status={stt.ACCURATE_MODEL: "incomplete"})
    assert "⚠ Modelo --accurate incompleto (faltan los pesos)" in out and "~3 GB" in out


def test_doctor_uses_the_unified_symbols(monkeypatch, capsys, tmp_path):
    _, out = _run(monkeypatch, capsys, ffmpeg=None, tmp_path=tmp_path)
    for old in ("✅", "❌", "✗", "⚠️"):
        assert old not in out
    assert out.startswith("✓ Python")


def test_the_two_model_sizes_doctor_quotes_match_the_published_weights():
    assert stt.format_size(stt.MODEL_SIZES[stt.DEFAULT_MODEL]) == "1,6 GB"     # weights.safetensors 1 613 977 612 B
    assert stt.format_size(stt.MODEL_SIZES[stt.ACCURATE_MODEL]) == "3 GB"      # weights.npz 3 083 520 416 B
