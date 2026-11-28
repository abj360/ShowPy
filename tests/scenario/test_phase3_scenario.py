#!/usr/bin/env python3

"""
test_phase3_scenario.py --- proves the Phase 3 storage exit criteria over 1,000 revisions

Copies the layout-1 repository v0.4.0 wrote and upgrades it with `upgrade_repository`, keeps
saving the upgraded show, then saves its head as a new show (an upgraded show keeps revision IDs
of the older scheme, which an archive refuses) and writes 1,000 revisions to it on the file
backend. The revisions mix one-spot designer saves; three role agents editing one set (drill,
facing and timing) whose edits `merge` combines with no conflict; proposals that are proposed,
previewed and then accepted, rejected or withdrawn; a text form on a user font; and a `revert`.
After every 100 revisions the head is read in under 1 ms (median of warm calls), `fsck` passes,
`events_since` replays the last 100 revisions to the head show and `revalidate` chained from the
last check equals `validate_show`; the log is one parent chain holding exactly one revision per
step. Last, the whole history goes through an archive into a fresh file repository and an
in-memory one. Every library call that takes a model runs inside `unchanged`, so a call that
changes its input fails the run.

Contains:
    Checkpoint: the head revision, show and validation report the last periodic check saw
    ScenarioRun: drives the revisions against one repository and tallies what each step did
    unchanged(): fails when a model serializes differently after a block
    median_head_s(): times warm `head()` calls and returns the median in seconds
    upgraded_repository(): copies and upgrades the v0.4.0 fixture, then saves the legacy show
    test_phase3_scenario_at_1000_revisions(): runs the scenario and checks every 100 revisions
"""

import itertools
import json
import shutil
import statistics
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pydantic import BaseModel

from e8s.choreo.form_place import rebuild
from e8s.choreo.forms import slots, text_form
from e8s.choreo.models import Placement, PlacementPatch
from e8s.core.ids import FormId, SetId
from e8s.core.util import seeded_rng
from e8s.core.validation import ValidationReport
from e8s.geo.fonts import FontRegistry, bundled_fonts, register_font
from e8s.show.archive import export_archive, import_archive
from e8s.show.changes import apply_change
from e8s.show.collab_models import FormOp, PlacementOp
from e8s.show.document_repository import DocumentRepository
from e8s.show.documents import encode_show
from e8s.show.events import apply_event, events_since
from e8s.show.file_repository import FileRepository
from e8s.show.font_documents import load_fonts
from e8s.show.fsck import fsck
from e8s.show.history import revert
from e8s.show.manage import set_counts
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.merge import merge
from e8s.show.models import Show
from e8s.show.proposals import (
    accept,
    get_proposal,
    preview_proposal,
    propose,
    reject,
    withdraw,
)
from e8s.show.revalidate import revalidate
from e8s.show.show_io import load_show, load_show_at, save_show
from e8s.show.upgrade import upgrade_repository
from e8s.show.validate import validate_show
from e8s.show.workspace import open_workspace
from tests.fixtures.fonts import tiny_bytes

pytestmark = [pytest.mark.slow, pytest.mark.timing]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "repos" / "v0.4.0"
LEGACY_SHOW = "legacy-show"
SHOW_ID = "show_1"
REVISIONS = 1_000
CHECK_EVERY = 100
LEGACY_SAVES = 3
# The first save re-encodes the documents v0.4.0 stored in a revision of its own.
NORMALIZING_SAVES = 1
HEAD_BUDGET_S = 1e-3
HEAD_CALLS = 21
SEED = 3
MOVE_M = (0.1, 0.5)
TURN_DEG = 90.0
COUNTS_STEP = 1.0
CYCLE = (
    *("propose", "save", "save", "agents", "save", "accept", "save", "save"),
    *("propose", "save", "save", "reject", "save", "agents", "save"),
    *("propose", "save", "save", "withdraw", "save"),
)
TEXT_CYCLE = 1
REVERT_CYCLE = 25
REVERT_BACK = 3
TEXT_FORM = FormId("tiny")
TEXT_SET = "3"
TEXT_SPOTS = 4
TEXT_HEIGHT_M = 2.0
TEXT_AT = (0.0, 5.0)
FALLBACK_FONT = "simplex"
CLOSED_STATES = {"accept": "accepted", "reject": "rejected", "withdraw": "withdrawn"}
AT = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


@contextmanager
def unchanged(*models: BaseModel) -> Iterator[None]:
    """Fails when any given model serializes differently after the block."""
    before = [m.model_dump_json() for m in models]
    yield
    assert [m.model_dump_json() for m in models] == before


