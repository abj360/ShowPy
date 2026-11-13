#!/usr/bin/env python3

"""
build_models.py --- data classes for set scripts and their builds

Contains:
    SetScript: holds a set's script source and what it depends on
    BuildResult: holds the outcome of building one set
    ScriptResult: holds the outcome of an analysis script
    BuildContext: gives a set script read-only access to the show and the library
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from stage.cast.models import Cast
from stage.choreo.engine import PathEngine
from stage.choreo.models import Form, Set
from stage.core.ids import SetId
from stage.core.selection import Selection, Selector
from stage.core.types import FrozenModel
from stage.sheet.models import Sheet
from stage.show.models import Show
from stage.time.models import Timeline


class SetScript(FrozenModel):
    """Holds a set's script source.

    Attributes:
        set_id: Set the script builds.
        path: Workspace path of the script.
        source: Python source defining build(ctx).
        hash: Content hash of the source.
        depends_on: Sets whose results the script reads (the previous set by default).
    """

    set_id: SetId
    path: str
    source: str
    hash: str
    depends_on: tuple[SetId, ...] = ()


class BuildResult(FrozenModel):
    """Holds the outcome of building one set.

    Attributes:
        set_id: Set built.
        set: The built set, stamped with the script's source hash.
        forms: Forms the script returned, stamped with the set id; empty for Set results.
        logs: Structured log lines the script emitted.
        warnings: Validation warnings for the built set.
        duration_ms: Wall time of the build.
        stale_dependents: Later sets whose inputs changed because of this build.
    """

    set_id: SetId
    set: Set
    forms: tuple[Form, ...] = ()
    logs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_ms: float
    stale_dependents: tuple[SetId, ...] = ()


class ScriptResult(FrozenModel):
    """Holds the outcome of an analysis script.

    Attributes:
        stdout: Printed output, truncated to a bounded size.
        result: JSON value returned by main(ctx).
        duration_ms: Wall time of the run.
    """

    stdout: str
    # Any: analysis scripts may return any JSON value.
    result: Any = None
    duration_ms: float


@dataclass(frozen=True)
class BuildContext:
    """Gives a set script read-only access to the show and the library.

    Attributes:
        show: The whole show, frozen.
        prev: The previous set, or None for the first set.
        set_id: Set being built.
        counts: Counts of the set being built.
        surface: The design sheet.
        timeline: Show clock.
        cast: Groups and spots.
        select: Resolves selection expressions against the show.
        engine: PathEngine over the show as it was before this build.
        logs: Lines emitted with log().
    """

    show: Show
    prev: Set | None
    set_id: SetId
    counts: float
    surface: Sheet
    timeline: Timeline
    cast: Cast
    select: Callable[[Selector], Selection]
    engine: PathEngine
    logs: list[str] = field(default_factory=list)

    def log(self, msg: str, **fields: object) -> None:
        """Records `msg key=value ...` in the build log."""
        suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        self.logs.append(f"{msg} {suffix}".strip())
