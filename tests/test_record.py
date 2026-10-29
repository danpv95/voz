import numpy as np

from record import SAMPLE_RATE, trim_silence

SR = SAMPLE_RATE


def tone(seconds, amp=0.1, freq=220):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype("float32")


def silence(seconds, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return (noise * rng.standard_normal(int(SR * seconds))).astype("float32")


def test_trims_edges_but_keeps_padding():
    audio = np.concatenate([silence(2.0), tone(1.0), silence(2.0)])
    out = trim_silence(audio)
    assert 1.0 <= len(out) / SR <= 1.0 + 2 * 0.3 + 0.05


def test_background_noise_does_not_cut_the_voice():
    audio = np.concatenate([silence(1.0, 0.004), tone(1.0), silence(1.0, 0.004, seed=1)])
    out = trim_silence(audio)
    assert len(out) / SR >= 1.0


def test_soft_first_word_survives_when_mostly_speech():
    # quiet word at the start, then loud continuous speech (little silence)
    audio = np.concatenate([silence(0.1), tone(0.4, amp=0.01), tone(3.0, amp=0.2)])
    out = trim_silence(audio)
    assert len(out) / SR >= 3.4


def test_only_silence_returns_empty():
    assert len(trim_silence(silence(2.0))) == 0


def test_very_short_audio_is_untouched():
    audio = tone(0.01)
    assert len(trim_silence(audio)) == len(audio)


def test_split_on_silence_cuts_in_gaps_and_covers_everything():
    from record import split_on_silence

    speech = lambda s: tone(s, amp=0.1)
    audio = np.concatenate([speech(10), silence(1.0), speech(12), silence(1.0), speech(8)])
    r = split_on_silence(audio, max_s=15)
    assert r[0][0] == 0 and r[-1][1] == len(audio)            # full coverage
    assert all(b == c for (_, b), (c, _) in zip(r, r[1:]))    # contiguous, no gaps/overlaps
    assert all((b - a) / SR <= 15.05 for a, b in r)
    first_cut = r[0][1] / SR
    assert 10.0 <= first_cut <= 11.0                           # inside the first silence


def test_split_on_silence_forces_cut_in_continuous_speech():
    from record import split_on_silence

    r = split_on_silence(tone(40, amp=0.1), max_s=12)
    assert len(r) >= 4 and all((b - a) / SR <= 12.05 for a, b in r)
    assert r[-1][1] == int(40 * SR)


class _FakeStream:
    def __init__(self, hang=False):
        self.hang, self.stopped, self.closed = hang, False, False

    def stop(self):
        if self.hang:                 # CoreAudio deadlock: never returns
            import threading
            threading.Event().wait()
        self.stopped = True

    def close(self):
        self.closed = True


def test_stop_stream_normal_path():
    import record

    record._STREAM_STUCK.clear()
    s = _FakeStream()
    assert record.stop_stream(s, timeout=1.0) is True
    assert s.stopped and s.closed and not record.stream_stuck()


def test_stop_stream_never_hangs_the_caller():
    import time
    import record

    record._STREAM_STUCK.clear()
    t = time.perf_counter()
    assert record.stop_stream(_FakeStream(hang=True), timeout=0.3) is False
    assert time.perf_counter() - t < 0.8          # gave up, did not block
    assert record.stream_stuck()                    # voz.py will os._exit at the end
    record._STREAM_STUCK.clear()
