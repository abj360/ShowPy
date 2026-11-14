#!/usr/bin/env python3

"""
test_video.py --- tests 2D frame rendering and MP4 export with optional audio

Also holds the audited video defects (UP-9.5-45 to UP-9.5-57) as strict xfails.

Contains:
    test_regression_short_audio_does_not_truncate_the_video(): keeps the video longer than the audio
    test_regression_sync_offset_past_the_audio_raises(): raises when the offset passes the audio
    test_regression_track_gain_is_applied(): plays a track at its gain
    test_regression_frames_cover_the_half_open_range(): renders frames over [t0, t1)
    test_regression_adjacent_ranges_do_not_duplicate_the_boundary_frame(): avoids a doubled frame
    test_regression_mp4_is_faststart(): puts the moov atom before mdat so web playback starts
        without the whole file
    test_regression_mp4_carries_bt709_colour_tags(): tags the stream BT.709 so players do not guess
        the colour matrix
    test_regression_video_canvas_caps_the_pixel_count(): rejects a 42,000 px wide canvas
    test_regression_frames_do_not_rebuild_the_cast_ids_per_spot(): builds the ID list once a frame
    test_regression_ffmpeg_start_failure_is_wrapped(): wraps a failed ffmpeg start in StorageError
    test_regression_audio_mux_has_a_timeout(): passes a timeout to the ffmpeg mux so a stuck process
        cannot hang the export
    test_regression_negative_sync_offset_delays_the_audio(): delays music for a negative offset
    test_regression_audio_section_limits_playback(): keeps a track silent before its section
"""

import array
import io
import math
import re
import struct
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import pytest

from showpy.cast.models import Cast
from showpy.choreo.engine import PathEngine
from showpy.core.errors import (
    SchemaError,
    ShowPyError,
    StorageError,
    TimeRangeError,
    UnknownRefError,
)
from showpy.core.util import approx_equal
from showpy.export.video import export_video, render_frame, video_canvas
from showpy.show.models import Show
from showpy.time.models import AudioTrack
from tests.fixtures.shows import two_set_show

SAMPLE_RATE = 8000
MUSIC = "asset://music"
FPS = 4
SMALL_PX_PER_M = 4.0
RANGE_SECONDS = 2.0
DURATION_TOL_S = 0.3
PAST_AUDIO_OFFSET_S = 30.0
QUIET_GAIN = 0.25
VALID_PX_PER_M = 8.0
SECTION_START_COUNT = 2.0
SILENT_LEAD_S = 0.8
HUGE_PX_PER_M = 1000.0


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        samples = (
            int(8000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE))
            for i in range(int(seconds * SAMPLE_RATE))
        )
        out.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buffer.getvalue()


def _probe(data: bytes, tmp_path: object) -> str:
    path = f"{tmp_path}/probe.mp4"
    with open(path, "wb") as handle:
        handle.write(data)
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stderr


def test_video_canvas_has_even_dimensions() -> None:
    width, height = video_canvas(two_set_show(), px_per_m=7.0).size
    assert width % 2 == 0
    assert height % 2 == 0


def test_render_frame_draws_spot_at_its_position() -> None:
    show = two_set_show()
    canvas = video_canvas(show, px_per_m=10.0)
    image = render_frame(show, PathEngine(show).sample(4.0), canvas)
    x, y = canvas.to_px((2.0, 7.0))
    assert image.size == canvas.size
    assert image.getpixel((round(x), round(y))) != image.getpixel((round(x) + 15, round(y) + 15))


def test_export_video_writes_a_valid_mp4(tmp_path: object) -> None:
    data = export_video(two_set_show(), count_range=(0.0, 4.0), fps=4, px_per_m=VALID_PX_PER_M)
    assert data[4:8] == b"ftyp"
    path = f"{tmp_path}/out.mp4"
    with open(path, "wb") as handle:
        handle.write(data)
    frames, _ = imageio_ffmpeg.count_frames_and_secs(path)
    assert frames > 0
    width, height = video_canvas(two_set_show(), VALID_PX_PER_M).size
    probe = _probe(data, tmp_path)
    assert "Video:" in probe
    assert f"{width}x{height}" in probe


def test_export_video_muxes_audio_from_source(tmp_path: object) -> None:
    show = two_set_show()
    track = AudioTrack(file="asset://music", label="music", sync_offset_s=0.5)
    show = show.model_copy(
        update={"timeline": show.timeline.model_copy(update={"audio": (track,)})}
    )
    data = export_video(
        show, count_range=(0.0, 4.0), fps=4, px_per_m=8.0, audio={"asset://music": _wav(4.0)}
    )
    assert "Audio:" in _probe(data, tmp_path)


def test_export_video_without_audio_has_no_audio_stream(tmp_path: object) -> None:
    data = export_video(two_set_show(), count_range=(0.0, 2.0), fps=4, px_per_m=8.0)
    assert "Audio:" not in _probe(data, tmp_path)


