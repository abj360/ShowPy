"""
v1_to_v2_timeline.py --- the timeline sub-step of the v1 to v2 format migration (lane T)

Lane T owns this module and fills it when its models change: tempo sections are sorted by count, and
a tempo map that sorting would change is refused. Until then v1 and v2 share this shape, so the step
returns the documents unchanged. A filled step records every repair with `report.repair()` and
refuses a stored value v2 cannot hold with SchemaError naming the document, the field and the value.

Contains:
    migrate_timeline(): rewrites the timeline document from the v1 shape to the v2 shape
"""

from typing import TYPE_CHECKING

from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder


def migrate_timeline(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites the timeline document from the v1 shape to the v2 shape, unchanged so far.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    del report
    return docs