def median_head_s(repo: DocumentRepository, show_id: str) -> float:
    """Times warm `head()` calls after one untimed call and returns the median in seconds."""
    repo.head(show_id)
    times = []
    for _ in range(HEAD_CALLS):
        start = time.perf_counter()
        repo.head(show_id)
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def _free_spots(show: Show, set_id: str, reserved: set[tuple[str, str]]) -> list[str]:
    """Lists the spots of a set an edit may move: in no form, not pinned and not reserved."""
    pinned = {pin.id for pin in show.pins if pin.target == "entity"}
    return [
        p.entity
        for p in show.set(set_id).placements
        if p.form is None and not p.pinned and p.entity not in pinned
        if (set_id, p.entity) not in reserved
    ]


def _moved(show: Show, set_id: str, spot: str, dx: float) -> Show:
    edited = show.set(set_id)
    x = edited.get(spot).x + dx
    with unchanged(show):
        moved = edited.with_placements({spot: PlacementPatch(x=x)})
    return show.replace(sets=tuple(moved if s.id == set_id else s for s in show.sets))


def _turned(show: Show, set_id: str, spot: str) -> Show:
    edited = show.set(set_id)
    facing = (edited.get(spot).facing + TURN_DEG) % 360.0
    with unchanged(show):
        turned = edited.with_placements({spot: PlacementPatch(facing=facing)})
    return show.replace(sets=tuple(turned if s.id == set_id else s for s in show.sets))


def _text_registry() -> tuple[FontRegistry, str]:
    return register_font(bundled_fonts(), tiny_bytes())


def upgraded_repository(root: Path) -> tuple[FileRepository, Show]:
    """Copies and upgrades the v0.4.0 fixture, then saves the upgraded legacy show a few times.

    Returns:
        repo: The upgraded file repository.
        legacy_head: The legacy show at its head after the saves.
    """
    shutil.copytree(FIXTURE / "repo", root / "old")
    report = upgrade_repository(root / "old", root / "new")
    assert [s.show for s in report.shows] == [LEGACY_SHOW]
    repo = FileRepository(root / "new")
    assert fsck(repo.root).ok
    ws = open_workspace(repo, LEGACY_SHOW)
    for n in range(LEGACY_SAVES):
        show = load_show(ws)
        spot = _free_spots(show, TEXT_SET, set())[n]
        save_show(ws, _moved(show, TEXT_SET, spot, MOVE_M[1]), AT + STEP * n)
    expected = json.loads((FIXTURE / "expected.json").read_text())
    log = [r.id for r in repo.revisions(LEGACY_SHOW)]
    assert log[: len(expected["revisions"])] == expected["revisions"]
    assert len(log) == len(expected["revisions"]) + NORMALIZING_SAVES + LEGACY_SAVES
    assert fsck(repo.root).ok
    return repo, load_show(ws)


@dataclass(frozen=True)
class Checkpoint:
    """Holds what the last periodic check saw, for the next check to build on.

    Attributes:
        revision: Head revision at the check.
        show: Head show at the check.
        report: validate_show's report of that show.
    """

    revision: str
    show: Show
    report: ValidationReport


