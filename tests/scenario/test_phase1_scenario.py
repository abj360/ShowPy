#!/usr/bin/env python3

"""
test_phase1_scenario.py --- proves the Phase 1 exit criteria on a 300-spot, 60-set show

Builds seeded_show(11, 300, 60), pins, inserts a sub-set, renumbers, regroups, relabels, saves
through file-backed workspaces, edits through a second workspace, publishes and claims, then
checks the exit invariants: continuity at set boundaries, the collision segment bound, JSON and
document round trips, events replaying to head, revert, determinism and no input mutation. The
whole scenario runs twice for the determinism check.
Every library call that takes a model is wrapped so a changed input fails the run. The path
checks sample deterministically: every SEGMENT_STRIDE-th transition, plus the two around the
inserted sub-set (indices 4 and 5), gets the nine-probe segment check; every set boundary of every
spot is checked for continuity.

Contains:
    Scenario: the shows, revisions and publication one scenario run produces
    unchanged(): fails when a model serializes differently after a block
    run_scenario(): runs every phase of the scenario against a fresh repository
    test_scenario_edits_hit_every_phase(): checks each phase left its mark
    test_final_show_validates(): checks validate_show and the stored JSON schema
    test_positions_are_continuous_at_every_set_boundary(): checks transition ends meet placements
    test_segments_follow_the_engine(): checks segments against position() at nine probes
    test_json_and_document_round_trips_are_equal(): checks export_json and encode_show round trips
    test_events_since_replays_to_head(): checks replayed canvas events reach the head show
    test_revert_restores_an_earlier_revision(): checks revert brings back an earlier show
    test_two_runs_are_byte_identical(): checks encode_show and content_hash across two runs
    test_locked_delete_is_refused_without_force(): checks a pinned spot resists deletion
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import jsonschema
import numpy as np
import pytest
from pydantic import BaseModel

from showpy.cast.models import LabelScheme
from showpy.cast.spots import delete_spots, regroup, relabel
from showpy.choreo.engine import DEFAULT_SEGMENT_TOL, PathEngine
from showpy.choreo.engine_models import MotionSegment
from showpy.choreo.models import Set
from showpy.core.errors import LockedError
from showpy.core.ids import SetId
from showpy.core.resources import schema_path
from showpy.core.util import approx_equal, content_hash
from showpy.examples import EXAMPLE_TIME, performer
from showpy.export.data import export_json, import_json
from showpy.export.models import Publication
from showpy.export.publish import claim_spot, join_link, publish, unclaimed, verify_join_token
from showpy.show.collab import apply_event, events_since
from showpy.show.documents import decode_show, encode_show
from showpy.show.file_repository import FileRepository
from showpy.show.history import revert
from showpy.show.manage import insert_subset, renumber, set_counts
from showpy.show.models import CanvasEvent, Show
from showpy.show.pinning import pin
from showpy.show.storage import Workspace, load_show, open_workspace, save_show
from showpy.show.validate import validate_show
from tests.fixtures.generators import seeded_show

pytestmark = pytest.mark.slow

SEED, SPOTS, SETS = 11, 300, 60
PINNED_SPOTS = 20
PINNED_SET = SetId("12")
SUBSET_AFTER = "5"
REGROUPED_SPOTS = 6
SECOND_WORKSPACE_EDITS = 10
RETIMED_FIRST_SET = 20
LABEL_PAD = 3
COUNT_BUMP = 0.5
CLAIMS = 30
JOIN_TOKEN = "scenario-join-token-0123456789"
BASE_URL = "https://shows.example"
PUBLICATION_ID = "scenario"
POSITION_TOL = 1e-6
SEGMENT_PROBES = 9
SEGMENT_STRIDE = 6
SUBSET_TRANSITIONS = (4, 5)
STEP = timedelta(minutes=1)


@dataclass(frozen=True)
class Scenario:
    """Holds what one scenario run produced.

    Attributes:
        ws: Workspace that saved the first revision and later reverted.
        base_revision: First saved revision, before the second workspace's edits.
        base: Show saved at `base_revision`.
        head: Show at the head before the revert.
        reverted: Show after reverting to `base_revision`.
        publication: Publication of `head` with its claims.
        events: Canvas events of the second workspace's edits, taken before the revert.
        mapping: Old to new set IDs from the renumber.
        pinned: IDs of the pinned spots, as relabeled.
        initial: The generated show before any edit.
    """

    ws: Workspace
    base_revision: str
    base: Show
    head: Show
    reverted: Show
    publication: Publication
    events: list[CanvasEvent]
    mapping: dict[SetId, SetId]
    pinned: tuple[str, ...]
    initial: Show


@contextmanager
def unchanged(*models: BaseModel) -> Iterator[None]:
    """Fails when any given model serializes differently after the block."""
    before = [m.model_dump_json() for m in models]
    yield
    assert [m.model_dump_json() for m in models] == before


def _pinned_show(show: Show) -> tuple[Show, tuple[str, ...]]:
    ids = tuple(s.id for s in show.cast.spots[:PINNED_SPOTS])
    with unchanged(show):
        edited = pin(show, "set", PINNED_SET, "scenario")
    for spot in ids:
        with unchanged(edited):
            edited = pin(edited, "entity", spot, "scenario")
    return edited, ids


def _restructured(show: Show) -> tuple[Show, dict[SetId, SetId]]:
    follower = show.set(show.sets[show.set_index(SUBSET_AFTER) + 1].id)
    with unchanged(show):
        edited, _ = insert_subset(show, SUBSET_AFTER, follower.counts / 2)
    with unchanged(edited):
        return renumber(edited)


def _regrouped(show: Show, pinned: tuple[str, ...]) -> Show:
    free = [s.id for s in show.cast.spots if s.group == "brass" and s.id not in pinned]
    with unchanged(show):
        edited, _ = regroup(show, free[-REGROUPED_SPOTS:], "woodwinds")
    with unchanged(edited):
        edited, _ = relabel(edited, LabelScheme(pad=LABEL_PAD))
    return edited


def _retimed(ws: Workspace, number: int, at: datetime) -> None:
    head = load_show(ws)
    target = str(RETIMED_FIRST_SET + number)
    with unchanged(head):
        edited = set_counts(head, target, head.set(target).counts + COUNT_BUMP)
    with unchanged(edited):
        save_show(ws, edited, at)


def _published(show: Show, at: datetime) -> Publication:
    with unchanged(show):
        pub = publish(show, PUBLICATION_ID, BASE_URL, at, join_token=JOIN_TOKEN)
    with unchanged(pub):
        assert verify_join_token(pub, JOIN_TOKEN)
        assert join_link(pub, JOIN_TOKEN).startswith(pub.link)
    with unchanged(pub, show):
        open_spots = unclaimed(pub, show)
    for number, spot in enumerate(open_spots[:CLAIMS]):
        with unchanged(pub, show):
            pub, _ = claim_spot(pub, show, performer(f"P{number:02d}"), spot, at)
    return pub


def run_scenario(root: Path) -> Scenario:
    """Runs every phase of the scenario against a file repository under `root`."""
    initial = seeded_show(SEED, SPOTS, SETS)
    pinned_show, pinned = _pinned_show(initial)
    restructured, mapping = _restructured(pinned_show)
    final_edit = _regrouped(restructured, pinned)
    repo = FileRepository(root)
    ws = open_workspace(repo, final_edit.id)
    with unchanged(final_edit):
        first = save_show(ws, final_edit, EXAMPLE_TIME)
    base = load_show(ws)
    second = open_workspace(repo, final_edit.id)
    for number in range(SECOND_WORKSPACE_EDITS):
        _retimed(second, number, EXAMPLE_TIME + STEP * (number + 1))
    head = load_show(second)
    events = events_since(second, first.id)
    pub = _published(head, EXAMPLE_TIME + STEP * 20)
    revert(ws, first.id, EXAMPLE_TIME + STEP * 30)
    return Scenario(
        ws=ws,
        base_revision=first.id,
        base=base,
        head=head,
        reverted=load_show(ws),
        publication=pub,
        events=events,
        mapping=mapping,
        pinned=tuple(p.id for p in final_edit.pins if p.target == "entity"),
        initial=initial,
    )


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """Runs the scenario once for the module's tests."""
    return run_scenario(tmp_path_factory.mktemp("scenario-one"))


