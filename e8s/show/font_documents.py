"""
font_documents.py --- stores the fonts of a show's text forms as documents in its own tree

Contains:
    FONTS_ROOT: names the folder that holds font documents
    FONT_DOCUMENT_PATTERN: matches a font document path, named by the SHA-256 of its bytes
    font_changes(): lists the writes and deletes that make the tree hold the fonts a show needs
    load_fonts(): returns the bundled fonts plus the fonts stored in a workspace's tree
"""

import re
from collections.abc import Mapping

from e8s.choreo.form_models import TextRecipe
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import FontError, MissingFontError, SchemaError
from e8s.geo.fonts import MAX_FONT_BYTES, FontRegistry, bundled_fonts, register_font
from e8s.show.integrity import blob_digest
from e8s.show.models import Show
from e8s.show.workspace import Workspace

FONTS_ROOT = "/assets/fonts/"
FONT_DOCUMENT_PATTERN = re.compile(rf"{FONTS_ROOT}([0-9a-f]{{64}})\.json")
URI_PREFIX = "asset:sha256:"


def _stored_paths(tree: Mapping[str, bytes]) -> list[str]:
    return sorted(path for path in tree if path.startswith(FONTS_ROOT))


def _registry_from_tree(tree: Mapping[str, bytes]) -> FontRegistry:
    registry = bundled_fonts()
    for path in _stored_paths(tree):
        match = FONT_DOCUMENT_PATTERN.fullmatch(path)
        if match is None:
            raise SchemaError(
                f"{path} is not a font document name",
                hint=f"name font documents {FONTS_ROOT}<sha256 hex>.json",
                context={"path": path},
            )
        data = tree[path]
        if len(data) > MAX_FONT_BYTES:
            raise FontError(
                f"{path} is {len(data)} bytes, over {MAX_FONT_BYTES}",
                hint=f"store fonts of at most {MAX_FONT_BYTES} bytes",
                context={"path": path},
            )
        if blob_digest(data) != match.group(1):
            raise FontError(
                f"{path} does not hash to its name",
                hint="restore the font document from an earlier revision",
                context={"path": path},
                code=ErrorCode.FONT_HASH_MISMATCH,
            )
        registry, _ = register_font(registry, data, if_name_taken="keep")
    return registry


def load_fonts(ws: Workspace) -> FontRegistry:
    """Returns the bundled fonts plus the fonts stored in the workspace's tree.

    Args:
        ws: Workspace whose revision's `/assets/fonts/` documents are registered.

    Returns:
        registry: Fonts for `slots`, `place` and `rebuild` of the show's text forms. A document
            whose name is not the SHA-256 of its bytes raises FontError, and one whose name is
            not a hash raises SchemaError, each naming the path.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.show_io import save_show
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> _ = save_show(ws, two_set_show(), datetime(2026, 11, 24, tzinfo=UTC))
    >>> load_fonts(ws) is bundled_fonts()
    True
    """
    return _registry_from_tree(ws.tree_at(ws.revision))


def _needed_fonts(show: Show, registry: FontRegistry) -> dict[str, bytes]:
    bundled = bundled_fonts()
    needed: dict[str, bytes] = {}
    for form_id, form in show.forms.items():
        if not isinstance(form.recipe, TextRecipe):
            continue
        for uri in form.recipe.fonts:
            if uri in bundled.fonts or uri in needed:
                continue
            try:
                needed[uri] = registry.source(uri)
            except MissingFontError as exc:
                raise MissingFontError(
                    f"text form {form_id!r} uses font {uri}, which no registry holds",
                    hint="pass font_registry= holding the font to save_show()",
                    context={"form": form_id, "font": uri},
                ) from exc
    return needed


def font_changes(
    show: Show, tree: Mapping[str, bytes], font_registry: FontRegistry | None
) -> dict[str, bytes | None]:
    """Lists the writes and deletes that leave exactly the fonts `show` needs in the tree.

    Args:
        show: Show whose text forms name fonts.
        tree: Stored documents the write is made over.
        font_registry: Registry holding the show's fonts; None means the bundled fonts plus the
            fonts already in `tree`.

    Returns:
        changes: Stored path to the exact font bytes, or None for a font document no text form
            uses. A font found nowhere raises MissingFontError naming the font and the form.

    >>> from e8s.examples import two_set_show
    >>> font_changes(two_set_show(), {}, None)
    {}
    """
    registry = _registry_from_tree(tree) if font_registry is None else font_registry
    needed = {
        f"{FONTS_ROOT}{uri.removeprefix(URI_PREFIX)}.json": data
        for uri, data in _needed_fonts(show, registry).items()
    }
    changes: dict[str, bytes | None] = {
        path: data for path, data in needed.items() if tree.get(path) != data
    }
    changes.update({path: None for path in _stored_paths(tree) if path not in needed})
    return changes
