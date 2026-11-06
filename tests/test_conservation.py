"""Never lose or overwrite anything (late results, failed chunks, pending
writer, clipboard restore, discarded recordings stay reachable, session names)."""
import threading
import types
import wave

import numpy as np
import pytest

import autosave
import flow as F
import liveui
import record
import ui
import voz
from live import LiveSession, Segmenter


def plain(lines):
    return "\n".join(ui._ANSI.sub("", ln) for ln in lines)


# ---------- nothing arrives late into a closed take ----------
def test_late_result_after_abandon_never_enters_the_closed_take():
    gate, calls, committed = threading.Event(), [], []

    def transcribe(audio, model, language):
        calls.append(1)
        if len(calls) > 1:                                  # the 1st call is the warm-up
            gate.wait(30)
        return "texto tardío"

    s = LiveSession(transcribe, [], lambda t, r: t, "m", "es", echo=False, on_commit=committed.append)
    s.ready.wait(2)
    s.submit(np.zeros(10, dtype="float32"))
    s.submit(np.zeros(10, dtype="float32"))
    assert s.drain(timeout=0.2) is False and s.abandoned
    gate.set()                                              # the stuck transcriber finally answers
    s.close()
    s._worker.join(3)
    assert not s._worker.is_alive()
    assert s.committed == "" and committed == []            # the take's text did not change
    assert len(calls) == 2                                  # and the 2nd queued chunk was not even transcribed
    record._HARD_EXIT.clear()


def _stuck_take(shell, tmp_path, monkeypatch):
    """A shell whose 1st chunk is transcribed ("uno") and whose 2nd one hangs, closed by timeout."""
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    monkeypatch.setattr(liveui, "FINISH_TIMEOUT_S", 0.3)
    monkeypatch.setattr(liveui, "CLOSE_JOIN_S", 0.1)
    sh, clip = shell()
    gate, calls = threading.Event(), []

    def transcribe(audio, **kw):
        calls.append(1)
        if len(calls) == 3:                                 # warm-up, "uno", then the stuck one
            gate.wait(30)
        return "uno" if len(calls) == 2 else "tardío"

    old = LiveSession(transcribe, [], lambda t, r: t, "m", "es", echo=False, warn=False)
    old.ready.wait(2)
    sh.audio.session, sh.audio.seg = old, Segmenter()
    sh.audio.rec = autosave.Session(run_id="sesion-20261029-100000", take=1)
    old._on_commit = sh.audio.rec.commit
    sh.audio.rec.write_audio(np.full(1600, 0.1, dtype="float32"))
    old.submit(np.zeros(1600, dtype="float32"))
    old.submit(np.zeros(1600, dtype="float32"))
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.FINISHING
    sh.region.draw = lambda lines: None
    monkeypatch.setattr(sh.audio, "open", lambda refresh=False: setattr(sh.audio, "_stop", lambda: None))
    sh._finish(None)
    assert sh.incomplete and old.abandoned and sh.flow.current.text == "uno" and sh.flow.state is F.St.REVIEW
    return sh, old, gate


def test_c_after_abandon_gets_a_new_engine_and_keeps_the_text(shell, tmp_path, monkeypatch):
    sh, old, gate = _stuck_take(shell, tmp_path, monkeypatch)
    sh.perform(F.step(sh.flow, "c"), None)
    new = sh.audio.session
    assert new is not old and new.committed == "uno" and sh.lost == 1 and sh.lost_carry == 1
    gate.set()                                              # the abandoned worker answers late
    old._worker.join(3)
    assert old.committed == "uno" and new.committed == "uno"
    assert sh.audio.rec.txt_path.read_text().splitlines() == ["uno"]
    sh._finish(None)                                        # the lost chunk is still counted after continuing
    assert sh.lost == 1 and sh.incomplete_text
    sh._cleanup()
    assert sh.audio.rec.lock_path.exists()                  # still recoverable
    record._HARD_EXIT.clear()