def _xy(set_: Set, spot: str) -> tuple[float, float]:
    placement = set_.get(spot)
    return placement.x, placement.y


def _transition_ends(show: Show) -> Iterator[tuple[str, int, np.ndarray, np.ndarray]]:
    engine = PathEngine(show)
    for index, tid in enumerate(show.transition_ids()):
        counts = show.set(tid[1]).counts
        for spot in show.cast.ids():
            yield (
                spot,
                index,
                np.asarray(engine.position(spot, 0.0, local=tid)),
                np.asarray(engine.position(spot, counts, local=tid)),
            )


def test_scenario_edits_hit_every_phase(scenario: Scenario) -> None:
    assert len(scenario.initial.cast.spots) == SPOTS
    assert len(scenario.initial.sets) == SETS
    assert len(scenario.head.sets) == SETS + 1
    assert [s.id for s in scenario.head.sets] == [str(n) for n in range(1, SETS + 2)]
    assert scenario.mapping[PINNED_SET] == str(int(PINNED_SET) + 1)
    assert [p.id for p in scenario.head.pins if p.target == "set"] == [scenario.mapping[PINNED_SET]]
    assert len(scenario.pinned) == PINNED_SPOTS
    assert len(scenario.publication.claims) == CLAIMS
    assert len(unclaimed(scenario.publication, scenario.head)) == SPOTS - CLAIMS
    assert len(scenario.ws.repo.revisions(scenario.head.id)) == SECOND_WORKSPACE_EDITS + 2
    assert scenario.head.cast.ids()[0] == "B001"


