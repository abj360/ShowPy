"""
__init__.py --- the sub-steps of each stored-format migration, in the fixed order they run

The order lives here, not in the step modules, so no lane reorders another's step. JSON
sub-steps run on the parsed show documents keyed by stored path (show, sheet, notes, timeline,
set, forms and paths documents); model sub-steps run after, on the show validated with any
non-empty v1 ID accepted. A step never edits the output format of an earlier version's steps.
A step returns new objects for whatever it changes and never mutates a value it was given,
nested values included, so the documents a caller passed in stay as they were.

Contains:
    JsonStep: a sub-step over parsed documents: documents and report in, documents out
    ModelStep: a sub-step over the validated show: show and report in, show out
    V1_TO_V2_JSON_STEPS: forms, paths, selectors, timeline, facings, sheet, cast and events
    V1_TO_V2_MODEL_STEPS: ids, which runs last
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from showpy.core.json_types import JsonValue
from showpy.show.migrations.v1_to_v2_cast import migrate_cast
from showpy.show.migrations.v1_to_v2_events import migrate_events
from showpy.show.migrations.v1_to_v2_facings import migrate_facings
from showpy.show.migrations.v1_to_v2_forms import migrate_forms
from showpy.show.migrations.v1_to_v2_ids import rename_ids
from showpy.show.migrations.v1_to_v2_paths import migrate_paths
from showpy.show.migrations.v1_to_v2_selectors import migrate_selectors
from showpy.show.migrations.v1_to_v2_sheet import migrate_sheet
from showpy.show.migrations.v1_to_v2_timeline import migrate_timeline
from showpy.show.models import Show

if TYPE_CHECKING:
    from showpy.show.migrate import ReportBuilder

type JsonStep = Callable[[dict[str, JsonValue], ReportBuilder], dict[str, JsonValue]]
type ModelStep = Callable[[Show, ReportBuilder], Show]

V1_TO_V2_JSON_STEPS: Final[tuple[JsonStep, ...]] = (
    migrate_forms,
    migrate_paths,
    migrate_selectors,
    migrate_timeline,
    migrate_facings,
    migrate_sheet,
    migrate_cast,
    migrate_events,
)
V1_TO_V2_MODEL_STEPS: Final[tuple[ModelStep, ...]] = (rename_ids,)
