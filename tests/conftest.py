"""Make the flat modules in src/ importable (voz.py imports them the same way)."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def primary_language_es(monkeypatch):
    """The primary language comes from the user's config/locale: pin it so tests do not
    depend on the machine (the resolution itself is tested in test_config.py)."""
    import stt

    monkeypatch.setattr(stt, "default_primary", lambda: "es")


@pytest.fixture
def shell(monkeypatch, tmp_path):
    """Factory of liveui.Shell with a fake clipboard, a fake recorder and a private state dir."""
    import liveui
    import output

    clip = {"v": "lo que tenía antes"}
    monkeypatch.setattr(output, "to_clipboard", lambda t: clip.__setitem__("v", t) or True)
    monkeypatch.setattr(output, "from_clipboard", lambda: clip["v"])
    monkeypatch.setattr(liveui.config, "load", lambda: {"copy": "auto"})
    monkeypatch.setattr(liveui.autosave, "STATE_DIR", tmp_path)

    def make(**kw):
        args = types.SimpleNamespace(model="m", lang="es", no_copy=False, sim=None, **kw)
        sh = liveui.Shell(args, [], lambda a, **k: "hola", lambda t, r: t, warm=False, silence_rms=1e-4)
        sh.audio.rec = types.SimpleNamespace(mark=lambda n: None, close=lambda clean=True: None)
        return sh, clip

    yield make


@pytest.fixture(autouse=True)
def fresh_download_state():
    """The model-download progress is process-wide: every test starts without one."""
    import stt

    stt.download.reset()
    stt.download.quiet = False
    yield
    stt.download.reset()
    stt.download.quiet = False