def test_locked_delete_is_refused_without_force(scenario: Scenario) -> None:
    with unchanged(scenario.head):
        with pytest.raises(LockedError):
            delete_spots(scenario.head, [scenario.pinned[0]])
        with pytest.raises(LockedError):
            set_counts(scenario.head, scenario.mapping[PINNED_SET], 9.0)
        forced = delete_spots(scenario.head, [scenario.pinned[0]], force=True)
    assert scenario.pinned[0] not in forced.cast.ids()


def test_final_show_validates(scenario: Scenario) -> None:
    with unchanged(scenario.head):
        assert validate_show(scenario.head).ok
    schema = json.loads(schema_path("showpy.show.v1.json").read_text())
    jsonschema.validate(scenario.head.model_dump(mode="json"), schema)


def test_positions_are_continuous_at_every_set_boundary(scenario: Scenario) -> None:
    head = scenario.head
    ends: dict[tuple[str, int], np.ndarray] = {}
    with unchanged(head):
        for spot, index, start, end in _transition_ends(head):
            before, after = head.sets[index], head.sets[index + 1]
            assert approx_equal(start, _xy(before, spot), POSITION_TOL), (spot, index)
            assert approx_equal(end, _xy(after, spot), POSITION_TOL), (spot, index)
            if index:
                assert approx_equal(start, ends[spot, index - 1], POSITION_TOL), (spot, index)
            ends[spot, index] = end
    assert len(ends) == SPOTS * SETS


def _probe_gap(engine: PathEngine, piece: MotionSegment) -> float:
    worst = 0.0
    for fraction in np.linspace(0.0, 1.0, SEGMENT_PROBES):
        t = piece.t0 + (piece.t1 - piece.t0) * fraction
        line = np.asarray(piece.p0) + (np.asarray(piece.p1) - np.asarray(piece.p0)) * fraction
        worst = max(
            worst, float(np.linalg.norm(engine.position(piece.entity, t, piece.tid) - line))
        )
    return worst


def test_segments_follow_the_engine(scenario: Scenario) -> None:
    head = scenario.head
    engine = PathEngine(head)
    tids = head.transition_ids()
    sampled = [t for i, t in enumerate(tids) if i % SEGMENT_STRIDE == 0 or i in SUBSET_TRANSITIONS]
    with unchanged(head):
        pieces = [piece for tid in sampled for piece in engine.segments(tid)]
        gaps = [_probe_gap(engine, piece) for piece in pieces]
    assert len(pieces) >= len(sampled) * SPOTS
    assert max(gaps) <= DEFAULT_SEGMENT_TOL


def test_json_and_document_round_trips_are_equal(scenario: Scenario) -> None:
    head = scenario.head
    with unchanged(head):
        assert import_json(export_json(head)) == head
        documents = encode_show(head)
        assert decode_show(documents, head.revision) == head
        assert encode_show(decode_show(documents, head.revision)) == documents


def _edit_revisions(ws: Workspace, scenario: Scenario) -> list[str]:
    log = [r.id for r in ws.repo.revisions(scenario.head.id)]
    return log[1 : 1 + SECOND_WORKSPACE_EDITS]


def test_events_since_replays_to_head(scenario: Scenario) -> None:
    ws = scenario.ws
    events = scenario.events
    assert {e.revision for e in events if e.revision} >= set(_edit_revisions(ws, scenario))
    replayed = scenario.base
    for event in events:
        with unchanged(replayed, event):
            replayed = apply_event(replayed, event)
    assert encode_show(replayed) == encode_show(scenario.head)


def test_revert_restores_an_earlier_revision(scenario: Scenario) -> None:
    assert scenario.reverted.revision != scenario.base_revision
    assert encode_show(scenario.reverted) == encode_show(scenario.base)
    assert scenario.reverted != scenario.head


def test_two_runs_are_byte_identical(scenario: Scenario, tmp_path: Path) -> None:
    again = run_scenario(tmp_path)
    assert encode_show(again.head) == encode_show(scenario.head)
    assert content_hash(again.head) == content_hash(scenario.head)
    assert again.head.revision == scenario.head.revision
    assert again.publication == scenario.publication
    assert encode_show(again.base) == encode_show(scenario.base)
    assert encode_show(again.reverted) == encode_show(scenario.reverted)
