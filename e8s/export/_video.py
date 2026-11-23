"""
_video.py --- renders the animated 2D sheet to MP4, with the show's audio when supplied

Frames are sampled by the PathEngine at the music's clock (fps per second of audio time,
honouring tempo and holds), drawn on the same sheet background as printouts, and encoded
with the ffmpeg binary bundled by imageio-ffmpeg. Audio only plays; it never changes timing.
Frames draw through Pillow, the `render` extra; encoding needs the `video` extra.

Contains:
    video_canvas(): builds a pixel canvas for a show with even dimensions for H.264
    render_frame(): draws one engine frame over the sheet
    export_video(): renders a count range to MP4 bytes, muxing the first audio track if given
"""

import math
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from e8s.choreo.engine import PathEngine
from e8s.choreo.engine_models import EntityState, Frame
from e8s.core.caps import MAX_VIDEO_PIXELS, require_size
from e8s.core.errors import SchemaError, StorageError, TimeRangeError, UnknownRefError
from e8s.core.extras import require_extra
from e8s.core.types import Count, Point
from e8s.core.units import facing_vector
from e8s.core.validation import require_finite
from e8s.sheet.render import BACKGROUND, SheetCanvas, _draw_sheet
from e8s.show.models import Show
from e8s.time.clock import count_to_seconds
from e8s.time.models import AudioTrack

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

DEFAULT_FPS = 30
DEFAULT_PX_PER_M = 10.0
MIN_DOT_PX = 2.0
FACING_TICK = 1.6
DEFAULT_COLOR = "#808080"
TICK_COLOR = "#000000"
VIDEO_CODEC = "libx264"
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
VIDEO_NAME = "video.mp4"
AUDIO_NAME = "audio.bin"
OUTPUT_NAME = "out.mp4"


def _require_finite_extent(canvas: SheetCanvas) -> None:
    extent_x = (canvas.max_x - canvas.min_x) * canvas.px_per_m
    extent_y = (canvas.max_y - canvas.min_y) * canvas.px_per_m
    if math.isfinite(extent_x) and math.isfinite(extent_y):
        return
    raise SchemaError(
        "video pixels would overflow",
        hint="ask for fewer video pixels or use a coarser setting",
        context={"what": "video pixels", "cap": MAX_VIDEO_PIXELS},
    )


def video_canvas(show: Show, px_per_m: float = DEFAULT_PX_PER_M) -> SheetCanvas:
    """Builds a canvas over the show's sheet whose pixel size is even (H.264 needs it).

    >>> from e8s.examples import two_set_show
    >>> [n % 2 for n in video_canvas(two_set_show(), 7.0).size]
    [0, 0]
    """
    px_per_m = require_finite(px_per_m, name="px_per_m", low=0, low_open=True)
    canvas = SheetCanvas.for_sheet(show.sheet, px_per_m=px_per_m)
    _require_finite_extent(canvas)
    width, height = canvas.size
    require_size((width + width % 2) * (height + height % 2), MAX_VIDEO_PIXELS, what="video pixels")
    pad_x = (width % 2) / px_per_m
    pad_y = (height % 2) / px_per_m
    padded = canvas.replace(max_x=canvas.max_x + pad_x, max_y=canvas.max_y + pad_y)
    if any(n % 2 for n in padded.size):
        raise SchemaError(
            f"px_per_m {px_per_m} leaves an odd video canvas {padded.size}, which H.264 rejects",
            hint="use a larger px_per_m, such as the default 10.0",
            context={"px_per_m": px_per_m, "size": padded.size},
        )
    return padded


def _color(show: Show, entity: str) -> str:
    if entity not in show.cast.ids():
        return DEFAULT_COLOR
    spot = show.cast.spot(entity)
    return spot.color or show.cast.groups[spot.group].color


def _draw_state(
    draw: "ImageDraw.ImageDraw", canvas: SheetCanvas, show: Show, state: EntityState
) -> None:
    extent = show.cast.spot(state.entity).shape.extent_m if state.entity in show.cast.ids() else 0.3
    radius = max(extent * canvas.px_per_m, MIN_DOT_PX)
    x, y = canvas.to_px(state.position)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=_color(show, state.entity))
    fx, fy = facing_vector(state.facing)
    tip: Point = (
        state.position[0] + fx * extent * FACING_TICK,
        state.position[1] + fy * extent * FACING_TICK,
    )
    draw.line((x, y, *canvas.to_px(tip)), fill=TICK_COLOR, width=max(1, round(radius / 3)))


