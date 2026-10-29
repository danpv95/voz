"""Regression tests for concurrency: live drain, warm-up, autosave writer and stream stop."""
import time

import numpy as np

import autosave
import record
from live import LiveSession


def test_c1_broken_correction_rule_does_not_hang_drain():
    def apply_boom(text, rules):
        raise RuntimeError("regla rota")

    s = LiveSession(lambda a, model, language: "hola", [], apply_boom, "m", "es", echo=False)
    s.submit(np.zeros(10, dtype="float32"))
    t = time.perf_counter()
    s.drain()
    assert time.perf_counter() - t < 2 and s.busy() and s.failed_chunks == 1
    s.finish()


def test_c5_finish_does_not_wait_for_a_slow_warmup():
    def slow_warmup(a, model, language):
        time.sleep(3)
        return ""

    s = LiveSession(slow_warmup, [], lambda t, r: t, "m", "es", echo=False)
    t = time.perf_counter()
    s.finish()
    assert time.perf_counter() - t < 1.0
    assert any("cargando" in r for r in record.hard_exit_reasons())
    record._HARD_EXIT.clear()


def test_c6_writer_failure_stops_queueing_and_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    s = autosave.Session(run_id="sesion-20261008-120000", take=1)

    def boom(_):
        raise OSError(28, "No space left on device")

    s._wav.writeframesraw = boom
    for _ in range(20):
        s.write_audio(np.zeros(1024, dtype="float32"))
    time.sleep(0.3)
    assert isinstance(s.failed, OSError)
    assert s._q.qsize() == 0                       # nothing piles up after the failure
    s.close()


def test_s2_stop_stream_is_never_run_twice_concurrently():
    class Slow:
        stops = 0

        def stop(self):
            Slow.stops += 1
            time.sleep(0.5)

        def close(self):
            pass

    st = Slow()
    record._STREAM_STUCK.clear()
    import threading
    t = threading.Thread(target=record.stop_stream, args=(st, 1.0)); t.start()
    time.sleep(0.05)
    assert record.stop_stream(st, 1.0) is False    # second caller doesn't touch the stream
    t.join()
    assert Slow.stops == 1


def test_s4_lock_vanishing_mid_scan_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    lock = tmp_path / "sesion-20261008-130000-g1.lock"
    lock.write_text("123")
    real = autosave.os.open

    def vanish(path, *a, **k):                       # another voz removed it after the glob
        if str(path).endswith(".lock"):
            raise FileNotFoundError(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(autosave.os, "open", vanish)
    assert autosave.unfinished() == []
    autosave.prune()                                 # must not raise
