import json

from output import to_json, to_srt, unique_path, write_sidecar

SEGS = [
    {"start": 0.0, "end": 1.25, "text": "Hola"},
    {"start": 1.25, "end": None, "text": "mundo"},
]


def test_srt_format_and_numbering():
    srt = to_srt(SEGS)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,250\nHola\n")
    assert "2\n00:00:01,250 --> 00:00:03,250\nmundo\n" in srt


def test_srt_hours():
    assert "01:01:01,500" in to_srt([{"start": 3661.5, "end": 3662.0, "text": "x"}])


def test_unknown_end_uses_next_start():
    segs = [{"start": 0.0, "end": None, "text": "a"}, {"start": 4.0, "end": 5.0, "text": "b"}]
    assert json.loads(to_json(segs))[0]["end"] == 4.0


def test_json_keeps_unicode():
    assert "Estás" in to_json([{"start": 0.0, "end": 1.0, "text": "Estás"}])


def test_unique_path_never_overwrites(tmp_path):
    first = tmp_path / "a.srt"
    assert unique_path(first) == first
    first.write_text("x")
    assert unique_path(first) == tmp_path / "a-1.srt"
    (tmp_path / "a-1.srt").write_text("x")
    assert unique_path(first) == tmp_path / "a-2.srt"


def test_write_sidecar_next_to_audio(tmp_path):
    audio = tmp_path / "reunion.m4a"
    audio.write_bytes(b"")
    dest = write_sidecar(audio, ".srt", "contenido")
    assert dest == tmp_path / "reunion.srt" and dest.read_text() == "contenido"
    assert write_sidecar(audio, ".srt", "otro") == tmp_path / "reunion-1.srt"


def test_sidecar_never_overwrites_a_file_created_in_the_meantime(tmp_path, monkeypatch):
    import output

    audio = tmp_path / "reunion.m4a"
    taken = tmp_path / "reunion.srt"
    taken.write_text("de otro proceso", encoding="utf-8")
    real = output.unique_path
    calls = []

    def racy(path):
        calls.append(1)
        return taken if len(calls) == 1 else real(path)    # first answer is stale: it exists now

    monkeypatch.setattr(output, "unique_path", racy)
    dest = output.write_sidecar(audio, ".srt", "mio")
    assert dest == tmp_path / "reunion-1.srt" and dest.read_text(encoding="utf-8") == "mio"
    assert taken.read_text(encoding="utf-8") == "de otro proceso"