def render_frame(show: Show, frame: Frame, canvas: SheetCanvas) -> "Image.Image":
    """Draws one engine frame over the sheet; off-field spots are not drawn.

    Needs the `render` extra.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> render_frame(show, PathEngine(show).sample(0.0), video_canvas(show)).mode
    'RGB'
    """
    require_extra("PIL", extra="render", needed_by="render_frame")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", canvas.size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    _draw_sheet(draw, canvas, show.sheet)
    for state in frame.states:
        if state.on_field:
            _draw_state(draw, canvas, show, state)
    return image


def _range(show: Show, count_range: tuple[Count, Count] | None) -> tuple[Count, Count]:
    total = sum(s.counts for s in show.sets[1:])
    t0, t1 = count_range or (0.0, total)
    if not 0 <= t0 < t1 <= total:
        raise TimeRangeError(
            f"count range {t0}–{t1} is outside the show (0–{total}) or empty",
            hint=f"pass 0 <= start < end <= {total}",
            context={"start": t0, "end": t1},
        )
    return t0, t1


def _read_encoded(path: Path) -> bytes:
    data = path.read_bytes()
    if data:
        return data
    raise StorageError(
        "ffmpeg wrote an empty video",
        hint="check the px_per_m and fps settings",
        context={"file": path.name},
    )


def _write_frames(
    show: Show, path: Path, t0: Count, t1: Count, fps: float, px_per_m: float
) -> None:
    import imageio_ffmpeg

    canvas = video_canvas(show, px_per_m)
    engine = PathEngine(show)
    writer = imageio_ffmpeg.write_frames(
        str(path),
        canvas.size,
        fps=fps,
        codec=VIDEO_CODEC,
        pix_fmt_out=PIXEL_FORMAT,
        macro_block_size=1,
    )
    writer.send(None)
    for frame in engine.frames(t0, t1, fps=fps):
        writer.send(render_frame(show, frame, canvas).tobytes())
    writer.close()


def _track(show: Show, audio: Mapping[str, bytes]) -> tuple[AudioTrack, bytes] | None:
    if not show.timeline.audio:
        return None
    track = show.timeline.audio[0]
    if track.file not in audio:
        raise UnknownRefError(
            f"no audio bytes for {track.file!r}",
            hint="load the track from the asset store and pass it in `audio`",
            context={"file": track.file},
        )
    return track, audio[track.file]


def _mux(video: Path, audio: Path, out: Path, start_s: float) -> None:
    import imageio_ffmpeg

    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-i", str(video), "-ss", f"{start_s:.6f}", "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", AUDIO_CODEC,
        "-shortest", str(out),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise StorageError(
            "ffmpeg could not add the audio track",
            hint="check that the audio asset is a readable audio file",
            context={"stderr": result.stderr.strip()[-500:]},
        )


def export_video(
    show: Show,
    count_range: tuple[Count, Count] | None = None,
    fps: float = DEFAULT_FPS,
    audio: Mapping[str, bytes] | None = None,
    px_per_m: float = DEFAULT_PX_PER_M,
) -> bytes:
    """Renders a count range of the show to MP4 bytes.

    With `audio` (track file URI to bytes), the timeline's first track is muxed, starting at
    its sync offset plus the range start in seconds. Without it the video is silent. Needs the
    `video` extra.

    >>> from e8s.examples import two_set_show
    >>> export_video(two_set_show(), (0.0, 1.0), fps=2, px_per_m=4.0)[4:8]
    b'ftyp'
    """
    require_extra("imageio_ffmpeg", extra="video", needed_by="export_video")
    require_extra("PIL", extra="video", needed_by="export_video")
    if not fps > 0 or not math.isfinite(fps):
        raise SchemaError(f"fps must be above 0, got {fps}", hint="use e.g. fps=30")
    t0, t1 = _range(show, count_range)
    source = _track(show, audio) if audio is not None else None
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _write_frames(show, root / VIDEO_NAME, t0, t1, fps, px_per_m)
        if source is None:
            return _read_encoded(root / VIDEO_NAME)
        track, data = source
        (root / AUDIO_NAME).write_bytes(data)
        start_s = track.sync_offset_s + count_to_seconds(show.timeline, t0)
        _mux(root / VIDEO_NAME, root / AUDIO_NAME, root / OUTPUT_NAME, start_s)
        return _read_encoded(root / OUTPUT_NAME)
