"""
font_paths.py --- names the font documents a show stores and checks each one a commit writes

Contains:
    FONTS_ROOT: names the folder that holds font documents
    FONT_DOCUMENT_PATTERN: matches a font document path, named by the SHA-256 of its bytes
    require_font_document(): refuses a font document whose name, size or hash is wrong
    require_written_fonts(): refuses a written font document that is misnamed or no font
"""

import re
from collections.abc import Callable, Iterable, Mapping
from functools import partial

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import FontError, SchemaError
from e8s.core.util import ASSET_PREFIX, asset_uri
from e8s.geo.fonts import MAX_FONT_BYTES, load_font
from e8s.show.decode_cache import DecodeCache

FONTS_ROOT = "/assets/fonts/"
FONT_DOCUMENT_PATTERN = re.compile(rf"{FONTS_ROOT}([0-9a-f]{{64}})\.json")
FONT_CHECKED = "font-checked"
NAME_HINT = (
    f"name a font document {FONTS_ROOT}<SHA-256 of its exact bytes, 64 lowercase hex>.json,"
    " as save_show() does"
)


def require_font_document(path: str, data: bytes) -> None:
    """Refuses a font document not named by the SHA-256 of its bytes, or over MAX_FONT_BYTES.

    Args:
        path: Stored path under FONTS_ROOT.
        data: Bytes stored at the path.

    >>> from e8s.core.util import asset_uri
    >>> data = b"{}"
    >>> require_font_document(f"{FONTS_ROOT}{asset_uri(data).removeprefix('asset:sha256:')}.json",
    ...                       data)
    >>> try:
    ...     require_font_document(f"{FONTS_ROOT}{'0' * 64}.json", data)
    ... except FontError as exc:
    ...     print(exc.code)
    FONT_HASH_MISMATCH
    """
    match = FONT_DOCUMENT_PATTERN.fullmatch(path)
    if match is None:
        raise SchemaError(
            f"{path} is not a font document name",
            hint=NAME_HINT,
            context={"path": path},
        )
    if len(data) > MAX_FONT_BYTES:
        raise FontError(
            f"{path} is {len(data)} bytes, over {MAX_FONT_BYTES}",
            hint=f"store fonts of at most {MAX_FONT_BYTES} bytes",
            context={"path": path},
        )
    if asset_uri(data) != f"{ASSET_PREFIX}{match.group(1)}":
        raise FontError(
            f"{path} does not hash to its name",
            hint=f"{NAME_HINT}; restore a changed one from an earlier revision",
            context={"path": path},
            code=ErrorCode.FONT_HASH_MISMATCH,
        )


def _check_written_font(path: str, digest: str, blob: Callable[[str], bytes]) -> None:
    data = blob(digest)
    require_font_document(path, data)
    try:
        load_font(data)
    except FontError as exc:
        raise FontError(
            f"{path} is not a font load_fonts() can register: {exc.message}",
            hint=exc.hint,
            context={**exc.context, "path": path},
            code=exc.code,
        ) from exc


def require_written_fonts(
    written: Iterable[str],
    after: Mapping[str, str],
    blob: Callable[[str], bytes],
    cache: DecodeCache | None = None,
) -> None:
    """Refuses a written font document that is misnamed, oversize, mis-hashed or not a font.

    Only written paths are checked, so a bad font document stored before the check can still be
    deleted. Each check runs once per path and digest when a cache is given.

    Args:
        written: Paths the commit writes.
        after: Path to blob digest the commit would leave.
        blob: Returns the bytes of a digest; called only for written font documents.
        cache: Checks of earlier commits, or None to check every written font document.

    >>> require_written_fonts(["/renders/a.txt"], {"/renders/a.txt": "d"}, lambda d: b"")
    >>> try:
    ...     require_written_fonts([f"{FONTS_ROOT}x.json"], {f"{FONTS_ROOT}x.json": "d"},
    ...                           lambda d: b"{}")
    ... except SchemaError as exc:
    ...     print(exc.context["path"])
    /assets/fonts/x.json
    """
    for path in sorted(p for p in written if p.startswith(FONTS_ROOT)):
        digest = after[path]
        check = partial(_check_written_font, path, digest, blob)
        if cache is None:
            check()
        else:
            cache.get_or_decode(path, f"{digest} {FONT_CHECKED}", check)
