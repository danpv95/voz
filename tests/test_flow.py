import flow as F
from flow import BACKSPACE, ENTER, ESC, SPACE, Act, Flow, St, finished, joined, norm, step


def keys(flow, *ks):
    return [step(flow, k).action for k in ks]


def test_norm_maps_raw_keys():
    assert norm("\r") == norm("\n") == ENTER
    assert norm(" ") == SPACE
    assert norm("\x1b") == ESC and norm("\x7f") == norm("\x08") == BACKSPACE
    assert norm("R") == "r" and norm(None) is None


def test_daily_flow_is_three_enters():
    f = Flow()
    assert keys(f, ENTER, ENTER) == [Act.START, Act.FINISH]
    finished(f, "hola mundo", 4.0)
    assert f.state is St.REVIEW
    assert keys(f, ENTER) == [Act.EXIT] and f.state is St.DONE


def test_pause_resume_then_finish_from_pause():
    f = Flow()
    assert keys(f, ENTER, "p") == [Act.START, Act.PAUSE] and f.state is St.PAUSED
    assert keys(f, norm("P")) == [Act.RESUME] and f.state is St.LISTEN
    assert keys(f, "p", ENTER) == [Act.PAUSE, Act.FINISH] and f.state is St.FINISHING


def test_continue_reopens_same_take():
    f = Flow(); keys(f, ENTER, ENTER); finished(f, "primera parte", 3)
    assert keys(f, "c") == [Act.CONTINUE] and f.state is St.LISTEN and len(f.takes) == 1


def test_record_another_creates_new_take():
    f = Flow(); keys(f, ENTER, ENTER); finished(f, "uno", 3)
    assert keys(f, "r") == [Act.START] and len(f.takes) == 2 and f.current.n == 2


def test_discard_and_undo_never_lose_text():
    f = Flow(); keys(f, ENTER, ENTER); finished(f, "texto", 3)
    assert keys(f, BACKSPACE) == [Act.DISCARD] and f.current.discarded
    assert f.current.text == "texto"                    # kept, only marked
    assert keys(f, "c") == [Act.NOOP]                   # can't continue a discarded take
    assert keys(f, "u") == [Act.UNDO] and f.current.valid


def test_esc_while_recording_finishes_as_discarded_and_is_undoable():
    f = Flow(); keys(f, ENTER, ESC)
    finished(f, "lo grabado", 5)
    assert f.state is St.REVIEW and f.current.discarded and f.current.text == "lo grabado"
    assert keys(f, "u") == [Act.UNDO] and f.current.valid


def test_no_voice_offers_retry():
    f = Flow(); keys(f, ENTER, ENTER); finished(f, "   ", 6)
    assert f.state is St.NOVOICE and not f.current.valid
    assert keys(f, "r") == [Act.START] and len(f.takes) == 2


def test_leaving_with_two_valid_takes_asks_all_or_last():
    f = Flow()
    keys(f, ENTER, ENTER); finished(f, "uno", 2)
    keys(f, "r", ENTER); finished(f, "dos", 2)
    assert keys(f, ENTER) == [Act.NOOP] and f.state is St.CHOOSE
    s = step(f, "a")
    assert s.action is Act.EXIT and s.copy_all and joined(f.takes) == "uno\n\ndos"


def test_leaving_with_one_valid_plus_discarded_exits_directly():
    f = Flow()
    keys(f, ENTER, ENTER); finished(f, "uno", 2)
    keys(f, "r", ENTER); finished(f, "dos", 2); keys(f, BACKSPACE)
    assert keys(f, ENTER) == [Act.EXIT] and f.state is St.DONE


def test_choose_esc_keeps_clipboard():
    f = Flow()
    keys(f, ENTER, ENTER); finished(f, "uno", 2); keys(f, "r", ENTER); finished(f, "dos", 2)
    keys(f, ESC)
    s = step(f, ESC)
    assert s.action is Act.EXIT and s.keep_clipboard


def test_unknown_keys_are_ignored_everywhere():
    f = Flow()
    for k in ("x", "z", "9", BACKSPACE):
        assert step(f, k).action is Act.NOOP
    assert f.state is St.READY


def test_mic_failure_ends_in_the_no_audio_screen_and_enter_retries():
    f = F.Flow()
    assert F.step(f, F.ENTER).action is F.Act.START
    F.mic_failed(f)
    assert f.state is F.St.NOVOICE and f.current.nomic and f.current.novoice
    assert F.step(f, F.ENTER).action is F.Act.START          # ↵ reintentar
    assert f.state is F.St.LISTEN and [t.n for t in f.takes] == [1, 2]


def test_enter_leaves_a_plain_no_voice_screen_but_retries_a_mic_one():
    plain = F.Flow(state=F.St.NOVOICE, takes=[F.Take(n=1, novoice=True)])
    assert F.step(plain, F.ENTER).action is F.Act.EXIT
    mic = F.Flow(state=F.St.NOVOICE, takes=[F.Take(n=1, novoice=True, nomic=True)])
    assert F.step(mic, F.ESC).action is F.Act.EXIT
    mic.state = F.St.NOVOICE
    assert F.step(mic, "r").action is F.Act.START