class ScenarioRun:
    """Drives the scenario's revisions against one repository and tallies each step.

    Attributes:
        repo: Repository the show lives in.
        ws: The designer's workspace.
        agent_ws: The workspace agents propose from.
        rng: Seeded generator choosing sets, spots and moves.
        ticks: Revision times handed out so far.
        tally: How many times each step kind ran.
        reserved: (set, spot) pairs an open proposal moves, which other edits leave alone.
        open_proposal: ID of the proposal awaiting its close, or None.
        boundaries: Head revision at the end of each cycle, oldest first.
        heads: Head revision after the fork and after each step, oldest first.
        font_uri: URI of the user font the text form uses, once added.
    """

    def __init__(self, repo: FileRepository) -> None:
        self.repo = repo
        self.ws = open_workspace(repo, SHOW_ID)
        self.agent_ws = open_workspace(repo, SHOW_ID)
        self.rng = seeded_rng(SEED)
        self.ticks = 0
        self.tally: Counter[str] = Counter()
        self.reserved: set[tuple[str, str]] = set()
        self.open_proposal: str | None = None
        self.boundaries: list[str] = []
        self.heads = [repo.head(SHOW_ID)]
        self.font_uri = ""

    def at(self) -> datetime:
        """Returns the next revision time, one minute after the last."""
        self.ticks += 1
        return AT + STEP * self.ticks

    def _pick(self, show: Show, count: int, *, skip_first: bool) -> tuple[str, list[str]]:
        """Picks a set with at least `count` free spots and `count` of those spots."""
        sets = show.sets[1:] if skip_first else show.sets
        eligible = [s.id for s in sets if len(_free_spots(show, s.id, self.reserved)) >= count]
        set_id = eligible[int(self.rng.integers(len(eligible)))]
        free = _free_spots(show, set_id, self.reserved)
        picked = self.rng.choice(len(free), size=count, replace=False)
        return set_id, [free[int(i)] for i in picked]

    def _dx(self) -> float:
        sign = 1.0 if self.rng.integers(2) else -1.0
        return sign * float(self.rng.uniform(*MOVE_M))

    def _designer_head(self) -> Show:
        self.ws.refresh()
        return load_show(self.ws)

    def save(self) -> None:
        """Saves one spot moved in one set, as a designer edit."""
        show = self._designer_head()
        set_id, (spot,) = self._pick(show, 1, skip_first=False)
        edited = _moved(show, set_id, spot, self._dx())
        with unchanged(edited):
            save_show(self.ws, edited, self.at())

    def agents(self) -> None:
        """Merges three role agents' edits of one set (drill, facing, timing) and saves them."""
        base = self._designer_head()
        set_id, (moved_spot, turned_spot) = self._pick(base, 2, skip_first=True)
        counts = base.set(set_id).counts
        new_counts = counts + COUNTS_STEP if self.rng.integers(2) else counts - COUNTS_STEP
        drill = _moved(base, set_id, moved_spot, self._dx())
        facing = _turned(base, set_id, turned_spot)
        with unchanged(base):
            timing = set_counts(base, set_id, max(new_counts, COUNTS_STEP))
        with unchanged(base, drill, facing, timing):
            first = merge(base, drill, facing)
            assert first.conflicts == ()
            assert first.show is not None
            merged = merge(base, first.show, timing)
        assert merged.conflicts == ()
        assert merged.show is not None
        result = merged.show.set(set_id)
        assert result.get(moved_spot) == drill.set(set_id).get(moved_spot)
        assert result.get(turned_spot) == facing.set(set_id).get(turned_spot)
        assert result.counts == timing.set(set_id).counts
        with unchanged(merged.show):
            save_show(self.ws, merged.show, self.at())

    def propose(self) -> None:
        """Proposes one spot's move from the agent workspace and previews it."""
        self.agent_ws.refresh()
        show = load_show(self.agent_ws)
        set_id, (spot,) = self._pick(show, 1, skip_first=False)
        old = show.set(set_id).get(spot)
        placement = Placement.from_json(old.to_json() | {"x": old.x + self._dx()})
        proposal_id = f"p{self.tally['propose']}"
        op = PlacementOp(set_id=SetId(set_id), placement=placement)
        propose(self.agent_ws, [op], "move one spot", proposal_id, self.at())
        preview = preview_proposal(self.ws, proposal_id)
        assert preview.conflicts == ()
        assert preview.blocked == ()
        assert preview.placements == {set_id: (placement,)}
        self.reserved = {(set_id, spot)}
        self.open_proposal = proposal_id

    def close(self, kind: str) -> None:
        """Accepts, rejects or withdraws the open proposal."""
        proposal_id = self.open_proposal
        assert proposal_id is not None
        if kind == "accept":
            ((set_id, spot),) = self.reserved
            placement = get_proposal(self.ws, proposal_id).change[0]
            accept(self.ws, proposal_id, self.at())
            assert isinstance(placement, PlacementOp)
            assert load_show(self.ws).set(set_id).get(spot) == placement.placement
        elif kind == "reject":
            reject(self.ws, proposal_id, "not now", self.at())
        else:
            withdraw(self.agent_ws, proposal_id, self.at())
        self.ws.refresh()
        assert get_proposal(self.ws, proposal_id).state == CLOSED_STATES[kind]
        self.reserved, self.open_proposal = set(), None

    def add_text_form(self) -> None:
        """Adds a text form on a user font to a hold set and lays its members on the slots."""
        registry, self.font_uri = _text_registry()
        show = self._designer_head()
        members = _free_spots(show, TEXT_SET, set())[:TEXT_SPOTS]
        fonts = [self.font_uri, registry.resolve(FALLBACK_FONT)]
        form = text_form(
            TEXT_FORM,
            members,
            "I",
            fonts=fonts,
            height=TEXT_HEIGHT_M,
            at=TEXT_AT,
            n=TEXT_SPOTS,
            set_id=TEXT_SET,
            font_registry=registry,
        )
        with unchanged(show, form):
            with_form = apply_change(show, [FormOp(form=form)])
        assert TEXT_FORM in with_form.set(TEXT_SET).forms
        with unchanged(with_form):
            built = rebuild(with_form, TEXT_FORM, font_registry=registry)
        with unchanged(built):
            save_show(self.ws, built, self.at(), font_registry=registry)
        stored = load_show(self.ws)
        laid = [stored.set(TEXT_SET).get(spot) for spot in members]
        expected = slots(stored.forms[TEXT_FORM], font_registry=load_fonts(self.ws))
        assert np.allclose([(p.x, p.y) for p in laid], expected)

    def revert_back(self) -> None:
        """Reverts to the head of an earlier cycle and checks the show comes back."""
        target = self.boundaries[-REVERT_BACK]
        revert(self.ws, target, self.at())
        restored, earlier = load_show(self.ws), load_show_at(self.ws, target)
        with unchanged(restored, earlier):
            assert encode_show(restored) == encode_show(earlier)
        assert self.ws.tree_at(self.ws.revision) == self.ws.tree_at(target)

    def step(self, kind: str) -> None:
        """Runs one step and checks it wrote exactly one revision on top of the head."""
        head = self.repo.head(SHOW_ID)
        if kind in ("accept", "reject", "withdraw"):
            self.close(kind)
        else:
            {
                "save": self.save,
                "agents": self.agents,
                "propose": self.propose,
                "text": self.add_text_form,
                "revert": self.revert_back,
            }[kind]()
        self.tally[kind] += 1
        self.heads.append(self.repo.head(SHOW_ID))
        assert self.heads[-1] != head, kind


