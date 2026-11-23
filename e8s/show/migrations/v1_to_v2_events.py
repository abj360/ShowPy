"""
v1_to_v2_events.py --- the events sub-step of the v1 to v2 format migration

Canvas events are derived from revisions and never stored in the show documents this step
receives, so the step leaves the documents as they are. A stored event that still holds the v0.2
`actor` field is decoded with `decode_event()`, which drops the field and reports it.

Contains:
    migrate_events(): returns the documents unchanged, as they hold no stored events
"""

from typing import TYPE_CHECKING

from e8s.core.json_types import JsonValue

if TYPE_CHECKING:
    from e8s.show.migrate import ReportBuilder


def migrate_events(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Returns the documents unchanged; they hold no stored events.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    del report
    return docs
