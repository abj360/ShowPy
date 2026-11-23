"""
canvas_renames.py --- carries a migration's set and spot renames into the stored canvas files

The canvas comments and the canvas selection name sets and spots by ID. When a migration renames
those IDs, both files are rewritten through the same renames, so they keep pointing at what they
pointed at. Each file is parsed as strict JSON and validated through its reader's adapter; a file
that does not decode raises SchemaError rather than being left pointing at IDs that no longer
exist.

Contains:
    renamed_canvas(): rewrites the canvas comments and selection through a migration's renames
"""

from collections.abc import Mapping

from e8s.core.ids import SpotId
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.core.validation import validate_adapter
from e8s.show.anchors import renamed_anchor
from e8s.show.migrate import MigrationReport
from e8s.show.structured import (
    COMMENTS_ADAPTER,
    COMMENTS_DOC,
    SELECTION_ADAPTER,
    SELECTION_DOC,
)

SET_KIND = "set"
SPOT_KIND = "spot"


def _comments(raw: bytes, sets: Mapping[str, str], spots: Mapping[str, str]) -> JsonValue:
    """Returns the renamed comments as JSON, or None when no anchor changed."""
    parsed = parse_strict_json(raw, document=COMMENTS_DOC)
    comments = validate_adapter(COMMENTS_ADAPTER, parsed, name="Comments")
    renamed = [c.replace(anchor=renamed_anchor(c.anchor, sets, spots)) for c in comments]
    return None if renamed == comments else [c.model_dump(mode="json") for c in renamed]


def _selection(raw: bytes, spots: Mapping[str, str]) -> JsonValue:
    """Returns the renamed selection as JSON, or None when no ID changed."""
    parsed = parse_strict_json(raw, document=SELECTION_DOC)
    selection = validate_adapter(SELECTION_ADAPTER, parsed, name="Selection")
    renamed = selection.replace(ids=tuple(SpotId(spots.get(i, i)) for i in selection.ids))
    return None if renamed == selection else renamed.model_dump(mode="json")


def renamed_canvas(docs: Mapping[str, bytes], report: MigrationReport) -> dict[str, JsonValue]:
    """Rewrites the canvas comments and selection through a migration's set and spot renames.

    Args:
        docs: Stored path to document bytes, as a repository tree holds them.
        report: The migration's report; its `set` and `spot` renames are applied.

    Returns:
        rewritten: Path to the new JSON of each canvas file whose IDs changed; empty when none
            did. A canvas file that is not strict JSON or does not fit its model raises
            SchemaError.

    >>> from e8s.show.migrate import MigrationReport
    >>> renamed_canvas({"/canvas/selection.json": b'{"ids": ["T-1"]}'},
    ...                MigrationReport(from_version=1, renamed={"spot": (("T-1", "T_1"),)}))
    {'/canvas/selection.json': {'ids': ['T_1'], 'description': ''}}
    """
    sets = dict(report.renamed.get(SET_KIND, ()))
    spots = dict(report.renamed.get(SPOT_KIND, ()))
    if not sets and not spots:
        return {}
    rewritten: dict[str, JsonValue] = {}
    if COMMENTS_DOC in docs:
        rewritten[COMMENTS_DOC] = _comments(docs[COMMENTS_DOC], sets, spots)
    if SELECTION_DOC in docs:
        rewritten[SELECTION_DOC] = _selection(docs[SELECTION_DOC], spots)
    return {path: value for path, value in rewritten.items() if value is not None}
