#!/usr/bin/env python3

"""
test_video.py --- tests 2D frame rendering and MP4 export with optional audio
"""

import io
import math
import struct
import subprocess
import wave

import imageio_ffmpeg
import pytest

from showpy.choreo.engine import PathEngine
from showpy.core.errors import SchemaError, TimeRangeError, UnknownRefError
from showpy.export.video import export_video, render_frame, video_canvas
from showpy.time.models import AudioTrack
from tests.fixtures.shows import two_set_show

SAMPLE_RATE = 8000


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


def test_export_video_writes_mp4_with_expected_frames(tmp_path: object) -> None:
    data = export_video(two_set_show(), count_range=(0.0, 4.0), fps=4, px_per_m=8.0)
    assert data[4:8] == b"ftyp"
    path = f"{tmp_path}/out.mp4"
    with open(path, "wb") as handle:
        handle.write(data)
    frames, _ = imageio_ffmpeg.count_frames_and_secs(path)
    assert frames == 9


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
