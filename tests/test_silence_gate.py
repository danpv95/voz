"""Issue #7: Whisper invents sentences on chunks with no voice. Live chunks whose loudest
frame is under MIN_CHUNK_PEAK are never transcribed, and a chunk that is not accepted
must not move the sticky language."""
import numpy as np
import pytest

import live
from live import LiveSession, Segmenter, chunk_peak
from record import SAMPLE_RATE as SR


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    monkeypatch.setattr(live, "MIN_CHUNK_PEAK", 0.015)   # conftest switches it off for other tests


def tone(s, frame_rms):
    t = np.arange(int(SR * s)) / SR
    return (frame_rms * np.sqrt(2) * np.sin(2 * np.pi * 220 * t)).astype("float32")


def room(s, noise=0.0005, seed=0):
    return (noise * np.random.default_rng(seed).standard_normal(int(SR * s))).astype("float32")


def session(fake, lang="es"):
    return LiveSession(fake, [], lambda t, r: t, "m", lang, warm=False, echo=False)


def run(chunks, text="hola", lang="es"):
    calls = []

    def fake(audio, **kw):
        calls.append(len(audio))
        return text

    s = session(fake, lang)
    for c in chunks:
        s.submit(c)
    s.finish()
    return s, calls


def test_chunk_peak_is_the_loudest_30ms_frame():
    x = np.concatenate([room(1), tone(0.1, 0.2), room(1, seed=1)])
    assert chunk_peak(x) == pytest.approx(0.2, rel=0.05)
    assert chunk_peak(np.zeros(0, dtype="float32")) == 0.0


@pytest.mark.parametrize("chunk", [np.zeros(SR * 2, dtype="float32"), room(2), tone(2, 0.012)])
def test_chunk_without_voice_is_not_transcribed(chunk):
    s, calls = run([chunk])
    assert calls == [] and s.parts == [] and s.committed == ""
    assert s.gated == 1 and s.failed_chunks == 0


def test_quiet_real_speech_is_not_lost():
    # 0.021 is the quietest real speech chunk seen in the audited meeting
    s, calls = run([tone(2, 0.021)])
    assert calls == [SR * 2] and s.parts == ["hola"] and s.gated == 0


def test_quiet_speech_survives_the_whole_segmenter_path():
    audio = np.concatenate([room(1.5), tone(2.0, 0.025), room(1.5, seed=1)])
    seg, chunks = Segmenter(), []
    for i in range(0, len(audio), 1024):
        chunks += seg.feed(audio[i:i + 1024])
    last = seg.flush()
    chunks += [last] if last is not None else []
    assert chunks
    s, calls = run(chunks)
    assert s.parts and s.gated == 0


def test_gated_chunk_keeps_the_sticky_language_untouched():
    seen = []

    def fake(audio, lang_state=None, **kw):
        seen.append(lang_state.current)
        lang_state.current = "en"
        return "I am here"

    s = session(fake, "auto")
    s.submit(room(2))
    s.submit(tone(2, 0.1))
    s.finish()
    assert seen == [None] and s.gated == 1       # the quiet chunk never reached the detector
    assert s._lang_state.current == "en"


def test_chunk_dropped_after_transcribing_does_not_move_the_language():
    def fake(audio, lang_state=None, **kw):
        lang_state.current = "en"                # stt moves it when it kept segments...
        return "Thank you."

    s = LiveSession(fake, [], lambda t, r: "", "m", "auto", warm=False, echo=False)
    s._lang_state.current = "es"
    s.submit(tone(2, 0.1))                       # ...but the text ends up empty
    s.finish()
    assert s.parts == [] and s._lang_state.current == "es"


def test_cross_chunk_loop_does_not_move_the_language():
    langs = iter(["es", "en"])

    def fake(audio, lang_state=None, **kw):
        lang_state.current = next(langs)
        return "I am going to put it in the middle."

    s = session(fake, "auto")
    s.submit(tone(3, 0.1))
    s.submit(tone(3, 0.1))
    s.finish()
    assert len(s.parts) == 1 and s._lang_state.current == "es"


def test_partial_guess_is_skipped_on_silence():
    calls = []
    s = LiveSession(lambda a, **k: calls.append(1) or "x", [], lambda t, r: t, "m", "es",
                    warm=False, echo=False, partial_source=lambda: room(2))
    s.closing = False
    s._guess()
    assert calls == [] and s.partial == ""
    s.finish()


def test_diagnostics_go_to_a_file_without_text(monkeypatch, tmp_path, capfd):
    import autosave

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.setenv(live.DIAG_ENV, "1")
    run([room(2), tone(2, 0.1)], text="secreto")
    log = (tmp_path / "diag.log").read_text()
    assert "gated" in log and "accepted" in log and "secreto" not in log
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_no_diagnostics_by_default(monkeypatch, tmp_path):
    import autosave

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.delenv(live.DIAG_ENV, raising=False)
    run([room(2)])
    assert not (tmp_path / "diag.log").exists()