def _checked(run: ScenarioRun, last: Checkpoint) -> Checkpoint:
    """Runs the periodic checks against a fresh reader and returns the new checkpoint."""
    for show_id in (SHOW_ID, LEGACY_SHOW):
        assert median_head_s(run.repo, show_id) < HEAD_BUDGET_S, show_id
    report = fsck(run.repo.root)
    assert report.ok
    assert report.findings == ()
    log = run.repo.revisions(SHOW_ID)
    assert [r.id for r in log] == run.heads
    assert all(r.parent == p.id for p, r in itertools.pairwise(log))
    reader = open_workspace(run.repo, SHOW_ID)
    head = load_show(reader)
    assert head.revision == run.repo.head(SHOW_ID)
    replayed = last.show
    for event in events_since(reader, last.revision):
        with unchanged(replayed, event):
            replayed = apply_event(replayed, event)
    with unchanged(replayed, head):
        assert encode_show(replayed) == encode_show(head)
        chained = revalidate(last.report, last.show, head)
        assert chained == validate_show(head)
    return Checkpoint(revision=head.revision, show=head, report=chained)


def _schedule() -> Iterator[str]:
    """Yields the step kinds in order: whole cycles, with the text form and revert between."""
    cycle = 0
    while True:
        if cycle == TEXT_CYCLE:
            yield "text"
        if cycle == REVERT_CYCLE:
            yield "revert"
        yield from CYCLE
        yield "boundary"
        cycle += 1


def _round_trip(repo: FileRepository, root: Path, font_uri: str) -> None:
    """Sends the whole history through an archive into a fresh file and in-memory repository."""
    archive = export_archive(repo, SHOW_ID)
    source = open_workspace(repo, SHOW_ID)
    head = load_show(source)
    for target in (FileRepository(root), InMemoryRepository()):
        assert import_archive(target, archive) == SHOW_ID
        assert target.revisions(SHOW_ID) == repo.revisions(SHOW_ID)
        imported = open_workspace(target, SHOW_ID)
        assert imported.tree_at(imported.revision) == source.tree_at(source.revision)
        with unchanged(head):
            assert encode_show(load_show(imported)) == encode_show(head)
        assert load_fonts(imported).source(font_uri) == tiny_bytes()
        assert export_archive(target, SHOW_ID) == archive
    assert fsck(root).ok


def test_phase3_scenario_at_1000_revisions(tmp_path: Path) -> None:
    """Writes 1,000 revisions on an upgraded repository and checks it every 100 revisions."""
    repo, legacy_head = upgraded_repository(tmp_path)
    with unchanged(legacy_head):
        repo.put_show(SHOW_ID, legacy_head.replace(id=SHOW_ID), expected_revision="", at=AT)
    run = ScenarioRun(repo)
    first = load_show(run.ws)
    last = Checkpoint(revision=first.revision, show=first, report=validate_show(first))
    written = 1
    for kind in _schedule():
        if kind == "boundary":
            run.boundaries.append(run.ws.revision)
            continue
        run.step(kind)
        written += 1
        if written % CHECK_EVERY == 0:
            last = _checked(run, last)
        if written == REVISIONS:
            break
    assert len(repo.revisions(SHOW_ID)) == REVISIONS
    assert all(run.tally[kind] for kind in (*CYCLE, "text", "revert"))
    _round_trip(repo, tmp_path / "imported", run.font_uri)
