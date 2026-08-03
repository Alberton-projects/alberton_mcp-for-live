import pytest

from alberton_mcp import api, files
from alberton_mcp.errors import ToolError


# --- audio import ------------------------------------------------------------


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "loop.wav"
    path.write_bytes(b"RIFF" + b"\0" * 200)
    return str(path)


async def test_import_into_arrangement(fake, session, wav):
    result = await api.import_audio_clip(session, track="Loops",
                                         file_path=wav, time=16.0,
                                         name="drop", color="#112233")
    clip = result["clip"]
    assert clip["view"] == "arrangement" and clip["start"] == 16.0
    assert clip["name"] == "drop" and clip["file"] == wav
    assert fake.live.song["tracks"][2]["arrangement_clips"][0]["file_path"] == wav


async def test_import_into_session_slot(fake, session, wav):
    result = await api.import_audio_clip(session, track="Loops",
                                         file_path=wav, slot=1, name="one shot")
    assert result["clip"]["view"] == "session" and result["clip"]["slot"] == 1
    slot = fake.live.song["tracks"][2]["clip_slots"][1]
    assert slot["has_clip"] and slot["clip"]["file_path"] == wav


async def test_audio_refused_on_midi_track(fake, session, wav):
    with pytest.raises(ToolError) as excinfo:
        await api.import_audio_clip(session, track="Lead", file_path=wav,
                                    time=0.0)
    assert "create_audio_track" in excinfo.value.hint


async def test_time_and_slot_are_mutually_exclusive(fake, session, wav):
    with pytest.raises(ToolError) as excinfo:
        await api.import_audio_clip(session, track="Loops", file_path=wav,
                                    time=0.0, slot=0)
    assert "exactly one" in excinfo.value.message
    with pytest.raises(ToolError):
        await api.import_audio_clip(session, track="Loops", file_path=wav)


async def test_missing_file_is_caught_before_the_wire(fake, session, tmp_path):
    fake.op_log.clear()
    with pytest.raises(ToolError) as excinfo:
        await api.import_audio_clip(session, track="Loops",
                                    file_path=str(tmp_path / "nope.wav"),
                                    time=0.0)
    assert excinfo.value.code == "not_found"
    assert not fake.op_log  # never asked Live


async def test_wrong_extension_lists_what_is_accepted(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not audio")
    with pytest.raises(ToolError) as excinfo:
        files.validate_audio_path(str(path))
    assert ".wav" in excinfo.value.hint


def test_empty_file_and_directory_are_rejected(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    with pytest.raises(ToolError):
        files.validate_audio_path(str(empty))
    with pytest.raises(ToolError) as excinfo:
        files.validate_audio_path(str(tmp_path))
    assert "folder" in excinfo.value.message


def test_relative_paths_and_tilde_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "s.aiff"
    path.write_bytes(b"FORM" + b"\0" * 50)
    assert files.validate_audio_path("~/s.aiff") == str(path)


# --- note summary -------------------------------------------------------------


PLAYED = [{"pitch": 64, "start": 0.051, "duration": 1.12, "velocity": 100},
          {"pitch": 67, "start": 0.052, "duration": 1.14, "velocity": 100},
          {"pitch": 71, "start": 0.053, "duration": 1.13, "velocity": 100},
          {"pitch": 60, "start": 3.47, "duration": 0.12, "velocity": 100}]
PROGRAMMED = [{"pitch": 36, "start": 0.0, "duration": 0.25, "velocity": 112},
              {"pitch": 42, "start": 0.5, "duration": 0.25, "velocity": 68},
              {"pitch": 38, "start": 2.0, "duration": 0.25, "velocity": 110}]


def test_summary_tells_played_from_programmed():
    played = api.summarize_notes(PLAYED, bar_beats=7.0)
    assert played["grid"]["verdict"] == "played"
    assert played["max_polyphony"] == 3
    assert played["velocity"]["distinct"] == 1
    programmed = api.summarize_notes(PROGRAMMED, bar_beats=7.0)
    assert programmed["grid"]["verdict"] == "quantised"
    assert programmed["grid"]["off_grid"] == 0


def test_summary_reports_pitches_and_density():
    summary = api.summarize_notes(PLAYED, bar_beats=7.0)
    assert summary["count"] == 4
    assert summary["pitch"]["min"] == "C3" and summary["pitch"]["max"] == "B3"
    assert summary["pitch"]["classes"] == ["C", "E", "G", "B"]
    assert summary["time"]["notes_per_bar"] == [4]


def test_empty_summary():
    assert api.summarize_notes([]) == {"count": 0}


async def test_get_notes_summary_replaces_the_note_dump(fake, session):
    await api.create_clip(session, track=0, slot=0, length=8.0, name="c",
                          notes=PROGRAMMED, signature_numerator=7,
                          signature_denominator=4)
    clip = {"track": 0, "slot": 0}
    full = await api.get_notes(session, clip=clip)
    summarised = await api.get_notes(session, clip=clip, summary=True)
    assert len(full["notes"]) == 3
    assert "notes" not in summarised
    assert summarised["summary"]["count"] == 3
    assert summarised["summary"]["time"]["bar_beats"] == 7.0


async def test_get_clip_note_summary(fake, session):
    await api.create_clip(session, track=0, slot=0, length=8.0, name="c",
                          notes=PROGRAMMED)
    result = await api.get_clip(session, clip={"track": 0, "slot": 0},
                                note_summary=True)
    assert result["note_summary"]["count"] == 3
    assert "notes" not in result


# --- browser cache -------------------------------------------------------------


async def test_browse_caches_then_refreshes(fake, session):
    first = await api.browse(session, query="fakesynth")
    assert first["index"]["instruments"]["items"] >= 1
    walks = sum(1 for op, f in fake.op_log if op == "get"
                and f.get("path") == "app.browser")
    await api.browse(session, query="fakepad")
    assert sum(1 for op, f in fake.op_log if op == "get"
               and f.get("path") == "app.browser") == walks  # cache hit
    await api.browse(session, query="fakepad", refresh=True)
    assert sum(1 for op, f in fake.op_log if op == "get"
               and f.get("path") == "app.browser") > walks


async def test_refresh_browser_index_drops_the_cache(fake, session):
    await api.browse(session, query="fakesynth", category="instruments")
    assert "instruments" in session.browser_cache
    result = await api.refresh_browser_index(session, category="instruments")
    assert result["dropped"] == ["instruments"]
    assert "instruments" not in session.browser_cache
