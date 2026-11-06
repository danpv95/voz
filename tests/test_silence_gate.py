"""Issue #7: Whisper invents sentences on chunks with no voice. Live chunks whose loudest
frame does not stand out of the session's noise floor are never transcribed (v0.1.2: the
gate is relative to the noise, it was an absolute peak), and a chunk that is not accepted
must not move the sticky language."""
import numpy as np
import pytest

import live
from live import LiveSession, Segmenter, chunk_peak
from record import SAMPLE_RATE as SR

# read at import, before conftest switches the gate off for every test
GATE = {n: getattr(live, n) for n in ("MIN_CHUNK_PEAK", "MIN_SNR", "MIN_SNR_SHORT")}


@pytest.fixture(autouse=True)
def gate_on(monkeypatch):
    for name, value in GATE.items():
        monkeypatch.setattr(live, name, value)


def tone(s, frame_rms):
    t = np.arange(int(SR * s)) / SR
    return (frame_rms * np.sqrt(2) * np.sin(2 * np.pi * 220 * t)).astype("float32")


def room(s, noise=0.0005, seed=0):
    return (noise * np.random.default_rng(seed).standard_normal(int(SR * s))).astype("float32")


def voiced(s, frame_rms, noise=0.0005):
    """A chunk as the Segmenter cuts it: pause before and after the voice."""
    return np.concatenate([room(0.5, noise), tone(s, frame_rms), room(0.3, noise, seed=1)])


def segment(audio):
    seg, chunks = Segmenter(), []
    for i in range(0, len(audio), 1024):
        chunks += seg.feed(audio[i:i + 1024])
    last = seg.flush()
    return chunks + ([last] if last is not None else [])


def speech(s, frame_rms, seed=0):
    """Syllable-like bursts (~4 per second) of a 220 Hz voice whose loudest frame is ~frame_rms."""
    t = np.arange(int(SR * s)) / SR
    env = np.clip(np.sin(2 * np.pi * 2.0 * t), 0, None) ** 2
    jitter = 1 + 0.1 * np.random.default_rng(seed).standard_normal(len(t))
    return (frame_rms * np.sqrt(2) * env * jitter * np.sin(2 * np.pi * 220 * t)).astype("float32")


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


@pytest.mark.parametrize("chunk", [np.zeros(SR * 2, dtype="float32"), room(2), tone(2, 0.012),
                                   room(3, noise=0.004)])
def test_chunk_without_voice_is_not_transcribed(chunk):
    s, calls = run([chunk])
    assert calls == [] and s.parts == [] and s.committed == ""
    assert s.gated == 1 and s.failed_chunks == 0


def test_quiet_real_speech_is_not_lost():
    # 0.021 is the quietest real speech chunk seen in the audited meeting
    chunk = voiced(2, 0.021)
    s, calls = run([chunk])
    assert calls == [len(chunk)] and s.parts == ["hola"] and s.gated == 0


def test_quiet_speech_survives_the_whole_segmenter_path():
    audio = np.concatenate([room(1.5), tone(2.0, 0.025), room(1.5, seed=1)])
    chunks = segment(audio)
    assert chunks
    s, calls = run(chunks)
    assert s.parts and s.gated == 0


@pytest.mark.parametrize("gain", [1.0, 0.1])
def test_gate_is_relative_to_the_noise_not_to_the_gain(gain):
    # same chunk, quieter mic: what counts is how far the voice stands out of the noise
    s, _ = run([voiced(2, 0.021) * gain])
    assert s.parts == ["hola"] and s.gated == 0


def test_low_voice_over_low_noise_is_transcribed():
    """v0.1.2 regression: a quiet dictation (noise floor ~0.0012, phrases of 1.6-3.5 s whose
    loudest frame is 0.008-0.014) was dropped whole by the old absolute gate (0.015)."""
    noise = 0.0012
    audio = np.concatenate([room(6, noise), speech(1.7, 0.008), room(1.5, noise, seed=1),
                            speech(3.0, 0.011, seed=2), room(1.5, noise, seed=3)])
    chunks = segment(audio)
    assert len(chunks) == 2 and all(c.noise_floor < 2 * noise for c in chunks)
    s, calls = run(chunks)
    assert len(calls) == 2 and s.gated == 0