def test_export_video_missing_audio_asset_raises() -> None:
    show = two_set_show()
    track = AudioTrack(file="asset://missing", label="music")
    show = show.model_copy(
        update={"timeline": show.timeline.model_copy(update={"audio": (track,)})}
    )
    with pytest.raises(UnknownRefError):
        export_video(show, count_range=(0.0, 2.0), fps=4, audio={})


def test_export_video_validates_range_and_fps() -> None:
    with pytest.raises(TimeRangeError):
        export_video(two_set_show(), count_range=(4.0, 2.0))
    with pytest.raises(SchemaError):
        export_video(two_set_show(), fps=0)


def _with_track(offset_s: float = 0.0, gain: float = 1.0) -> Show:
    show = two_set_show()
    track = AudioTrack(file=MUSIC, label="music", sync_offset_s=offset_s, gain=gain)
    return show.model_copy(
        update={"timeline": show.timeline.model_copy(update={"audio": (track,)})}
    )


def _save(data: bytes, tmp_path: Path) -> Path:
    path = tmp_path / "out.mp4"
    path.write_bytes(data)
    return path


def _duration_s(stderr: str) -> float:
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr)
    assert match is not None, stderr
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def _audio_peak(path: Path, until_s: float = 60.0) -> int:
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-t", str(until_s), "-i", str(path)]
    result = subprocess.run(
        [*command, "-vn", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
        check=True,
    )
    samples = array.array("h", result.stdout)
    return max((abs(s) for s in samples), default=0)


def _frame_count(show: Show, tmp_path: Path, count_range: tuple[float, float]) -> int:
    data = export_video(show, count_range=count_range, fps=FPS, px_per_m=SMALL_PX_PER_M)
    return int(imageio_ffmpeg.count_frames_and_secs(str(_save(data, tmp_path)))[0])


@pytest.mark.defect("UP-9.5-45")
@pytest.mark.xfail(strict=True, reason="UP-9.5-45")
def test_regression_short_audio_does_not_truncate_the_video(tmp_path: Path) -> None:
    """Keeps the whole 2 s video when the audio is only 0.5 s long (audit 9.5).

    `-shortest` cuts the video to the audio length.
    """
    data = export_video(
        _with_track(),
        count_range=(0.0, 4.0),
        fps=FPS,
        px_per_m=SMALL_PX_PER_M,
        audio={MUSIC: _wav(0.5)},
    )
    assert approx_equal(_duration_s(_probe(data, tmp_path)), RANGE_SECONDS, DURATION_TOL_S)


@pytest.mark.defect("UP-9.5-47")
@pytest.mark.xfail(strict=True, reason="UP-9.5-47")
def test_regression_sync_offset_past_the_audio_raises() -> None:
    """Raises instead of returning a corrupt file when the offset is past the audio's end."""
    with pytest.raises(ShowPyError):
        export_video(
            _with_track(offset_s=PAST_AUDIO_OFFSET_S),
            count_range=(0.0, 4.0),
            fps=FPS,
            px_per_m=SMALL_PX_PER_M,
            audio={MUSIC: _wav(1.0)},
        )


@pytest.mark.defect("UP-9.5-48")
@pytest.mark.xfail(strict=True, reason="UP-9.5-48")
def test_regression_track_gain_is_applied(tmp_path: Path) -> None:
    """Plays a track at its `gain`; a quarter gain must be quieter than full gain (audit 9.5)."""
    peaks = {}
    for gain in (1.0, QUIET_GAIN):
        data = export_video(
            _with_track(gain=gain),
            count_range=(0.0, 4.0),
            fps=FPS,
            px_per_m=SMALL_PX_PER_M,
            audio={MUSIC: _wav(RANGE_SECONDS)},
        )
        peaks[gain] = _audio_peak(_save(data, tmp_path))
    assert peaks[QUIET_GAIN] < peaks[1.0] * 0.5


@pytest.mark.defect("UP-9.5-49")
@pytest.mark.xfail(strict=True, reason="UP-9.5-49")
def test_regression_frames_cover_the_half_open_range(tmp_path: Path) -> None:
    """Renders fps x duration frames over [t0, t1); the inclusive end adds one frame.

    A 4-count range at 120 cpm is 2 s, so 8 frames at 4 fps, not 9 (audit 9.5).
    """
    assert _frame_count(two_set_show(), tmp_path, (0.0, 4.0)) == round(RANGE_SECONDS * FPS)


@pytest.mark.defect("UP-9.5-50")
@pytest.mark.xfail(strict=True, reason="UP-9.5-50")
def test_regression_adjacent_ranges_do_not_duplicate_the_boundary_frame(tmp_path: Path) -> None:
    """Gives the same frame total for 0-2 plus 2-4 counts as for 0-4 (audit 9.5)."""
    show = two_set_show()
    whole = _frame_count(show, tmp_path, (0.0, 4.0))
    parts = _frame_count(show, tmp_path, (0.0, 2.0)) + _frame_count(show, tmp_path, (2.0, 4.0))
    assert parts == whole


@pytest.mark.defect("UP-9.5-51")
@pytest.mark.xfail(strict=True, reason="UP-9.5-51")
def test_regression_mp4_is_faststart() -> None:
    """Puts the moov atom before mdat so web playback starts without the whole file."""
    data = export_video(two_set_show(), count_range=(0.0, 1.0), fps=FPS, px_per_m=SMALL_PX_PER_M)
    assert data.index(b"moov") < data.index(b"mdat")


@pytest.mark.defect("UP-9.5-52")
@pytest.mark.xfail(strict=True, reason="UP-9.5-52")
def test_regression_mp4_carries_bt709_colour_tags(tmp_path: Path) -> None:
    """Tags the stream BT.709 so players do not guess the colour matrix (audit 9.5)."""
    data = export_video(two_set_show(), count_range=(0.0, 1.0), fps=FPS, px_per_m=SMALL_PX_PER_M)
    assert "bt709" in _probe(data, tmp_path)


@pytest.mark.defect("UP-9.5-53")
@pytest.mark.xfail(strict=True, reason="UP-9.5-53")
def test_regression_video_canvas_caps_the_pixel_count() -> None:
    """Rejects `px_per_m=1000`, a 42,000 x 25,000 px canvas, with SchemaError (P0 #8)."""
    with pytest.raises(SchemaError):
        video_canvas(two_set_show(), HUGE_PX_PER_M)


@pytest.mark.defect("UP-9.5-54")
@pytest.mark.xfail(strict=True, reason="UP-9.5-54")
def test_regression_frames_do_not_rebuild_the_cast_ids_per_spot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builds the cast ID list at most once per frame, not twice per spot (audit 9.5)."""
    show = two_set_show()
    canvas = video_canvas(show, SMALL_PX_PER_M)
    frame = PathEngine(show).sample(0.0)
    real_ids = Cast.ids
    calls = []

    def counting(self: Cast) -> tuple[str, ...]:
        calls.append(1)
        return real_ids(self)

    monkeypatch.setattr(Cast, "ids", counting)
    render_frame(show, frame, canvas)
    assert len(calls) <= 1


@pytest.mark.defect("UP-9.5-55")
@pytest.mark.xfail(strict=True, reason="UP-9.5-55")
def test_regression_ffmpeg_start_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raises StorageError when the ffmpeg writer cannot start, not a raw OS or runtime error."""
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/nonexistent/ffmpeg")
    with pytest.raises(StorageError):
        export_video(two_set_show(), count_range=(0.0, 1.0), fps=FPS, px_per_m=SMALL_PX_PER_M)


@pytest.mark.defect("UP-9.5-56")
@pytest.mark.xfail(strict=True, reason="UP-9.5-56")
def test_regression_audio_mux_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passes a timeout to the ffmpeg mux so a stuck process cannot hang the export."""
    seen: list[dict[str, object]] = []
    real_run = subprocess.run

    def recording(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(dict(kwargs))
        return real_run(*args, **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(subprocess, "run", recording)
    export_video(
        _with_track(),
        count_range=(0.0, 1.0),
        fps=FPS,
        px_per_m=SMALL_PX_PER_M,
        audio={MUSIC: _wav(1.0)},
    )
    assert seen
    assert all(call.get("timeout") is not None for call in seen)


@pytest.mark.defect("UP-9.5-46")
@pytest.mark.xfail(strict=True, reason="UP-9.5-46")
def test_regression_negative_sync_offset_delays_the_audio(tmp_path: Path) -> None:
    """Starts the music 1 s into the video when count 0 is 1 s before the music (audit 9.5).

    A negative offset is legitimate; the audit reports a file without audio.
    """
    data = export_video(
        _with_track(offset_s=-1.0),
        count_range=(0.0, 4.0),
        fps=FPS,
        px_per_m=SMALL_PX_PER_M,
        audio={MUSIC: _wav(RANGE_SECONDS * 2)},
    )
    path = _save(data, tmp_path)
    assert _audio_peak(path, until_s=SILENT_LEAD_S) == 0
    assert _audio_peak(path) > 0


@pytest.mark.defect("UP-9.5-57")
@pytest.mark.xfail(strict=True, reason="UP-9.5-57")
def test_regression_audio_section_limits_playback(tmp_path: Path) -> None:
    """Keeps a track silent before its `section` start (counts the track covers) (audit 9.5).

    `AudioTrack.section` is the (start, end) counts the track covers. With section (2, 4) at
    120 cpm, the first count 2 is 1 s, so the video's first 0.8 s carry no music. Today the
    field is ignored and the music plays from the start.
    """
    show = _with_track()
    track = show.timeline.audio[0].model_copy(update={"section": (SECTION_START_COUNT, 4.0)})
    show = show.model_copy(
        update={"timeline": show.timeline.model_copy(update={"audio": (track,)})}
    )
    data = export_video(
        show,
        count_range=(0.0, 4.0),
        fps=FPS,
        px_per_m=SMALL_PX_PER_M,
        audio={MUSIC: _wav(RANGE_SECONDS)},
    )
    path = _save(data, tmp_path)
    assert _audio_peak(path, until_s=SILENT_LEAD_S) == 0
    assert _audio_peak(path) > 0
