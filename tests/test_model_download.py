"""First-run model download: status check, progress reporting and how the UI shows it."""
import os
import types

import pytest

import liveui
import stt
import ui


def _plain(lines):
    return "\n".join(ui._ANSI.sub("", ln) for ln in lines)


# ---------- model_status ----------
def _cache(tmp_path, repo="org/modelo", files=()):
    """A Hugging Face cache folder holding `files` for `repo` (refs/main + one snapshot)."""
    root = tmp_path / ("models--" + repo.replace("/", "--"))
    snap = root / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (root / "refs").mkdir()
    (root / "refs" / "main").write_text("abc123")
    for name in files:
        (snap / name).write_text("x")
    return tmp_path


def test_model_status_absent_incomplete_and_ready(tmp_path):
    assert stt.model_status("org/modelo", cache_dir=tmp_path) == "missing"
    _cache(tmp_path, files=["config.json"])
    assert stt.model_status("org/modelo", cache_dir=tmp_path) == "incomplete"      # no weights yet
    (tmp_path / "models--org--modelo" / "snapshots" / "abc123" / "weights.safetensors").write_text("x")
    assert stt.model_status("org/modelo", cache_dir=tmp_path) == "ready"


def test_model_status_accepts_either_weights_file(tmp_path):
    _cache(tmp_path, files=["config.json", "weights.npz"])
    assert stt.model_status("org/modelo", cache_dir=tmp_path) == "ready"


def test_model_status_weights_alone_are_not_enough(tmp_path):
    _cache(tmp_path, files=["weights.safetensors"])
    assert stt.model_status("org/modelo", cache_dir=tmp_path) == "incomplete"


def test_model_status_of_a_local_folder_and_of_nonsense(tmp_path):
    folder = tmp_path / "mi-modelo"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    assert stt.model_status(str(folder)) == "incomplete"
    (folder / "weights.npz").write_text("x")
    assert stt.model_status(str(folder)) == "ready"
    assert stt.model_status("/no/existe/de/verdad", cache_dir=tmp_path) == "missing"


@pytest.mark.parametrize("n, text", [(420e6, "420 MB"), (1.614e9, "1,6 GB"), (3e9, "3 GB"), (999.6e6, "1 GB")])
def test_format_size_uses_a_spanish_comma(n, text):
    assert stt.format_size(n) == text


# ---------- download_model ----------
def _fake_snapshot(monkeypatch, steps, seen=None, fail=None):
    """huggingface_hub.snapshot_download as 1.x drives it: one bytes bar (unit B) fed by
    the downloads, plus a file-count bar that must not be counted as bytes."""
    import huggingface_hub

    def snapshot_download(repo_id, tqdm_class=None, **kw):
        files = tqdm_class(iter(["a", "b"]), unit="it", total=2)
        list(files)                                            # file count: ignored
        bar = tqdm_class(unit="B", total=1000, desc="Downloading")
        for n in steps:
            bar.update(n)
            if seen is not None:
                seen.append((stt.download.active, stt.download.done, os.environ.get("HF_HUB_OFFLINE")))
        if fail:
            raise fail
        return "/cache/snapshot"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot_download)