@pytest.mark.parametrize("bump_s, bump, noise", [
    (0.4, 0.004, 0.0012),    # short noise burst over a quiet room (tramos vacíos of 0.5 s)
    (0.5, 0.008, 0.0012),    # short blip under 8 × the room: where Whisper invented words
    (2.5, 0.004, 0.0012),    # long hum barely over the room
])
def test_room_noise_without_voice_is_gated_through_the_segmenter(bump_s, bump, noise):
    audio = np.concatenate([room(6, noise), tone(bump_s, bump) + room(bump_s, noise, seed=1),
                            room(2, noise, seed=2)])
    chunks = segment(audio)
    assert chunks                                # the Segmenter did cut it...
    s, calls = run(chunks)
    assert calls == [] and s.gated == len(chunks)   # ...and the gate kept it from Whisper


def test_noise_floor_is_causal_and_follows_the_room():
    seg = Segmenter()
    for i in range(0, SR * 3, 1024):
        seg.feed(room(3, 0.002)[i:i + 1024])
    quiet_floor = seg.noise_floor()
    assert 0.0015 < quiet_floor < 0.0025
    # what comes later does not change a floor already measured
    chunks = segment(np.concatenate([room(3, 0.002), tone(2, 0.05), room(1, 0.002, seed=1)]))
    assert 0.0015 < chunks[0].noise_floor < 0.0025
    assert Segmenter().noise_floor() == 0.0


def test_continuous_speech_does_not_raise_the_noise_floor():
    # 20 s of voice after a quiet room: the floor stays at the room, not at the voice
    seg = Segmenter()
    audio = np.concatenate([room(3, 0.002), tone(20, 0.05), room(1, 0.002, seed=1)])
    for i in range(0, len(audio), 1024):
        seg.feed(audio[i:i + 1024])
    assert 0.0015 < seg.noise_floor() < 0.0025


def test_talking_from_the_first_second_still_has_a_floor():
    # no pauses yet: every frame counts, so the floor is not zero (that would accept clicks)
    seg = Segmenter()
    audio = tone(3, 0.05)
    for i in range(0, len(audio), 1024):
        seg.feed(audio[i:i + 1024])
    assert seg.noise_floor() > 0.0


def test_gated_chunk_keeps_the_sticky_language_untouched():
    seen = []

    def fake(audio, lang_state=None, **kw):
        seen.append(lang_state.current)
        lang_state.current = "en"
        return "I am here"

    s = session(fake, "auto")
    s.submit(room(2))
    s.submit(voiced(2, 0.1))
    s.finish()
    assert seen == [None] and s.gated == 1       # the quiet chunk never reached the detector
    assert s._lang_state.current == "en"


def test_chunk_dropped_after_transcribing_does_not_move_the_language():
    def fake(audio, lang_state=None, **kw):
        lang_state.current = "en"                # stt moves it when it kept segments...
        return "Thank you."

    s = LiveSession(fake, [], lambda t, r: "", "m", "auto", warm=False, echo=False)
    s._lang_state.current = "es"
    s.submit(voiced(2, 0.1))                     # ...but the text ends up empty
    s.finish()
    assert s.parts == [] and s._lang_state.current == "es"


def test_cross_chunk_loop_does_not_move_the_language():
    langs = iter(["es", "en"])

    def fake(audio, lang_state=None, **kw):
        lang_state.current = next(langs)
        return "I am going to put it in the middle."

    s = session(fake, "auto")
    s.submit(voiced(3, 0.1))
    s.submit(voiced(3, 0.1))
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
    run([room(2), voiced(2, 0.1)], text="secreto")
    log = (tmp_path / "diag.log").read_text()
    assert "gated" in log and "accepted" in log and "secreto" not in log
    assert log.count("floor=") == 2 and log.count("thr=") == 2
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_no_diagnostics_by_default(monkeypatch, tmp_path):
    import autosave

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.delenv(live.DIAG_ENV, raising=False)
    run([room(2)])
    assert not (tmp_path / "diag.log").exists()
