"""
dependencies.py --- moves a show's pinned sheet references to newer versions

Contains:
    upgrade_dependency(): pins a newer version of a sheet reference
"""

from e8s.core.errors import (
    UnknownRefError,
    VersionError,
)
from e8s.core.ids import (
    format_ref,
    parse_ref,
)
from e8s.core.util import close_matches
from e8s.sheet.models import Sheet
from e8s.show.models import Show


def upgrade_dependency(show: Show, ref: str, version: str, sheet: Sheet | None = None) -> Show:
    """Pins a newer version of a sheet reference, swapping in that sheet when it is given.

    Stored positions never change. Without `sheet`, the show keeps its current sheet object
    until one is loaded, and dependency validation reports the mismatch.

    Args:
        show: Show to edit.
        ref: Sheet reference, with or without a version.
        version: Version to pin.
        sheet: The sheet at that version, to adopt it now.

    >>> from e8s.examples import two_set_show
    >>> dict(upgrade_dependency(two_set_show(), "sheet:test-field", "2").dependencies)
    {'sheet:test-field': '2'}
    """
    kind, ref_id, _ = parse_ref(ref)
    key = format_ref(kind, ref_id)
    if key not in show.dependencies:
        close = close_matches(key, show.dependencies)
        raise UnknownRefError(
            f"show does not depend on {key}",
            hint=f"did you mean {', '.join(close)}?" if close else "check show.dependencies",
            context={"ref": key, "known": sorted(show.dependencies)},
        )
    update: dict[str, object] = {"dependencies": {**show.dependencies, key: version}}
    if sheet is not None:
        if sheet.ref != format_ref(kind, ref_id, version):
            raise VersionError(
                f"sheet {sheet.ref} is not {format_ref(kind, ref_id, version)}",
                hint="pass the sheet saved at the version being pinned",
                context={"sheet": sheet.ref, "ref": key, "version": version},
            )
        update["sheet"] = sheet
    return show.replace(**update)
