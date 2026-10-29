from stt import _clean_segments


def seg(text, no_speech=0.0, logprob=0.0):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def test_lone_junk_phrase_is_dropped():
    assert _clean_segments({"segments": [seg(" Gracias.")]}) == ""


def test_real_gracias_inside_dictation_survives():
    result = {"segments": [seg("Hola equipo,"), seg("gracias por venir.")]}
    assert _clean_segments(result) == "Hola equipo, gracias por venir."


def test_junk_phrase_among_real_speech_needs_non_speech_signal():
    kept = {"segments": [seg("Revisa el pedido."), seg("Gracias.", no_speech=0.1)]}
    dropped = {"segments": [seg("Revisa el pedido."), seg("Gracias.", no_speech=0.4)]}
    assert _clean_segments(kept) == "Revisa el pedido. Gracias."
    assert _clean_segments(dropped) == "Revisa el pedido."


def test_low_confidence_non_speech_is_dropped():
    result = {"segments": [seg("texto real"), seg("ruido", no_speech=0.6, logprob=-0.5)]}
    assert _clean_segments(result) == "texto real"


def test_confident_segment_with_high_no_speech_survives():
    result = {"segments": [seg("texto claro", no_speech=0.6, logprob=-0.1)]}
    assert _clean_segments(result) == "texto claro"


def test_amara_hallucination_dropped():
    result = {"segments": [seg("Subtítulos realizados por la comunidad de Amara.org")]}
    assert _clean_segments(result) == ""


def test_without_segments_falls_back_to_text():
    assert _clean_segments({"text": "  hola mundo  "}) == "hola mundo"


def test_empty_segments_are_skipped():
    assert _clean_segments({"segments": [seg("  "), seg("uno")]}) == "uno"


def test_loop_hallucination_is_collapsed():
    from stt import _collapse_repeats

    loop = " ".join(["I'm going to put a little bit on the bag."] * 12) + " y uno va adelantando"
    assert _collapse_repeats(loop) == "I'm going to put a little bit on the bag. y uno va adelantando"


def test_genuine_double_repetition_is_kept():
    from stt import _collapse_repeats

    assert _collapse_repeats("Sí. Sí. Listo.") == "Sí. Sí. Listo."


def test_segment_flagged_as_loop_is_dropped_when_it_looks_like_non_speech():
    seg_loop = {"text": "bla bla bla", "no_speech_prob": 0.4, "avg_logprob": -0.2, "compression_ratio": 3.1}
    seg_ok = {"text": "Hola equipo.", "no_speech_prob": 0.0, "avg_logprob": -0.2, "compression_ratio": 1.2}
    assert _clean_segments({"segments": [seg_ok, seg_loop]}) == "Hola equipo."


def test_flagged_window_with_real_speech_is_kept_and_collapsed():
    # mlx stamps compression_ratio per 30 s window: real dictation shares the flag
    text = "Necesito una caja de leche, una caja de leche, una caja de leche, una caja de leche."
    seg = {"text": text, "no_speech_prob": 0.01, "avg_logprob": -0.2, "compression_ratio": 3.2}
    assert _clean_segments({"segments": [seg]}) == "Necesito una caja de leche."
