#!/usr/bin/env python3

"""
build.py --- builds sets from their workspace scripts and runs analysis scripts

Scripts are the source and placements are the result: each set's `/sets/<id>/set.py`
defines build(ctx), which runs in the sandbox (show/sandbox.py). A set is stale when its
script-plus-input hash differs from the set's source_hash, or when an earlier set is stale.

Contains:
    SCRIPT_TEMPLATES: holds the starting scripts new_set_script writes
    build_set(): runs a set's script, validates the result and stores it
    build_all(): rebuilds every scripted set in show order, from a set or the first stale one
    stale(): lists sets whose script or inputs changed
    new_set_script(): writes a starting script for a set
    run_script(): runs an analysis script's main(ctx) from the workspace
"""

from datetime import datetime
from typing import Literal

from stage.choreo.models import Form
from stage.core.errors import SchemaError, ValidationError
from stage.core.ids import SetId
from stage.show.build_models import BuildResult, ScriptResult
from stage.show.documents import SCRIPT_NAME, script_doc
from stage.show.models import Show
from stage.show.sandbox import (
    DEFAULT_TIMEOUT_S,
    run_analysis_script,
    run_set_script,
    script_hash,
)
from stage.show.storage import Workspace, load_show, save_show
from stage.show.validate import validate_set

type Template = Literal["copy_prev", "hold", "from_forms", "blank"]

SCRIPT_TEMPLATES: dict[str, str] = {
    "copy_prev": '''def build(ctx):
    """Copies the previous set, forms and all."""
    return ctx.prev.model_copy(update={"id": ctx.set_id, "counts": ctx.counts})
''',
    "hold": '''def build(ctx):
    """Holds every spot where it was; forms are released."""
    released = {p.entity: {"form": None, "order": None} for p in ctx.prev.placements}
    still = ctx.prev.with_placements(released)
    return still.model_copy(update={"id": ctx.set_id, "counts": ctx.counts, "forms": ()})
''',
    "from_forms": '''from stage.choreo.forms import line_form


def build(ctx):
    """Places every spot on one line form 2 m upstage of the previous set's center."""
    ids = [p.entity for p in ctx.prev.placements]
    pts = ctx.prev.positions(ids)
    y = float(pts[:, 1].mean()) + 2.0
    left, right = (float(pts[:, 0].min()), y), (float(pts[:, 0].max()), y)
    return [line_form("form-" + ctx.set_id, ids, left, right)]
''',
    "blank": '''from stage.choreo.models import Set


def build(ctx):
    """Starts from the previous positions; replace with forms, e.g. form_place.place(...)."""
    return Set(id=ctx.set_id, counts=ctx.counts, placements=ctx.prev.placements)
''',
}


def _script(ws: Workspace, set_id: SetId) -> str | None:
    if SCRIPT_NAME not in ws.ls(f"/sets/{set_id}"):
        return None
    source = ws.read(script_doc(set_id))
    return source.decode() if isinstance(source, bytes) else source


def build_set(
    ws: Workspace, set_id: SetId, at: datetime, timeout_s: float = DEFAULT_TIMEOUT_S
) -> BuildResult:
    """Runs a set's script in the sandbox, validates the result and stores it.

    Raises ValidationError when the built set has structural errors; warnings are returned.
    """
    show = load_show(ws)
    source = _script(ws, set_id)
    if source is None:
        raise SchemaError(
            f"set {set_id} has no script", hint=f"write {script_doc(set_id)} or use new_set_script"
        )
    result = run_set_script(show, set_id, source, timeout_s)
    show = _with_forms(show, set_id, result.forms)
    report = validate_set(show, result.set)
    if not report.ok:
        raise ValidationError(
            f"built set {set_id} is invalid:\n{report}",
            hint="fix the script so every spot is placed once",
        )
    sets = tuple(result.set if s.id == set_id else s for s in show.sets)
    saved = show.model_copy(update={"sets": sets})
    save_show(ws, saved, at, message=f"build set {set_id}")
    after = show.set_ids()[show.set_index(set_id) + 1 :]
    dependents = tuple(s for s in _stale_ids(ws, saved) if s in after)
    warnings = tuple(str(issue) for issue in report.issues)
    return result.model_copy(update={"warnings": warnings, "stale_dependents": dependents})


def _with_forms(show: Show, set_id: SetId, forms: tuple[Form, ...]) -> Show:
    if not forms:
        return show
    kept = {k: f for k, f in show.forms.items() if f.set != set_id}
    return show.model_copy(update={"forms": {**kept, **{f.id: f for f in forms}}})


def _stale_ids(ws: Workspace, show: Show) -> list[SetId]:
    out: list[SetId] = []
    upstream_changed = False
    for s in show.sets:
        source = _script(ws, s.id)
        if source is None:
            continue
        if upstream_changed or script_hash(show, s.id, source) != s.source_hash:
            out.append(s.id)
            upstream_changed = True
    return out


def stale(ws: Workspace) -> list[SetId]:
    """Lists scripted sets whose script changed or that follow a stale set, in show order."""
    return _stale_ids(ws, load_show(ws))


def build_all(ws: Workspace, at: datetime, from_set: SetId | None = None) -> list[BuildResult]:
    """Rebuilds scripted sets in show order, from `from_set` or from the first stale set."""
    show = load_show(ws)
    pending = _stale_ids(ws, show)
    start = from_set or (pending[0] if pending else None)
    if start is None:
        return []
    ids = show.set_ids()[show.set_index(start) :]
    return [build_set(ws, set_id, at) for set_id in ids if _script(ws, set_id) is not None]


def new_set_script(
    ws: Workspace, set_id: SetId, at: datetime, template: Template = "copy_prev"
) -> str:
    """Writes a starting script for a set and returns its path."""
    if template not in SCRIPT_TEMPLATES:
        raise SchemaError(
            f"unknown template {template!r}", hint=f"use one of {sorted(SCRIPT_TEMPLATES)}"
        )
    load_show(ws).set_index(set_id)
    path = script_doc(set_id)
    ws.write(
        path, SCRIPT_TEMPLATES[template], at=at, message=f"new {template} script for set {set_id}"
    )
    return path


def run_script(ws: Workspace, path: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> ScriptResult:
    """Runs an analysis script's main(ctx) against the show, in the sandbox."""
    source = ws.read(path)
    text = source.decode() if isinstance(source, bytes) else source
    return run_analysis_script(load_show(ws), text, timeout_s)
