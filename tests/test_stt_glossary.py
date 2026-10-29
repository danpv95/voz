"""STT cleanup (hallucination filters, repeats), glossary corrections and data handling."""
import re

import numpy as np

import autosave
import corrections
import output
import record
from stt import _clean_segments, _collapse_repeats


def test_english_silence_hallucination_is_junk():
    seg = {"text": "Thank you.", "no_speech_prob": 0.1, "avg_logprob": -0.3}
    assert _clean_segments({"segments": [seg]}, np.zeros(16000, dtype="float32")) == ""


def test_repeats_compared_without_punctuation_and_word_loops():
    assert _collapse_repeats("Gracias. Gracias! Gracias.") == "Gracias."
    assert _collapse_repeats("hola hola hola hola") == "hola"
    assert _collapse_repeats(" ".join(["Press"] * 13)) == "Press"   # white-noise loop
    assert _collapse_repeats("no, no, no") == "no, no, no"          # real speech
    assert _collapse_repeats("muy muy bien") == "muy muy bien"


def _rules(table):
    return [(re.compile(rf"(?<!\w){re.escape(k)}(?!\w)", re.I), v)
            for k, v in sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True)]


def test_corrections_single_pass():
    rules = _rules({"queso campesino": "Queso Campesino La Vaca", "la vaca": "LaVaca S.A.S."})
    assert corrections.apply("Quiero queso campesino.", rules) == "Quiero Queso Campesino La Vaca."
    assert corrections.apply("y la vaca", rules) == "y LaVaca S.A.S."


def test_corrections_non_table_replacements(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('replacements = "x"\n')
    assert corrections.load(f) == []


def test_split_on_silence_small_max():
    audio = np.random.default_rng(0).normal(0, 0.1, 16000 * 6).astype("float32")
    ranges = record.split_on_silence(audio, max_s=2)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(audio)


def test_adaptive_threshold_empty():
    assert record.adaptive_threshold([]) == record.LIVE_PROFILE[0]


def test_json_keeps_lang():
    out = output.to_json([{"start": 0.0, "end": 1.0, "text": "hi", "lang": "en"}])
    assert '"lang": "en"' in out


def test_lock_held_by_flock_not_by_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    s = autosave.Session(run_id="sesion-20260101-000000", take=1)
    assert autosave.unfinished() == []                 # open → alive
    s.close(clean=False)                               # crashed-like: lock left behind
    stem = tmp_path / "sesion-20260101-000000-g1"
    stem.with_suffix(".lock").write_text("1")          # pid 1 is alive but is not voz
    assert autosave.unfinished() == [stem]


def test_lock_with_garbage_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    (tmp_path / "sesion-20260101-000000-g1.lock").write_bytes(b"\xff\xfe")
    autosave.unfinished()                              # must not raise


def test_same_second_run_ids_do_not_truncate(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.setattr(autosave.time, "strftime", lambda fmt: "sesion-20260101-000000")
    a = autosave.Session()
    a.write_audio(np.ones(16000, dtype="float32") * 0.1)
    a.close()
    b = autosave.Session()
    b.close()
    assert a.wav_path != b.wav_path
    assert a.wav_path.stat().st_size > 1000


def test_prune_ignores_non_standard_names(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    (tmp_path / "sesion-manual.wav").write_bytes(b"x")
    for i in range(7):
        (tmp_path / f"sesion-2026010{i}-000000-g1.wav").write_bytes(b"x")
    autosave.prune(keep=5)
    assert (tmp_path / "sesion-manual.wav").exists()                   # not ours: untouched
    assert len(list(tmp_path.glob("sesion-2026*.wav"))) == 5


def test_literal_tokens_are_never_collapsed():
    # digits and single letters are dictated literally, not Whisper loops
    assert _collapse_repeats("La clave es 1 1 1 1.") == "La clave es 1 1 1 1."
    assert _collapse_repeats("Marca 3 0 0 0 0 0 0.") == "Marca 3 0 0 0 0 0 0."
    assert _collapse_repeats("El PIN es 1 2 1 2 1 2.") == "El PIN es 1 2 1 2 1 2."
    assert _collapse_repeats("Escribe A A A A") == "Escribe A A A A"


def test_word_loop_keeps_final_punctuation():
    assert _collapse_repeats("Dije que no, no, no, no.") == "Dije que no."
    assert _collapse_repeats("hola hola hola hola!") == "hola!"
    assert _collapse_repeats("una caja, una caja, una caja, una caja?") == "una caja?"
