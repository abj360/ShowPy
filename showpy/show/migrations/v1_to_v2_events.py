"""
v1_to_v2_events.py --- the events sub-step of the v1 to v2 format migration (lane A)

Lane A owns this module. Canvas events are derived from revisions, not stored in the show
documents this step receives, so it stays an identity step unless lane A finds stored event JSON
among them. A filled step records every repair with `report.repair()` and refuses a stored
value v2 cannot hold with SchemaError naming the document, the field and the value.

Contains:
    migrate_events(): rewrites any stored event JSON from the v1 shape to the v2 shape
"""

from typing import TYPE_CHECKING

from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder


def migrate_events(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites any stored event JSON to the v2 shape; the documents hold none so far.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    del report
    return docs
