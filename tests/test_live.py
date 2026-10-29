import numpy as np

import live
from live import LiveSession, Segmenter
from record import SAMPLE_RATE as SR


def tone(s, amp=0.1):
    t = np.arange(int(SR * s)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype("float32")


def quiet(s, noise=0.0005, seed=0):
    return (noise * np.random.default_rng(seed).standard_normal(int(SR * s))).astype("float32")


def feed_in_blocks(seg, audio, block=1024):
    out = []
    for i in range(0, len(audio), block):
        out += seg.feed(audio[i:i + block])
    return out


def test_two_phrases_with_pause_give_two_chunks():
    audio = np.concatenate([quiet(1), tone(1.5), quiet(1.2, seed=1), tone(1.0), quiet(1.2, seed=2)])
    chunks = feed_in_blocks(Segmenter(), audio)
    assert len(chunks) == 2
    assert 1.5 <= len(chunks[0]) / SR <= 2.5
    assert Segmenter().flush() is None


def test_short_pause_does_not_split():
    audio = np.concatenate([quiet(1), tone(1.0), quiet(0.3, seed=1), tone(1.0), quiet(1.2, seed=2)])
    assert len(feed_in_blocks(Segmenter(), audio)) == 1


def test_long_speech_is_force_cut_without_losing_audio():
    seg = Segmenter(max_chunk_s=5.0)
    audio = np.concatenate([quiet(0.5), tone(12.0)])
    chunks = feed_in_blocks(seg, audio)
    rest = seg.flush()
    assert len(chunks) >= 2 and all(len(c) / SR <= 5.05 for c in chunks)
    assert rest is not None
    total = sum(len(c) for c in chunks) + len(rest)
    assert total / SR >= 12.0                     # nothing dropped at the cuts


def test_soft_cut_lands_in_the_quietest_gap():
    """Continuous speech with a short dip (a breath) near the limit: cut there, not mid-word."""
    seg = Segmenter(max_chunk_s=5.0)
    audio = np.concatenate([quiet(0.5), tone(4.2), tone(0.15, amp=0.004), tone(3.0)])
    first = feed_in_blocks(seg, audio)[0]
    # the dip starts at ~4.7 s of audio (0.5 quiet + 4.2 tone); preroll keeps ~0.3 s of the quiet lead
    assert 4.4 <= len(first) / SR <= 4.75


def test_preroll_keeps_soft_onset():
    seg = Segmenter()
    chunks = feed_in_blocks(seg, np.concatenate([quiet(1.0), tone(1.0), quiet(1.2, seed=3)]))
    assert len(chunks[0]) / SR >= 1.0 + 0.2  # ~300 ms preroll before the tone


def test_flush_returns_unfinished_speech():
    seg = Segmenter()
    assert feed_in_blocks(seg, np.concatenate([quiet(1.0), tone(1.0)])) == []
    assert seg.flush() is not None


def test_silence_only_gives_nothing_and_low_peak():
    seg = Segmenter()
    assert feed_in_blocks(seg, np.zeros(SR * 2, dtype="float32")) == []
    assert seg.flush() is None and seg.peak_level == 0.0


def test_session_streams_and_joins(capsys):
    said = iter(["", "hola cloud", "  ", "segunda frase"])  # 1st call = warm-up

    def fake_transcribe(audio, model, language):
        return next(said)

    def fake_apply(text, rules):
        return text.replace("cloud", "Claude")

    s = LiveSession(fake_transcribe, [], fake_apply, "m", "es")
    for _ in range(3):
        s.submit(np.zeros(10, dtype="float32"))
    assert s.finish() == "hola Claude segunda frase"
    assert capsys.readouterr().out == "hola Claude segunda frase\n"


def test_loop_across_chunks_keeps_one_copy():
    """Each copy of a Whisper loop can arrive in its own chunk; only the first stays."""
    loop = "I'm going to put it in the bag."
    said = iter(["", "hacia atrás. " + loop, loop, loop, "I'm going to put it in the bag!", "y ahí terminó"])

    s = LiveSession(lambda audio, model, language: next(said), [], lambda t, r: t, "m", "es")
    for _ in range(5):
        s.submit(np.zeros(10, dtype="float32"))
    assert s.finish() == "hacia atrás. " + loop + " y ahí terminó"


def test_literal_chunks_are_not_treated_as_loops():
    said = iter(["", "la clave es 1 1.", "1 1.", "fin"])

    s = LiveSession(lambda audio, model, language: next(said), [], lambda t, r: t, "m", "es")
    for _ in range(3):
        s.submit(np.zeros(10, dtype="float32"))
    assert s.finish() == "la clave es 1 1. 1 1. fin"


def test_decide_copy(monkeypatch):
    class A:
        no_copy = False
        yes = False

    a = A()
    a.no_copy = True
    assert live.decide_copy(a) is False
    a.no_copy, a.yes = False, True
    assert live.decide_copy(a) is True
    a.yes = False
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert live.decide_copy(a) is False
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert live.decide_copy(a) is True


def test_moderate_background_noise_still_splits_at_pauses():
    """Regression: with ~0.004 RMS fan-like noise the old threshold sat below the
    noise floor and merged everything into one chunk."""
    rng = np.random.default_rng(7)
    noise = lambda s: (0.004 * rng.standard_normal(int(SR * s))).astype("float32")
    speech = lambda s: tone(s, amp=0.1) + noise(s)
    audio = np.concatenate([noise(1.0), speech(1.5), noise(1.2), speech(1.0), noise(1.2)])
    seg = Segmenter()
    chunks = feed_in_blocks(seg, audio)
    rest = seg.flush()
    chunks += [rest] if rest is not None else []
    assert len(chunks) == 2


def test_soft_first_word_is_kept_live():
    audio = np.concatenate([quiet(1.0), tone(0.4, amp=0.01), tone(3.0, amp=0.2), quiet(1.2, seed=4)])
    chunks = feed_in_blocks(Segmenter(), audio)
    assert len(chunks[0]) / SR >= 3.4


def test_recent_voiced_follows_the_segmenter_threshold():
    seg = Segmenter()
    feed_in_blocks(seg, np.concatenate([quiet(1), tone(0.3)]))
    assert len(seg.recent_voiced) == len(seg.recent_levels)      # same frames
    flags = list(seg.recent_voiced)
    assert not flags[0] and flags[-1]                            # silence then voice
    seg2 = Segmenter()
    feed_in_blocks(seg2, quiet(1))
    assert not any(seg2.recent_voiced)


# ---------- meter snapshot (audio thread appends while the UI reads) ----------
def test_meter_snapshot_waits_for_feed_and_returns_plain_lists():
    import threading

    seg = Segmenter()
    feed_in_blocks(seg, quiet(0.3))
    levels, voiced = seg.meter_snapshot()
    assert isinstance(levels, list) and isinstance(voiced, list) and len(levels) == len(voiced) > 0
    done = threading.Event()
    with seg._lock:                                  # the audio thread is inside feed()
        t = threading.Thread(target=lambda: (seg.meter_snapshot(), done.set()))
        t.start()
        assert not done.wait(0.2)                    # the reader waits instead of iterating
    t.join(2)
    assert done.is_set()


def test_meter_snapshot_survives_concurrent_feeding():
    import threading

    seg = Segmenter()
    stop = threading.Event()
    errors = []

    def feeder():
        block = tone(0.1)
        while not stop.is_set():
            seg.feed(block)

    t = threading.Thread(target=feeder)
    t.start()
    try:
        for _ in range(3000):
            try:
                levels, voiced = seg.meter_snapshot()
                assert len(levels) == len(voiced)
            except RuntimeError as e:               # "deque mutated during iteration"
                errors.append(e)
                break
    finally:
        stop.set()
        t.join()
    assert errors == []


# ---------- bounded drain ----------
def _blocked_session():
    import threading

    gate = threading.Event()
    n = {"calls": 0}

    def transcribe(audio, model, language):
        n["calls"] += 1
        if n["calls"] > 1:                           # the 1st call is the warm-up
            gate.wait(30)
        return "texto"

    s = LiveSession(transcribe, [], lambda t, r: t, "m", "es", echo=False)
    s.ready.wait(2)
    s.submit(np.zeros(10, dtype="float32"))
    return s, gate


def test_drain_times_out_on_a_blocked_transcriber_and_finish_does_not_hang():
    import time

    import record

    s, gate = _blocked_session()
    t0 = time.perf_counter()
    assert s.drain(timeout=0.3) is False
    assert 0.25 < time.perf_counter() - t0 < 2 and s.abandoned and s.pending == 1
    t0 = time.perf_counter()
    s.finish()                                       # abandoned: no waiting for the worker
    assert time.perf_counter() - t0 < 0.5
    assert any("pendiente" in r for r in record.hard_exit_reasons())
    record._HARD_EXIT.clear()
    gate.set()


def test_drain_can_be_abandoned_from_outside():
    import threading
    import time

    s, gate = _blocked_session()
    threading.Timer(0.2, s.abandon).start()
    t0 = time.perf_counter()
    assert s.drain(timeout=60) is False
    assert time.perf_counter() - t0 < 2
    gate.set()
    s.finish()


def test_drain_reports_success_when_everything_was_transcribed():
    s = LiveSession(lambda a, model, language: "hola", [], lambda t, r: t, "m", "es", echo=False)
    s.submit(np.zeros(10, dtype="float32"))
    assert s.drain(timeout=5) is True and not s.abandoned
    s.finish()


def test_simple_flow_close_keeps_the_session_unfinished_on_timeout(tmp_path, monkeypatch, capsys):
    import autosave
    import record

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.setattr(live, "FINISH_TIMEOUT_S", 0.3)
    s, gate = _blocked_session()
    rec = autosave.Session(run_id="sesion-20261029-110000", take=1)
    done = live._flush_and_close(Segmenter(), s, rec)
    assert done is False and rec.lock_path.exists()
    err = capsys.readouterr().err
    assert "Cierre incompleto" in err and str(rec.wav_path) in err
    assert [p.name for p in autosave.unfinished()] == [rec.base.name]
    s.finish()
    record._HARD_EXIT.clear()
    gate.set()
