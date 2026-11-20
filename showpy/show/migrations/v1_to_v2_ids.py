"""
v1_to_v2_ids.py --- the ids sub-step of the v1 to v2 format migration (lane A)

The last sub-step. It runs on the show validated with any non-empty v1 ID accepted, renames
each ID that breaks the ID pattern, splits IDs that differ only by case, and records every
rename with `report.rename()` so a host can follow it in what it stores outside ShowPy. Lane A
owns this module and fills it; until then it renames nothing.

Validation after this step keeps accepting v1 IDs while KEEPS_LEGACY_IDS is True, because an
unfilled step leaves v1 IDs such as `brass-2` or `con` in place and those shows must still load.
The change that makes the step rename every such ID sets KEEPS_LEGACY_IDS to False in the same
commit, which turns every ID check on for migrated shows.

Contains:
    KEEPS_LEGACY_IDS: tells whether validation after this step still accepts any non-empty v1 ID
    rename_ids(): renames v1 IDs that break the ID pattern, recording each rename
"""

from typing import TYPE_CHECKING, Final

from showpy.show.models import Show

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

KEEPS_LEGACY_IDS: Final = True


def rename_ids(show: Show, report: "ReportBuilder") -> Show:
    """Renames v1 IDs that break the ID pattern; renames nothing yet.

    Args:
        show: Show validated with any non-empty v1 ID accepted.
        report: Collects each rename, per ID kind, in show order.

    Returns:
        show: The show with every ID passing the ID pattern once the step is filled.
    """
    del report
    return show