def test_r_after_abandon_never_receives_the_late_result(shell, tmp_path, monkeypatch):
    sh, old, gate = _stuck_take(shell, tmp_path, monkeypatch)
    first_txt = sh.audio.rec.txt_path
    sh.perform(F.step(sh.flow, "r"), None)
    new = sh.audio.session
    assert new is not old and new.committed == ""
    gate.set()
    old._worker.join(3)
    assert new.committed == "" and old.committed == "uno"
    assert first_txt.read_text().splitlines() == ["uno"]    # nor the previous take's file
    sh._cleanup()
    record._HARD_EXIT.clear()


# ---------- a failed chunk never closes the take as clean ----------
def _failed_take(sh, tmp_path, failed):
    sh.audio.seg = Segmenter()
    sh.audio.seg.peak_level = 0.3
    sh.audio.session = types.SimpleNamespace(committed="algo", partial="", pending=0, abandoned=False,
                                             failed_chunks=failed, drain=lambda timeout=None: True,
                                             submit=lambda c: None)
    sh.audio.rec = autosave.Session(run_id="sesion-20261029-110000", take=1)
    sh.region.draw = lambda lines: None
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.FINISHING
    sh._finish(None)


def test_failed_chunk_leaves_the_session_marked_and_recoverable(shell, tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    sh, _ = shell()
    _failed_take(sh, tmp_path, failed=1)
    assert sh.lost == 1 and sh.incomplete_text and "Texto incompleto" in plain(sh.lines())
    sh._cleanup()
    assert sh.audio.rec.lock_path.exists()
    assert [p.name for p in autosave.unfinished()] == [sh.audio.rec.base.name]


def test_clean_take_still_closes_clean(shell, tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    sh, _ = shell()
    _failed_take(sh, tmp_path, failed=0)
    sh._cleanup()
    assert not sh.audio.rec.lock_path.exists()


def test_simple_flow_failed_chunk_is_not_a_clean_close(tmp_path, monkeypatch, capsys):
    import live

    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    calls = []

    def transcribe(audio, model, language):
        calls.append(1)
        if len(calls) > 1:                                  # warm-up passes, the chunk fails
            raise RuntimeError("modelo roto")
        return ""

    s = LiveSession(transcribe, [], lambda t, r: t, "m", "es", echo=False)
    s.ready.wait(2)
    s.submit(np.zeros(1600, dtype="float32"))
    rec = autosave.Session(run_id="sesion-20261029-120000", take=1)
    done = live._flush_and_close(Segmenter(), s, rec)
    assert s.failed_chunks == 1 and done is False
    assert rec.lock_path.exists()
    err = capsys.readouterr().err
    assert "Cierre incompleto" in err and str(rec.wav_path) in err
    assert [p.name for p in autosave.unfinished()] == [rec.base.name]
    s.finish()


# ---------- autosave.close() with the writer still alive ----------
def test_close_with_a_slow_writer_keeps_the_flock_and_warns_the_next_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    s = autosave.Session(run_id="sesion-20261029-130000", take=1)
    gate = threading.Event()
    s._wav.writeframesraw = lambda _: gate.wait(10)
    s.write_audio(np.zeros(1024, dtype="float32"))
    real_join = s._writer.join
    monkeypatch.setattr(s._writer, "join", lambda timeout=None: real_join(0.05))
    s.close(clean=True)
    assert s.close_pending and s._writer.is_alive()
    assert s.lock_path.exists() and autosave._lock_held(s.lock_path) is True
    assert autosave.unfinished() == []                      # a live writer is not an abandoned session
    gate.set()
    s._writer.join(2)
    assert autosave._lock_held(s.lock_path) is False and s.lock_path.exists()
    autosave.report_unfinished()                            # what the next `voz` prints
    assert "no terminó bien" in capsys.readouterr().err


def test_close_with_a_finished_writer_is_not_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    s = autosave.Session(run_id="sesion-20261029-130001", take=1)
    s.write_audio(np.zeros(1024, dtype="float32"))
    s.close()
    assert not s.close_pending and not s.lock_path.exists()
    assert autosave._lock_held(s.lock_path) is None


# ---------- clipboard restore / retire ----------
def _take(sh, n, text):
    sh.flow.takes.append(F.Take(n=n, text=text, seconds=2))


def test_discard_does_not_overwrite_a_later_external_copy(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    sh.flow.state = F.St.REVIEW
    clip["v"] = "copiado en otra app"
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "copiado en otra app"
    assert sh.copied is None


def test_leaving_after_discarding_over_an_external_copy_keeps_it(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    _take(sh, 2, "dos"); sh._copy("dos", 2)
    sh.flow.state = F.St.REVIEW
    clip["v"] = "copiado en otra app"
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)          # discards "dos"
    sh.perform(F.step(sh.flow, F.ESC), None)                # leaves (one valid take: exit)
    assert clip["v"] == "copiado en otra app"


def test_discard_still_restores_when_the_clipboard_is_still_voz_text(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    _take(sh, 2, "dos"); sh._copy("dos", 2)
    sh.flow.state = F.St.REVIEW
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "uno"
    sh.perform(F.step(sh.flow, "u"), None)                  # undo copies it again
    assert clip["v"] == "dos"
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)          # back to "uno"
    sh.flow.state = F.St.REVIEW
    sh.flow.takes.pop()
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)          # nothing valid left: what the user had before
    assert clip["v"] == "lo que tenía antes"


def _live_shell(sh, text):
    sh.audio.seg = Segmenter()
    sh.audio.session = types.SimpleNamespace(committed=text, partial="", pending=0, abandoned=False,
                                             failed_chunks=0, drain=lambda timeout=None: True,
                                             submit=lambda c: None, release=lambda: None)
    sh.region.draw = lambda lines: None


def test_review_c_esc_takes_the_discarded_text_out_of_the_clipboard(shell):
    sh, clip = shell()
    _take(sh, 1, "uno"); sh._copy("uno", 1)
    sh.flow.state = F.St.REVIEW
    _live_shell(sh, "uno dos")
    sh.perform(F.step(sh.flow, "c"), None)                  # c: keep recording the same take
    assert sh.flow.state is F.St.LISTEN
    sh.perform(F.step(sh.flow, F.ESC), None)                # esc while listening: discard the whole take
    assert sh.flow.current.discarded and clip["v"] == "lo que tenía antes"


def test_esc_on_a_first_listen_never_touches_the_clipboard(shell):
    sh, clip = shell()
    sh.flow.takes.append(F.Take(n=1))
    sh.flow.state = F.St.LISTEN
    _live_shell(sh, "hablé")
    sh.perform(F.step(sh.flow, F.ESC), None)
    assert sh.flow.current.discarded and clip["v"] == "lo que tenía antes" and sh.copied is None


def _seed(state, run, n, *lines):
    stem = state / f"{run}-g{n}"
    stem.with_suffix(".txt").write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")


def test_history_copy_of_the_current_take_is_retired_when_that_take_is_discarded(shell, tmp_path):
    sh, clip = shell()
    sh.copy_mode = "preguntar"
    _seed(tmp_path, sh.run_id, 1, "grabación actual")
    _take(sh, 1, "grabación actual")
    sh.flow.state = F.St.REVIEW
    for k in ("h", F.ENTER):
        sh.perform(F.step(sh.flow, k), None)
    assert clip["v"] == "grabación actual"
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "lo que tenía antes"                # what it replaced, not the discarded text


def test_history_copy_then_external_copy_then_discard_keeps_the_external_one(shell, tmp_path):
    sh, clip = shell()
    sh.copy_mode = "preguntar"
    _seed(tmp_path, sh.run_id, 1, "grabación actual")
    _take(sh, 1, "grabación actual")
    sh.flow.state = F.St.REVIEW
    for k in ("h", F.ENTER):
        sh.perform(F.step(sh.flow, k), None)
    clip["v"] = "copiado en otra app"
    sh.perform(F.step(sh.flow, F.BACKSPACE), None)
    assert clip["v"] == "copiado en otra app"


def test_history_copy_of_an_older_session_survives_a_discard(shell, tmp_path):
    sh, clip = shell()
    sh.copy_mode = "preguntar"
    _seed(tmp_path, "sesion-20260102-103000", 1, "nota de otro día")
    _take(sh, 1, "grabación actual")
    sh.flow.state = F.St.REVIEW
    for k in ("h", F.ENTER, F.BACKSPACE):
        sh.perform(F.step(sh.flow, k), None)
    assert clip["v"] == "nota de otro día"


# ---------- discarded recordings stay reachable ----------
def _wav(path, secs):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * secs))


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    _seed(tmp_path, "sesion-20261103-090000", 1, "vieja buena")
    _seed(tmp_path, "sesion-20261103-120000", 1, "primera buena")
    _seed(tmp_path, "sesion-20261103-120000", 2, "se me escapó", "[descartada]")
    _seed(tmp_path, "sesion-20261103-120000", 3, "tercera buena")
    _wav(tmp_path / "sesion-20261103-120000-g2.wav", 5)
    return tmp_path


