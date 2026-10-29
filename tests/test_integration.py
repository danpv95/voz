"""Real model E2E (macOS `say` + cached mlx model). Run: pytest -m integration"""
import shutil
import subprocess

import pytest

from stt import DEFAULT_MODEL, transcribe_segments

pytestmark = pytest.mark.integration


def _cached(repo):
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo, "config.json"), str)
    except Exception:
        return False


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say")
@pytest.mark.skipif(not _cached(DEFAULT_MODEL), reason="model not cached")
def test_say_roundtrip(tmp_path):
    audio = tmp_path / "prueba.aiff"
    subprocess.run(["say", "-v", "Paulina", "-o", str(audio), "Hola, esto es una prueba de pedidos"], check=True)
    segs = transcribe_segments(str(audio), dictation=False)
    text = " ".join(s["text"] for s in segs).lower()
    assert "prueba" in text and "pedidos" in text
    assert all(s["start"] is not None for s in segs)


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say")
@pytest.mark.skipif(not _cached(DEFAULT_MODEL), reason="model not cached")
def test_live_pipeline_streams_two_phrases(tmp_path, capsys):
    """Simulated mic: `say` audio fed in 1024-sample blocks through Segmenter + LiveSession."""
    import corrections
    from mlx_whisper.audio import load_audio

    from live import LiveSession, Segmenter
    from stt import transcribe

    wav = tmp_path / "live.aiff"
    phrase = "Necesito dos galones de salsa rosada. [[slnc 1500]] El pago es por cloud code."
    subprocess.run(["say", "-v", "Paulina", "-o", str(wav), phrase], check=True)
    audio = load_audio(str(wav))

    import re
    rules = [(re.compile(r"(?<!\w)cloud ?code(?!\w)", re.I), "Claude Code")]  # Whisper writes both
    session = LiveSession(transcribe, rules, corrections.apply, DEFAULT_MODEL, "es")
    session.ready.wait()
    seg = Segmenter()
    chunks = 0
    for i in range(0, len(audio), 1024):
        for c in seg.feed(audio[i:i + 1024]):
            session.submit(c)
            chunks += 1
    last = seg.flush()
    if last is not None:
        session.submit(last)
        chunks += 1
    text = session.finish().lower()
    assert chunks >= 2
    assert "salsa rosada" in text and "claude code" in text


@pytest.mark.skipif(not shutil.which("say"), reason="needs macOS say")
@pytest.mark.skipif(not _cached(DEFAULT_MODEL), reason="model not cached")
def test_file_mixing_spanish_and_english_keeps_each_language(tmp_path):
    """Files with --lang auto detect language per phrase (Whisper alone detects once)."""
    import stt

    es1, en, es2 = tmp_path / "es1.aiff", tmp_path / "en.aiff", tmp_path / "es2.aiff"
    subprocess.run(["say", "-v", "Paulina", "-o", str(es1), "Para mañana necesito tres kilos de queso mozzarella."], check=True)
    subprocess.run(["say", "-v", "Daniel", "-o", str(en), "Please check the invoice module before Friday."], check=True)
    subprocess.run(["say", "-v", "Paulina", "-o", str(es2), "Gracias, confirmo el pedido por la tarde."], check=True)
    mix = tmp_path / "mix.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(es1), "-f", "lavfi", "-t", "1.2",
                    "-i", "anullsrc=r=22050:cl=mono", "-i", str(en), "-f", "lavfi", "-t", "1.2",
                    "-i", "anullsrc=r=22050:cl=mono", "-i", str(es2), "-filter_complex",
                    "[0:a]aresample=22050[a0];[2:a]aresample=22050[a2];[4:a]aresample=22050[a4];"
                    "[a0][1:a][a2][3:a][a4]concat=n=5:v=0:a=1", "-ar", "16000", "-ac", "1", str(mix)], check=True)
    segs = stt.transcribe_file_segments(str(mix))
    text = " ".join(s["text"] for s in segs).lower()
    assert "invoice" in text and "queso" in text and "pedido" in text
    assert {"es", "en"} <= {s["lang"] for s in segs}
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(segs, segs[1:]))   # monotonic for .srt
