"""--lang auto in live sessions: the language is sticky across chunks."""
import sys
import types

import numpy as np
import pytest

import stt
from live import LiveSession
from stt import LangState


def p(es, en):
    return {"es": es, "en": en}


def run_chunks(monkeypatch, detections):
    """Feed one chunk per detection through LiveSession + real transcribe_segments,
    with a mocked detector and a fake mlx_whisper. Returns the language used per chunk."""
    probs = iter(detections)
    used = []
    monkeypatch.setattr(stt, "detect_language_probs", lambda audio, model: next(probs))

    def fake_transcribe(audio, **opts):
        used.append(opts["language"])
        return {"text": "hola", "segments": [
            {"start": 0.0, "end": 1.0, "text": "hola", "no_speech_prob": 0.0, "avg_logprob": -0.2}]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake_transcribe))
    s = LiveSession(stt.transcribe, [], lambda t, r: t, "m", "auto", warm=False, echo=False)
    for _ in detections:
        s.submit(np.full(16000, 0.1, dtype="float32"))
    s.finish()
    return used


def test_weak_english_detection_keeps_spanish(monkeypatch):
    assert run_chunks(monkeypatch, [p(0.9, 0.05), p(0.3, 0.6)]) == ["es", "es"]


def test_strong_english_switches(monkeypatch):
    assert run_chunks(monkeypatch, [p(0.9, 0.05), p(0.02, 0.95)]) == ["es", "en"]


def test_weak_first_chunk_seeds_the_primary_language(monkeypatch):
    assert run_chunks(monkeypatch, [p(0.1, 0.7)]) == ["es"]


def test_clear_first_chunk_in_the_other_language_is_followed(monkeypatch):
    assert run_chunks(monkeypatch, [p(0.03, 0.9)]) == ["en"]


def test_real_bilingual_session_still_switches_both_ways(monkeypatch):
    seq = [p(0.9, 0.05), p(0.03, 0.92), p(0.9, 0.04)]
    assert run_chunks(monkeypatch, seq) == ["es", "en", "es"]


def test_clear_margin_switches_even_below_min():
    st = LangState()
    st.current = "es"
    assert st.choose(p(0.1, 0.7)) == "en"      # 0.7 < 0.80 but beats es by 0.6
    assert st.choose(p(0.3, 0.7)) == "es"      # margin 0.4: not enough


def test_a_weak_other_language_never_seeds_the_session():
    st = LangState()
    assert st.choose(p(0.1, 0.7)) == "es"       # first chunk below SWITCH_MIN: primary
    assert st.choose(p(0.0, 0.0)) == "es"       # no evidence at all
    assert st.choose(p(0.5, 0.5)) == "es"       # low tie
    assert st.choose(p(0.02, 0.9)) == "en"      # clear: follow it


def test_first_chunk_boundary_is_inclusive_with_float_tolerance():
    st = LangState()
    assert st.choose(p(0.1, 0.80)) == "en"      # exactly SWITCH_MIN
    assert st.choose(p(0.1, 0.7999)) == "es"
    assert st.choose(p(0.1, 0.8 - 1e-12)) == "en"   # float noise


def test_leaving_the_primary_needs_strong_evidence_at_the_exact_frontier():
    st = LangState()
    st.current = "es"
    assert st.choose(p(0.1, 0.80)) == "en"      # prob == 0.80
    assert st.choose(p(0.2, 0.7)) == "en"       # margin 0.7 - 0.2 = 0.4999999999999999 (float) -> 0.50
    assert st.choose(p(0.21, 0.7)) == "es"      # margin 0.49
    assert st.choose(p(0.3, 0.3)) == "es"       # low tie


def test_returning_to_the_primary_costs_less_than_leaving_it():
    st = LangState()
    st.current = "en"
    assert st.choose(p(0.60, 0.1)) == "es"      # prob >= 0.60
    assert st.choose(p(0.45, 0.2)) == "es"      # margin 0.25
    assert st.choose(p(0.44, 0.2)) == "en"      # margin 0.24 and prob < 0.60
    assert st.choose(p(0.3, 0.3)) == "en"       # low tie keeps the current language
    assert st.choose(p(0.1, 0.5)) == "en"


def test_spurious_english_recovers_in_one_chunk(monkeypatch):
    seq = [p(0.9, 0.05), p(0.02, 0.85), p(0.65, 0.1)]
    assert run_chunks(monkeypatch, seq) == ["es", "en", "es"]


def test_primary_language_can_be_english():
    st = LangState(primary="en")
    assert st.choose(p(0.7, 0.1)) == "en"       # weak Spanish first chunk: primary is en
    assert st.choose(p(0.85, 0.1)) == "es"
    st.current = "es"
    assert st.choose(p(0.2, 0.62)) == "en"      # back to en: cheap
    assert LangState(primary="fr").primary == "es"   # not allowed: first of `allowed`


def test_default_primary_comes_from_the_resolver(monkeypatch):
    monkeypatch.setattr(stt, "default_primary", lambda: "en")
    assert LangState().primary == "en"


def test_failed_transcription_does_not_move_the_language(monkeypatch):
    st = LangState()
    st.current = "es"
    monkeypatch.setattr(stt, "detect_language_probs", lambda audio, model: p(0.0, 0.95))

    def boom(audio, **o):
        raise RuntimeError("mlx exploto")

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=boom))
    with pytest.raises(RuntimeError):
        stt.transcribe_segments(np.zeros(16000, dtype="float32"), language="auto", lang_state=st)
    assert st.current == "es"


def test_chunk_filtered_to_nothing_does_not_move_the_language(monkeypatch):
    st = LangState()
    st.current = "es"
    monkeypatch.setattr(stt, "detect_language_probs", lambda audio, model: p(0.0, 0.95))
    junk = {"text": "Thank you.", "segments": [
        {"start": 0.0, "end": 1.0, "text": "Thank you.", "no_speech_prob": 0.9, "avg_logprob": -1.0}]}
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=lambda a, **o: junk))
    assert stt.transcribe_segments(np.zeros(16000, dtype="float32"), language="auto", lang_state=st) == []
    assert st.current == "es"


def test_silent_chunk_does_not_set_language(monkeypatch):
    st = LangState()
    monkeypatch.setattr(stt, "detect_language_probs", lambda audio, model: p(0.0, 0.9))
    fake = lambda audio, **o: {"text": "", "segments": []}
    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake))
    stt.transcribe_segments(np.zeros(16000, dtype="float32"), language="auto", lang_state=st)
    assert st.current is None


def test_partials_reuse_the_sticky_language_without_detection():
    calls = []

    def fake(audio, model, language, **kw):
        calls.append(language)
        return "hola"

    s = LiveSession(fake, [], lambda t, r: t, "m", "auto", warm=False, echo=False,
                    partial_source=lambda: np.zeros(16000 * 2, dtype="float32"))
    s._lang_state.current = "en"
    s.closing = False
    s._guess()
    assert calls == ["en"]
    s.finish()


def test_partials_before_the_first_chunk_use_the_primary_and_leave_the_state_alone():
    calls = []
    detected = []

    def fake(audio, model, language, **kw):
        calls.append(language)
        return "hola"

    s = LiveSession(fake, [], lambda t, r: t, "m", "auto", warm=False, echo=False,
                    partial_source=lambda: np.zeros(16000 * 2, dtype="float32"),
                    partial_lang=lambda: detected.append(1) or "en")
    s.closing = False
    assert s._lang_state.current is None
    s._guess()
    assert calls == ["es"] and detected == []     # no raw detection, no invented English
    assert s._lang_state.current is None          # only committed chunks set it
    s.finish()
