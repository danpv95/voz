"""Regressions for the main failure modes: model load, chunks, files and exit codes."""
import sys
import types

import numpy as np
import pytest

import live
import stt
from live import LiveSession


def _boom(*a, **k):
    raise RuntimeError("model exploded")


# 1 - warm-up failure must not hang
def test_warmup_failure_releases_ready_and_reports():
    s = LiveSession(_boom, [], lambda t, r: t, "m", "es")
    assert s.ready.wait(timeout=3), "ready never set → record_live would hang forever"
    assert s.error is not None
    assert live.record_live(s, wait_start=False) is None  # returns before opening the mic
    s.finish()


# 3 - a failing chunk must not swallow the rest
def test_midstream_failure_keeps_other_chunks(capsys):
    calls = iter([None, "uno", RuntimeError("x"), "tres"])  # 1st = warm-up

    def flaky(audio, model, language):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v or ""

    s = LiveSession(flaky, [], lambda t, r: t, "m", "es")
    for _ in range(3):
        s.submit(np.zeros(10, dtype="float32"))
    assert s.finish() == "uno tres"
    assert s.failed_chunks == 1
    assert "no se pudo transcribir" in capsys.readouterr().err.lower()


# 2 - prompts must never go to stdout (pipes / clipboard stay clean)
def test_prompts_go_to_stderr(monkeypatch, capsys):
    import record

    monkeypatch.setattr("builtins.input", lambda *a: "")
    record.prompt("▶️  Enter para EMPEZAR ")
    out = capsys.readouterr()
    assert out.out == "" and "EMPEZAR" in out.err


# 4 - only a missing-model error may trigger the download retry
def _fake_mlx(monkeypatch, behaviour):
    calls = {"n": 0}

    def transcribe(audio, **opts):
        calls["n"] += 1
        return behaviour(calls["n"])

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=transcribe))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    return calls


def test_corrupt_audio_is_not_retried(monkeypatch, capsys):
    def bad(n):
        raise RuntimeError("Failed to load audio")

    calls = _fake_mlx(monkeypatch, bad)
    with pytest.raises(RuntimeError):
        stt.transcribe("corrupt.m4a")
    assert calls["n"] == 1
    assert "descargando" not in capsys.readouterr().err


def test_missing_model_is_retried_online(monkeypatch, capsys):
    from huggingface_hub.errors import LocalEntryNotFoundError

    def first_missing(n):
        if n == 1:
            raise LocalEntryNotFoundError("not cached")
        return {"segments": [{"text": "hola", "start": 0.0, "end": 1.0,
                              "no_speech_prob": 0.0, "avg_logprob": -0.1}]}

    calls = _fake_mlx(monkeypatch, first_missing)
    assert stt.transcribe("x.wav") == "hola"
    assert calls["n"] == 2 and "descargando" in capsys.readouterr().err


# 5 - a real spoken "gracias" survives; silence hallucination does not
def _seg(text):
    return {"text": text, "no_speech_prob": 0.0, "avg_logprob": -0.2}


def test_spoken_gracias_survives_when_audio_has_voice():
    loud = (0.1 * np.sin(np.arange(16000) / 5)).astype("float32")
    assert stt._clean_segments({"segments": [_seg("Gracias.")]}, audio=loud) == "Gracias."


def test_gracias_on_silence_is_dropped():
    quiet = np.full(16000, 0.0005, dtype="float32")
    assert stt._clean_segments({"segments": [_seg("Gracias.")]}, audio=quiet) == ""
