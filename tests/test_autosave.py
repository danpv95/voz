import os
import io
import time
import wave

import numpy as np
import pytest

import autosave


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    return tmp_path


def _dur(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def test_clean_session_keeps_audio_and_text(state_dir):
    s = autosave.Session(base=state_dir / "sesion-20261007-100000")
    for _ in range(50):                                  # 50 × 1024 samples ≈ 3.2 s
        s.write_audio(np.full(1024, 0.1, dtype="float32"))
    s.commit("primer tramo")
    s.commit("segundo tramo")
    s.close()
    assert abs(_dur(s.wav_path) - 50 * 1024 / 16000) < 0.01
    assert s.txt_path.read_text().splitlines() == ["primer tramo", "segundo tramo"]
    assert not s.lock_path.exists()
    assert autosave.unfinished() == []


def test_crashed_session_is_readable_and_reported(state_dir):
    s = autosave.Session(base=state_dir / "sesion-20261007-110000")
    for _ in range(30):
        s.write_audio(np.full(1024, 0.1, dtype="float32"))
    s.commit("lo que alcanzó a decirse")
    time.sleep(1.3)
    s.write_audio(np.zeros(1024, dtype="float32"))     # triggers the periodic header sync
    time.sleep(0.3)
    os.close(s._lock_fd)                               # the recorder "died": the OS drops its flock
    assert _dur(s.wav_path) > 1.5                       # valid WAV even without close()
    assert [p.name for p in autosave.unfinished()] == ["sesion-20261007-110000"]
    out = io.StringIO()
    autosave.report_unfinished(out)
    msg = out.getvalue()
    assert "no terminó bien" in msg and str(s.wav_path) in msg and "voz" in msg
    assert autosave.unfinished() == []                  # reported only once


def test_prune_keeps_last_five(state_dir):
    for i in range(8):
        stem = state_dir / f"sesion-20261007-1{i}0000"
        stem.with_suffix(".wav").write_bytes(b"x")
        stem.with_suffix(".txt").write_text("t")
    autosave.prune(keep=5)
    left = sorted(p.name for p in state_dir.glob("*.wav"))
    assert len(left) == 5 and left[0] == "sesion-20261007-130000.wav"


def test_prune_groups_by_run_and_spares_the_current_one(state_dir):
    # 6 older runs (1 recording each) + the current run with 7 recordings
    for i in range(6):
        stem = state_dir / f"sesion-20261007-0{i}0000-g1"
        stem.with_suffix(".wav").write_bytes(b"x")
    cur = "sesion-20261008-100000"
    for g in range(1, 8):
        (state_dir / f"{cur}-g{g}.wav").write_bytes(b"x")
    autosave.prune(keep=5, current_run=cur)
    left = {p.name for p in state_dir.glob("*.wav")}
    assert all(f"{cur}-g{g}.wav" in left for g in range(1, 8))   # current run untouched
    assert "sesion-20261007-000000-g1.wav" not in left          # oldest runs pruned
    assert "sesion-20261007-050000-g1.wav" in left


def test_takes_share_the_run_prefix(state_dir):
    s1 = autosave.Session(run_id="sesion-20261008-110000", take=1)
    s2 = autosave.Session(run_id="sesion-20261008-110000", take=2)
    s1.close(); s2.mark("[descartada]"); s2.close()
    assert s2.wav_path.name == "sesion-20261008-110000-g2.wav"
    assert s2.txt_path.read_text().strip() == "[descartada]"


def _mode(path):
    return os.stat(path).st_mode & 0o777


def test_recordings_are_private_whatever_the_umask(tmp_path, monkeypatch):
    state = tmp_path / "state"                      # does not exist yet: voz creates it
    monkeypatch.setattr(autosave, "STATE_DIR", state)
    old = os.umask(0o000)                           # most permissive umask
    try:
        s = autosave.Session(run_id="sesion-20261008-120000", take=1)
        s.write_audio(np.full(1024, 0.1, dtype="float32"))
        s.commit("texto privado")
        s.mark("[descartada]")
        assert _mode(state) == 0o700
        for p in (s.wav_path, s.txt_path, s.lock_path):
            assert _mode(p) == 0o600, p
        s.close(clean=False)                        # keep the .lock to inspect it
        assert _mode(s.lock_path) == 0o600
    finally:
        os.umask(old)


def test_existing_state_dir_is_tightened(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    os.chmod(state, 0o755)
    monkeypatch.setattr(autosave, "STATE_DIR", state)
    autosave.Session(run_id="sesion-20261008-120001", take=1).close()
    assert _mode(state) == 0o700


def test_two_runs_with_the_same_id_do_not_overwrite_each_other(state_dir):
    a = autosave.Session(run_id="sesion-20261008-130000", take=1)
    a.write_audio(np.full(1024, 0.1, dtype="float32"))
    a.commit("de la primera")
    b = autosave.Session(run_id="sesion-20261008-130000", take=1)    # same second, same id
    assert b.base != a.base and b.run_id == "sesion-20261008-130000-2"
    assert b.wav_path.name == "sesion-20261008-130000-2-g1.wav"
    b.commit("de la segunda")
    a.close(); b.close()
    assert a.txt_path.read_text().splitlines() == ["de la primera"]
    assert b.txt_path.read_text().splitlines() == ["de la segunda"]
    assert _dur(a.wav_path) > 0                      # the first wav was not truncated


def test_leftover_wav_is_never_truncated(state_dir):
    foreign = state_dir / "sesion-20261008-140000-g1"
    foreign.with_suffix(".wav").write_bytes(b"audio ajeno")     # clean session already closed
    s = autosave.Session(base=foreign)
    s.close()
    assert foreign.with_suffix(".wav").read_bytes() == b"audio ajeno"
    assert s.base.name == "sesion-20261008-140000-g1-2"


def test_failed_flock_does_not_write_over_someone_elses_file(state_dir, monkeypatch):
    real = autosave.fcntl.flock
    calls = {"n": 0}

    def flaky(fd, op):
        calls["n"] += 1
        if calls["n"] <= 5:                          # the whole retry budget of candidate 1
            raise BlockingIOError()
        return real(fd, op)

    monkeypatch.setattr(autosave.fcntl, "flock", flaky)
    base = state_dir / "sesion-20261008-150000-g1"
    s = autosave.Session(base=base)
    assert s.base.name == "sesion-20261008-150000-g1-2"
    assert not base.with_suffix(".lock").exists()    # the reservation we gave up is cleaned
    assert not base.with_suffix(".wav").exists()
    s.close()


# ---------- text and audio failures are independent ----------
def test_text_failure_does_not_stop_the_audio(state_dir, monkeypatch):
    s = autosave.Session(run_id="sesion-20261029-100000", take=1)
    s.write_audio(np.full(1024, 0.1, dtype="float32"))

    class Broken:
        closed = False

        def write(self, _):
            raise OSError(28, "No space left on device")

        def flush(self):
            pass

        def close(self):
            pass

    s._txt = Broken()
    s.commit("no se puede guardar")
    assert isinstance(s.text_failed, OSError) and s.audio_failed is None
    assert s.failed is s.text_failed                       # the UI warns
    for _ in range(10):
        s.write_audio(np.full(1024, 0.1, dtype="float32"))  # still accepted after the text error
    s.close()
    assert abs(_dur(s.wav_path) - 11 * 1024 / 16000) < 0.01
    assert s.lock_path.exists()                            # something failed: recoverable


def test_audio_failure_does_not_stop_the_text(state_dir):
    s = autosave.Session(run_id="sesion-20261029-100001", take=1)

    def boom(_):
        raise OSError(5, "I/O error")

    s._wav.writeframesraw = boom
    s.write_audio(np.zeros(1024, dtype="float32"))
    time.sleep(0.3)
    assert isinstance(s.audio_failed, OSError) and s.text_failed is None
    s.commit("el texto sigue")
    s.close()
    assert s.txt_path.read_text().splitlines() == ["el texto sigue"]


def test_lock_stays_if_the_writer_is_still_alive_at_close(state_dir, monkeypatch):
    import threading

    s = autosave.Session(run_id="sesion-20261029-100002", take=1)
    gate = threading.Event()

    def slow(_):
        gate.wait(10)

    s._wav.writeframesraw = slow
    s.write_audio(np.zeros(1024, dtype="float32"))
    time.sleep(0.1)
    real_join = s._writer.join
    monkeypatch.setattr(s._writer, "join", lambda timeout=None: real_join(0.05))   # don't wait 5 s
    s.close()
    assert s._writer.is_alive()
    assert s.lock_path.exists()                            # session still shows as recoverable
    assert [p.name for p in autosave.unfinished()] == [s.base.name]
    gate.set()
    s._writer.join(2)


def test_lock_is_removed_only_after_a_clean_writer(state_dir):
    s = autosave.Session(run_id="sesion-20261029-100003", take=1)
    s.write_audio(np.zeros(1024, dtype="float32"))
    s.close()
    assert not s.lock_path.exists()
    t = autosave.Session(run_id="sesion-20261029-100004", take=1)
    t.close(clean=False)                                   # asked to keep it (incomplete session)
    assert t.lock_path.exists()


# ---------- permissions that cannot be tightened ----------
def test_chmod_failure_warns_once(state_dir, monkeypatch, capsys):
    monkeypatch.setattr(autosave, "_PERM_WARNED", False)

    def deny(*a, **k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(autosave.os, "fchmod", deny)
    monkeypatch.setattr(autosave.os, "chmod", deny)
    s = autosave.Session(run_id="sesion-20261029-100005", take=1)   # 3 files + the directory
    s.mark("[x]")
    s.close()
    err = capsys.readouterr().err
    assert err.count("No se pudieron restringir los permisos") == 1
    assert "Operation not permitted" in err