def test_download_reports_bytes_and_restores_offline_mode(monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    seen = []
    _fake_snapshot(monkeypatch, [100, 320, 580], seen)
    stt.download.quiet = True
    stt.download_model(stt.DEFAULT_MODEL)
    assert seen == [(True, 100, "0"), (True, 420, "0"), (True, 1000, "0")]   # online only while it runs
    assert os.environ["HF_HUB_OFFLINE"] == "1"                                 # local again afterwards
    assert not stt.download.active and stt.download.ended_at > 0
    assert capsys.readouterr().err == ""                                       # quiet: the UI shows it


def test_download_failure_still_restores_offline_mode(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _fake_snapshot(monkeypatch, [10], fail=OSError("sin red"))
    with pytest.raises(OSError):
        stt.download_model(stt.DEFAULT_MODEL)
    assert os.environ["HF_HUB_OFFLINE"] == "1" and not stt.download.active


def test_download_without_ui_prints_one_notice_with_the_size(monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _fake_snapshot(monkeypatch, [1])
    stt.download_model(stt.DEFAULT_MODEL)
    err = capsys.readouterr().err
    assert err.count("descargando") == 1 and "~1,6 GB" in err


def test_unknown_model_takes_its_total_from_the_downloader(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _fake_snapshot(monkeypatch, [5])
    stt.download.quiet = True
    stt.download_model("org/otro")
    assert stt.download.total == 1000 and stt.download.done == 5


# ---------- transcribe_segments triggers it ----------
def _fake_mlx(monkeypatch, errors):
    import sys

    calls = {"n": 0}

    def transcribe(audio, **opts):
        calls["n"] += 1
        if errors and calls["n"] <= len(errors):
            raise errors[calls["n"] - 1]
        return {"text": "hola", "segments": [{"text": "hola", "start": 0.0, "end": 1.0, "no_speech_prob": 0.0}]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=transcribe))
    return calls


def test_missing_model_is_downloaded_then_the_load_is_retried(monkeypatch):
    from huggingface_hub.errors import LocalEntryNotFoundError

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    calls = _fake_mlx(monkeypatch, [LocalEntryNotFoundError("no está")])
    downloaded = []
    monkeypatch.setattr(stt, "download_model", lambda m: downloaded.append(m))
    assert stt.transcribe_segments(b"x", model="org/modelo", language="es")[0]["text"] == "hola"
    assert downloaded == ["org/modelo"] and calls["n"] == 2


def test_incomplete_model_resumes_the_download(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _fake_mlx(monkeypatch, [FileNotFoundError("weights.npz")])
    monkeypatch.setattr(stt, "model_status", lambda m: "incomplete")
    downloaded = []
    monkeypatch.setattr(stt, "download_model", lambda m: downloaded.append(m))
    stt.transcribe_segments(b"x", model="org/modelo", language="es")
    assert downloaded == ["org/modelo"]


def test_other_errors_are_not_mistaken_for_a_missing_model(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    _fake_mlx(monkeypatch, [RuntimeError("MLX roto")])
    monkeypatch.setattr(stt, "model_status", lambda m: "ready")
    monkeypatch.setattr(stt, "download_model", lambda m: pytest.fail("no debe descargar"))
    with pytest.raises(RuntimeError):
        stt.transcribe_segments(b"x", model="org/modelo", language="es")


# ---------- the UI ----------
def _loading(shell, monkeypatch, cols=100, now=100.0):
    sh, _ = shell()
    sh.next_session = types.SimpleNamespace(ready=types.SimpleNamespace(is_set=lambda: False), error=None)
    monkeypatch.setattr(ui, "width", lambda: cols)
    monkeypatch.setattr(liveui.time, "monotonic", lambda: now)
    return sh


def test_ui_shows_downloaded_bytes_against_the_expected_size(shell, monkeypatch):
    sh = _loading(shell, monkeypatch)
    stt.download.start(stt.DEFAULT_MODEL)
    stt.download.add(420_000_000)
    assert _plain(sh.lines()) == "⠋  Descargando modelo · 420 MB / ~1,6 GB · esc salir"


def test_ui_without_byte_figures_shows_seconds_and_no_percentage(shell, monkeypatch):
    sh = _loading(shell, monkeypatch, now=107.0)
    stt.download.start(stt.DEFAULT_MODEL)
    stt.download.started_at = 100.0
    text = _plain(sh.lines())
    assert "Descargando modelo (~1,6 GB, solo la primera vez)… 7 s" in text and "%" not in text and "/" not in text


def test_ui_unknown_size_shows_only_what_arrived(shell, monkeypatch):
    sh = _loading(shell, monkeypatch)
    stt.download.start("org/otro")
    stt.download.add(2_000_000)
    assert _plain(sh.lines()) == "⠋  Descargando modelo · 2 MB · esc salir"


def test_ui_goes_back_to_loading_after_the_download(shell, monkeypatch):
    sh = _loading(shell, monkeypatch, now=110.0)
    sh.load_t0 = 50.0
    stt.download.start(stt.DEFAULT_MODEL)
    stt.download.finish()
    stt.download.ended_at = 104.0                       # the load began when the download ended
    assert "Cargando modelo… 6 s" in _plain(sh.lines()) and "Descargando" not in _plain(sh.lines())


@pytest.mark.parametrize("cols", [40, 59, 80])
def test_download_line_never_overflows_and_keeps_esc(shell, monkeypatch, cols):
    sh = _loading(shell, monkeypatch, cols=cols)
    stt.download.start(stt.DEFAULT_MODEL)
    stt.download.add(1_234_000_000)
    lines = [ui._ANSI.sub("", ln) for ln in sh.lines()]
    assert all(len(ln) <= cols - 1 for ln in lines) and "esc salir" in "\n".join(lines)
    assert "Descargando modelo" in lines[0]
