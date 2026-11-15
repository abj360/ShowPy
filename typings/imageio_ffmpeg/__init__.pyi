"""
__init__.pyi --- type stub for the parts of imageio-ffmpeg 0.8 that ShowPy and its tests call

The package ships no annotations, so checkers inferred `fps: int` from its default of 16
although write_frames accepts a float or a Fraction.

Contains:
    get_ffmpeg_exe(): returns the path of the bundled or configured ffmpeg binary
    count_frames_and_secs(): counts the frames and seconds of a video file
    write_frames(): opens a generator that encodes raw RGB frames sent to it
"""

from collections.abc import Generator
from fractions import Fraction
from pathlib import Path

def get_ffmpeg_exe() -> str: ...
def count_frames_and_secs(path: str | Path) -> tuple[int | None, float | None]: ...
def write_frames(
    path: str | Path,
    size: tuple[int, int],
    pix_fmt_in: str = ...,
    pix_fmt_out: str = ...,
    fps: float | Fraction = ...,
    quality: float | None = ...,
    bitrate: str | None = ...,
    codec: str | None = ...,
    macro_block_size: int = ...,
    ffmpeg_log_level: str = ...,
    ffmpeg_timeout: float | None = ...,
    input_params: list[str] | None = ...,
    output_params: list[str] | None = ...,
    audio_path: str | None = ...,
    audio_codec: str | None = ...,
) -> Generator[None, bytes | None]: ...