def test_listing_puts_discarded_after_the_valid_ones_without_renumbering(state):
    valid = autosave.recordings()
    assert [r.text for r in valid] == ["tercera buena", "primera buena", "vieja buena"]
    everything = autosave.listing()
    assert [r.text for r in everything[:3]] == [r.text for r in valid]       # same numbers 1-3
    assert everything[3].text == "se me escapó" and everything[3].discarded
    assert not any(r.discarded for r in everything[:3])


def test_copiar_lista_marks_discarded_and_keeps_valid_numbers(state, capsys):
    assert voz.main(["copiar", "--lista"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[0] for ln in lines] == ["1", "2", "3", "4"]
    assert "tercera buena" in lines[0] and "descartada" not in lines[0]
    assert "se me escapó" in lines[3] and lines[3].rstrip().endswith("descartada") and "0:05" in lines[3]


def test_copiar_n_is_unchanged_for_valid_and_can_pick_the_discarded_one(state, monkeypatch, capsys):
    import output

    box = {}
    monkeypatch.setattr(output, "to_clipboard", lambda t: box.__setitem__("v", t) or True)
    voz.main(["copiar", "2"])
    assert box["v"] == "primera buena"
    voz.main(["copiar"])
    assert box["v"] == "tercera buena"
    capsys.readouterr()
    assert voz.main(["copiar", "4"]) == 0
    assert box["v"] == "se me escapó" and "descartada" in capsys.readouterr().err


def test_history_shows_discarded_last_and_it_can_be_copied(shell, state):
    sh, clip = shell()
    _take(sh, 1, "grabación actual")
    sh.flow.state = F.St.REVIEW
    sh.perform(F.step(sh.flow, "h"), None)
    lines = [ui._ANSI.sub("", ln) for ln in sh.lines()]
    rows = [ln for ln in lines if "«" in ln]
    assert len(rows) == 4 and "descartada" in rows[3] and all("descartada" not in r for r in rows[:3])
    for k in (F.DOWN, F.DOWN, F.DOWN, F.ENTER):
        sh.perform(F.step(sh.flow, k), None)
    assert clip["v"] == "se me escapó"
    assert "descartada" in plain(sh.lines())


# ---------- collision names stay inside the contract ----------
def test_chained_name_collision_keeps_a_valid_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    for name in ("sesion-20261029-100000-g1", "sesion-20261029-100000-2-g1"):
        (tmp_path / f"{name}.lock").write_text("")        # both names already taken
    s = autosave.Session(run_id="sesion-20261029-100000-2", take=1)
    try:
        assert s.base.name == "sesion-20261029-100000-3-g1"
        assert autosave._RUN_RE.fullmatch(s.run_id)
        s.commit("sigue en la lista")
    finally:
        s.close()
    assert [r.text for r in autosave.recordings()] == ["sigue en la lista"]


def test_plain_collision_still_takes_the_next_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(autosave, "STATE_DIR", tmp_path)
    (tmp_path / "sesion-20261029-100000-g1.lock").write_text("")
    s = autosave.Session(run_id="sesion-20261029-100000", take=1)
    s.close()
    assert s.base.name == "sesion-20261029-100000-2-g1"
