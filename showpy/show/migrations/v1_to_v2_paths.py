"""
v1_to_v2_paths.py --- the paths sub-step of the v1 to v2 format migration (lane B)

Lane B owns this module and fills it when its models change: motion parameters and route curves get
their typed v2 shape. Until then v1 and v2 share this shape, so the step returns the documents
unchanged. A filled step records every repair with `report.repair()` and refuses a stored value v2
cannot hold with SchemaError naming the document, the field and the value.

Contains:
    migrate_paths(): rewrites paths documents from the v1 shape to the v2 shape
"""

from typing import TYPE_CHECKING

from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder


def migrate_paths(docs: dict[str, JsonValue], report: "ReportBuilder") -> dict[str, JsonValue]:
    """Rewrites paths documents from the v1 shape to the v2 shape, unchanged so far.

    Args:
        docs: Stored path to parsed document, without a format header.
        report: Collects the repairs the step makes.

    Returns:
        docs: The documents in the v2 shape.
    """
    del report
    return docs
