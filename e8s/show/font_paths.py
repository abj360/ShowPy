"""
font_paths.py --- names the font documents a show stores and checks each one's name, size and hash

Contains:
    FONTS_ROOT: names the folder that holds font documents
    FONT_DOCUMENT_PATTERN: matches a font document path, named by the SHA-256 of its bytes
    require_font_document(): refuses a font document whose name, size or hash is wrong
"""

import re

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import FontError, SchemaError
from e8s.core.util import ASSET_PREFIX, asset_uri
from e8s.geo.fonts import MAX_FONT_BYTES

FONTS_ROOT = "/assets/fonts/"
FONT_DOCUMENT_PATTERN = re.compile(rf"{FONTS_ROOT}([0-9a-f]{{64}})\.json")


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
            hint=f"name font documents {FONTS_ROOT}<sha256 hex>.json",
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
            hint="restore the font document from an earlier revision",
            context={"path": path},
            code=ErrorCode.FONT_HASH_MISMATCH,
        )
