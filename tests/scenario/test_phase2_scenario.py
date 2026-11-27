#!/usr/bin/env python3

"""
test_phase2_scenario.py --- proves the Phase 2 exit criteria on the 300-spot, 60-set v0.3.0 show

Loads the stored documents v0.3.0 wrote for the seeded 300 x 60 benchmark show, adds what the
seeded show lacks (a spot ID, named selections and notes that the migration must rename), commits
them as a v1 tree to a `FileRepository`, migrates through the first save, saves again, inserts a
sub-set, renumbers and relabels, edits through a second workspace, replays `events_since` and
reverts. Then checks the exit invariants: the report lists every rename, the second save writes
nothing, selections pick the same spots through the relabel, notes land in the notes document
unchanged, the stored format is `e8.show`, positions are continuous at every set boundary,
segments follow the engine, round trips are equal, one revision's show never changes another's
and two runs are byte-identical. Every library call that takes a model runs inside `unchanged`,
so a call that changes its input fails the run. The module fixture runs the scenario twice, so
the determinism check costs no run of its own.

Contains:
    Scenario: the documents, shows and revisions one scenario run produces
    unchanged(): fails when a model serializes differently after a block
    v1_documents(): returns the benchmark show's v1 documents with renames and notes added
    run_scenario(): runs every phase of the scenario against a fresh file repository
    scenario(): runs the scenario twice and keeps the second run for the determinism check
    test_scenario_edits_hit_every_phase(): checks each phase left its mark
    test_report_lists_every_rename(): checks the report against the IDs before and after
    test_second_save_writes_nothing(): checks a save of the loaded migrated show adds no revision
    test_selections_select_the_same_spots(): checks named selections and selectors after edits
    test_notes_land_in_the_notes_document(): checks the stored notes document and show document
    test_format_is_e8_show(): checks the stored show document's format header
    test_positions_are_continuous_at_every_set_boundary(): checks transition ends meet placements
    test_segments_follow_the_engine(): checks segments against position() at nine probes
    test_json_and_document_round_trips_are_equal(): checks export_json and encode_show round trips
    test_events_since_replays_to_head(): checks replayed canvas events reach the head show
    test_revert_restores_an_earlier_revision(): checks revert brings back an earlier show
    test_one_revision_never_changes_another(): checks every revision's show after all reads
    test_two_runs_are_byte_identical(): checks encode_show and content_hash across two runs
"""

import dataclasses
import gzip
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import BaseModel

from e8s.cast.models import LabelScheme
from e8s.cast.select import select
from e8s.cast.spots import relabel
from e8s.choreo.engine import DEFAULT_SEGMENT_TOL, PathEngine
from e8s.choreo.engine_models import MotionSegment
from e8s.choreo.models import Set
from e8s.core.ids import SetId, SpotId
from e8s.core.util import approx_equal, content_hash
from e8s.show import document_repository
from e8s.show.collab_models import CanvasEvent
from e8s.show.documents import (
    LEGACY_NOTES_DOC,
    NOTES_DOC,
    SHOW_DOC,
    decode_show,
    dump_json,
    encode_show,
    migrate_documents,
)
from e8s.show.events import apply_event, events_since
from e8s.show.file_repository import FileRepository
from e8s.show.history import revert
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.interchange import export_json, import_json
from e8s.show.manage import insert_subset, set_counts
from e8s.show.migrate import MigrationReport
from e8s.show.models import Show
from e8s.show.renumber import renumber
from e8s.show.show_io import load_show, save_show
from e8s.show.storage import ROOT_REVISION
from e8s.show.workspace import Workspace, open_workspace

pytestmark = pytest.mark.slow

FIXTURE = Path(__file__).resolve().parents[1] / "bench" / "fixtures" / "v0.3.0-300x60.json.gz"
SPOTS, SETS = 300, 60
FORMAT = "e8.show"
NOTES_FORMAT = "e8.notes"
NOTES_VERSION = 1
RESPELLED_SPOT = ("B1", "brass-1")
V1_SELECTIONS: dict[str, list[str]] = {
    "front-line": ["brass-1", "B2", "B3", "G1", "G2"],
    "pit": ["P1", "P2", "P3"],
    "Pit": ["W1", "W2"],
}
WRITTEN = "2026-11-12T10:00:00+00:00"
V1_NOTES: list[dict[str, Any]] = [
    {"id": "n-1", "text": "open wide", "anchor": {"kind": "spots", "spots": ["brass-1", "B2"]}},
    {"id": "n2", "text": "hold the arc", "anchor": {"kind": "set", "set_id": "1"}},
    {"id": "N3", "text": "mark", "anchor": {"kind": "point", "point": [1.0, 2.0]}},
]
NOTE_AUTHOR = "pat"
LABEL_FREE_SELECTORS = (
    "sel:front_line",
    "sel:pit",
    "sel:Pit.2",
    "group:guard",
    "sel:front_line + group:woodwinds - sel:pit",
)
LABELLED_SELECTOR = "B2-B10 + G*"
LABEL_PAD = 3
SUBSET_AFTER = "5"
SECOND_WORKSPACE_EDITS = 10
RETIMED_FIRST_SET = 20
COUNT_BUMP = 0.5
POSITION_TOL = 1e-6
SEGMENT_PROBES = 9
SEGMENT_STRIDE = 6
SUBSET_TRANSITIONS = (4, 5)
AT = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


@dataclass(frozen=True)
class Scenario:
    """Holds what one scenario run produced.

    Attributes:
        v1: The v1 documents committed as the repository's first revision.
        report: Migration report of the stored v1 tree.
        repo: Repository every revision was saved to.
        v1_revision: Revision holding the v1 documents.
        migrated_revision: Revision the first save wrote, holding the v2 documents.
        again_revision: Revision a second save of the loaded migrated show returned.
        migrated: Show loaded from the v1 tree before the first save.
        base_revision: Revision of the sub-set insert and renumber.
        base: Show saved at `base_revision`.
        head: Show at the head before the revert.
        reverted: Show after reverting to `base_revision`.
        events: Canvas events of the second workspace's edits, taken before the revert.
        mapping: Old to new set IDs from the renumber.
        labels: Old to new spot IDs from the relabel.
        revision_docs: Show documents of every revision read oldest first, encoded after all
            reads.
        reread_docs: The same, read again newest first through the same workspace.
        rerun: A second run of the whole scenario, or None for the second run itself.
    """

    v1: dict[str, bytes]
    report: MigrationReport
    repo: FileRepository
    v1_revision: str
    migrated_revision: str
    again_revision: str
    migrated: Show
    base_revision: str
    base: Show
    head: Show
    reverted: Show
    events: list[CanvasEvent]
    mapping: dict[SetId, SetId]
    labels: dict[SpotId, str]
    revision_docs: dict[str, dict[str, bytes]]
    reread_docs: dict[str, dict[str, bytes]]
    rerun: "Scenario | None" = None


@contextmanager
def unchanged(*models: BaseModel) -> Iterator[None]:
    """Fails when any given model serializes differently after the block."""
    before = [m.model_dump_json() for m in models]
    yield
    assert [m.model_dump_json() for m in models] == before


def _v1_note(note: dict[str, Any]) -> dict[str, Any]:
    return note | {"kind": "todo", "author": NOTE_AUTHOR, "created_at": WRITTEN, "visible": True}


def v1_documents() -> dict[str, bytes]:
    """Returns the benchmark show's v1 documents with renames and notes added.

    The seeded show has valid spot IDs, no selections and no notes, so one spot ID is respelled
    with a hyphen, three named selections (one hyphenated, two clashing by case) join the cast
    and three notes (one hyphenated ID) are written to the v1 notes document.
    """
    stored: dict[str, str] = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    old, new = (f'"{spot}"' for spot in RESPELLED_SPOT)
    docs = {path: text.replace(old, new).encode() for path, text in stored.items()}
    show_doc = json.loads(docs[SHOW_DOC])
    show_doc["cast"]["selections"] = V1_SELECTIONS
    docs[SHOW_DOC] = json.dumps(show_doc).encode()
    docs[LEGACY_NOTES_DOC] = dump_json([_v1_note(n) for n in V1_NOTES])
    return docs


def _commit_v1(repo: FileRepository, show_id: str, docs: Mapping[str, bytes]) -> str:
    """Commits v1 documents through the backend, named by the current revision ID scheme."""
    tree = {path: blob_digest(raw) for path, raw in docs.items()}
    revision_id = document_repository.revision_id(ROOT_REVISION, AT, tree)
    revision = Revision(id=revision_id, parent=ROOT_REVISION, at=AT, changed=tuple(sorted(docs)))
    repo.backend.commit(show_id, revision, tree, {blob_digest(raw): raw for raw in docs.values()})
    return revision_id


def _restructured(show: Show) -> tuple[Show, dict[SetId, SetId], dict[SpotId, str]]:
    follower = show.set(show.sets[show.set_index(SUBSET_AFTER) + 1].id)
    with unchanged(show):
        edited, _ = insert_subset(show, SUBSET_AFTER, follower.counts / 2)
    with unchanged(edited):
        renumbered, mapping = renumber(edited)
    with unchanged(renumbered):
        relabeled, labels = relabel(renumbered, LabelScheme(pad=LABEL_PAD))
    return relabeled, mapping, labels


def _retimed(ws: Workspace, number: int, at: datetime) -> None:
    head = load_show(ws)
    target = str(RETIMED_FIRST_SET + number)
    with unchanged(head):
        edited = set_counts(head, target, head.set(target).counts + COUNT_BUMP)
    with unchanged(edited):
        save_show(ws, edited, at)


def _revision_docs(
    repo: FileRepository, show_id: str
) -> tuple[dict[str, dict[str, bytes]], dict[str, dict[str, bytes]]]:
    """Reads every revision's show twice through one workspace, oldest first, then newest
    first, so the decode cache shares models between them; encodes each only after all reads."""
    ws = open_workspace(repo, show_id)
    revisions = [r.id for r in repo.revisions(show_id)]
    forward = [(r, s) for r in revisions if (s := ws.show_at(r)) is not None]
    backward = [(r, s) for r in reversed(revisions) if (s := ws.show_at(r)) is not None]
    with unchanged(*(s for _, s in forward), *(s for _, s in backward)):
        return (
            {r: encode_show(s) for r, s in forward},
            {r: encode_show(s) for r, s in backward},
        )


def run_scenario(root: Path) -> Scenario:
    """Runs every phase of the scenario against a file repository under `root`."""
    v1 = v1_documents()
    show_id = str(json.loads(v1[SHOW_DOC])["id"])
    repo = FileRepository(root)
    v1_revision = _commit_v1(repo, show_id, v1)
    _, report = migrate_documents(repo.documents(show_id))
    ws = open_workspace(repo, show_id)
    migrated = load_show(ws)
    with unchanged(migrated):
        first = save_show(ws, migrated, AT + STEP)
    ws.refresh()
    reloaded = load_show(ws)
    with unchanged(reloaded):
        again = save_show(ws, reloaded, AT + STEP * 2)
    restructured, mapping, labels = _restructured(load_show(ws))
    with unchanged(restructured):
        base_revision = save_show(ws, restructured, AT + STEP * 3)
    base = load_show(ws)
    second = open_workspace(repo, show_id)
    for number in range(SECOND_WORKSPACE_EDITS):
        _retimed(second, number, AT + STEP * (number + 4))
    head = load_show(second)
    events = events_since(second, base_revision.id)
    revert(ws, base_revision.id, AT + STEP * 30)
    revision_docs, reread_docs = _revision_docs(repo, show_id)
    return Scenario(
        v1=v1,
        report=report,
        repo=repo,
        v1_revision=v1_revision,
        migrated_revision=first.id,
        again_revision=again.id,
        migrated=migrated,
        base_revision=base_revision.id,
        base=base,
        head=head,
        reverted=load_show(ws),
        events=events,
        mapping=mapping,
        labels=labels,
        revision_docs=revision_docs,
        reread_docs=reread_docs,
    )


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    """Runs the scenario twice in fresh repositories, keeping the second run for comparison."""
    first = run_scenario(tmp_path_factory.mktemp("phase2-scenario"))
    rerun = run_scenario(tmp_path_factory.mktemp("phase2-rerun"))
    return dataclasses.replace(first, rerun=rerun)


def _stored(scenario: Scenario, path: str, revision: str | None = None) -> Any:
    raw = scenario.repo.read(scenario.head.id, path, revision)
    assert raw is not None, path
    return json.loads(raw)


def _v1_show_doc(scenario: Scenario) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(scenario.v1[SHOW_DOC])
    return document


def _v1_forms(scenario: Scenario) -> dict[tuple[str, tuple[str, ...]], str]:
    """Maps (set ID, members) to each v1 form's ID, read from the v1 forms documents."""
    forms: dict[tuple[str, tuple[str, ...]], str] = {}
    for path, raw in scenario.v1.items():
        if path.endswith("/forms.json"):
            for form in json.loads(raw):
                members = tuple(m for m in form["members"] if m is not None)
                forms[form["set"], members] = form["id"]
    return forms


def _changed(pairs: Iterator[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted((old, new) for old, new in pairs if old != new)


def test_scenario_edits_hit_every_phase(scenario: Scenario) -> None:
    """Checks each phase of the run left its mark on the shows and the log."""
    assert scenario.report.from_version == 1
    assert len(scenario.migrated.cast.spots) == SPOTS
    assert len(scenario.migrated.sets) == SETS
    assert [s.id for s in scenario.head.sets] == [str(n) for n in range(1, SETS + 2)]
    assert scenario.mapping[SetId("6")] == "7"
    assert scenario.labels[SpotId("brass_1")].startswith("B")
    assert all(len(i) == len("B001") for i in scenario.head.cast.ids())
    log = [r.id for r in scenario.repo.revisions(scenario.head.id)]
    assert log[:2] == [scenario.v1_revision, scenario.migrated_revision]
    assert len(log) == SECOND_WORKSPACE_EDITS + 4


def test_report_lists_every_rename(scenario: Scenario) -> None:
    """Checks the migration report lists every renamed ID, and only those."""
    migrated = scenario.migrated
    v1_cast = _v1_show_doc(scenario)["cast"]
    spots = _changed(zip((s["id"] for s in v1_cast["spots"]), migrated.cast.ids(), strict=True))
    back = {new: old for old, new in spots}

    def v1_ids(ids: tuple[str | None, ...]) -> tuple[str, ...]:
        return tuple(back.get(i, i) for i in ids if i is not None)

    v1_forms = _v1_forms(scenario)
    forms = _changed((v1_forms[f.set, v1_ids(f.members)], f.id) for f in migrated.forms.values())
    selections = _changed(
        (old, new)
        for old, ids in V1_SELECTIONS.items()
        for new, kept in migrated.cast.selections.items()
        if v1_ids(kept) == tuple(ids)
    )
    stored_notes = _stored(scenario, NOTES_DOC, scenario.migrated_revision)["notes"]
    notes = _changed(zip((n["id"] for n in V1_NOTES), (n["id"] for n in stored_notes), strict=True))
    assert len(v1_forms) == len(migrated.forms)
    assert spots == [(RESPELLED_SPOT[1], "brass_1")]
    assert selections == [("Pit", "Pit.2"), ("front-line", "front_line")]
    assert notes == [("n-1", "n_1")]
    assert len(forms) > SETS
    expected = {"spot": spots, "form": forms, "selection": selections, "note": notes}
    reported = {kind: sorted(pairs) for kind, pairs in scenario.report.renamed.items()}
    assert reported == expected


def test_second_save_writes_nothing(scenario: Scenario) -> None:
    """Checks a second save of the loaded migrated show adds no revision."""
    log = [r.id for r in scenario.repo.revisions(scenario.head.id)]
    assert scenario.again_revision == scenario.migrated_revision
    assert log.count(scenario.migrated_revision) == 1
    assert log[2] == scenario.base_revision


def _selected(show: Show) -> dict[str, tuple[str, ...]]:
    with unchanged(show):
        return {expr: select(show, expr).ids for expr in LABEL_FREE_SELECTORS}


def test_selections_select_the_same_spots(scenario: Scenario) -> None:
    """Checks named selections and selectors pick the same spots through every edit."""
    migrated = scenario.migrated
    renamed = dict(scenario.report.renamed["spot"])
    selections = migrated.cast.selections
    front_line = tuple(renamed.get(i, i) for i in V1_SELECTIONS["front-line"])
    assert selections["front_line"] == front_line
    assert selections["pit"] == tuple(V1_SELECTIONS["pit"])
    assert selections["Pit.2"] == tuple(V1_SELECTIONS["Pit"])
    guard = tuple(s.id for s in migrated.cast.spots if s.group == "guard")
    with unchanged(migrated):
        labelled = select(migrated, LABELLED_SELECTOR).ids
    assert labelled == (*(f"B{n}" for n in range(2, 11)), *guard)
    before = _selected(migrated)
    assert all(before.values())
    relabeled = {
        e: tuple(scenario.labels.get(SpotId(i), i) for i in ids) for e, ids in before.items()
    }
    assert relabeled != before
    assert _selected(scenario.base) == relabeled
    assert _selected(scenario.head) == relabeled
    assert _selected(scenario.reverted) == relabeled


def test_notes_land_in_the_notes_document(scenario: Scenario) -> None:
    """Checks the v1 notes are stored in the notes document with text, author and time kept."""
    renamed = dict(scenario.report.renamed["note"])
    for revision in (scenario.migrated_revision, scenario.base_revision):
        document = _stored(scenario, NOTES_DOC, revision)
        assert (document["format"], document["version"], document["show"]) == (
            NOTES_FORMAT,
            NOTES_VERSION,
            scenario.head.id,
        )
        assert [(n["id"], n["text"], n["author"]) for n in document["notes"]] == [
            (renamed.get(n["id"], n["id"]), n["text"], NOTE_AUTHOR) for n in V1_NOTES
        ]
        written = {datetime.fromisoformat(n["created_at"]) for n in document["notes"]}
        assert written == {datetime.fromisoformat(WRITTEN)}
        assert "notes" not in _stored(scenario, SHOW_DOC, revision)
        assert scenario.repo.read(scenario.head.id, LEGACY_NOTES_DOC, revision) is None


def test_format_is_e8_show(scenario: Scenario) -> None:
    """Checks every stored show document opens with format e8.show and version 2."""
    for revision in (scenario.migrated_revision, scenario.base_revision, None):
        header = list(_stored(scenario, SHOW_DOC, revision).items())[:2]
        assert header == [("format", FORMAT), ("version", 2)]
    assert "format" not in _v1_show_doc(scenario)


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


def test_positions_are_continuous_at_every_set_boundary(scenario: Scenario) -> None:
    """Checks every transition starts and ends on its sets' placements within 1e-6 m."""
    head = scenario.head
    ends: dict[tuple[str, int], np.ndarray] = {}
    with unchanged(head):
        for spot, index, start, end in _transition_ends(head):
            before, after = head.sets[index], head.sets[index + 1]
            assert approx_equal(start, _xy(before, spot), abs_tol=POSITION_TOL), (spot, index)
            assert approx_equal(end, _xy(after, spot), abs_tol=POSITION_TOL), (spot, index)
            if index:
                previous = ends[spot, index - 1]
                assert approx_equal(start, previous, abs_tol=POSITION_TOL), (spot, index)
            ends[spot, index] = end
    assert len(ends) == SPOTS * SETS


def _probe_gap(engine: PathEngine, piece: MotionSegment) -> float:
    worst = 0.0
    for fraction in np.linspace(0.0, 1.0, SEGMENT_PROBES):
        t = piece.t0 + (piece.t1 - piece.t0) * fraction
        line = np.asarray(piece.p0) + (np.asarray(piece.p1) - np.asarray(piece.p0)) * fraction
        gap = float(np.linalg.norm(engine.position(piece.entity, t, piece.tid) - line))
        worst = max(worst, gap)
    return worst


def test_segments_follow_the_engine(scenario: Scenario) -> None:
    """Checks sampled segments stay within the engine's segment bound of its positions."""
    head = scenario.head
    tids = head.transition_ids()
    sampled = [t for i, t in enumerate(tids) if i % SEGMENT_STRIDE == 0 or i in SUBSET_TRANSITIONS]
    with unchanged(head):
        engine = PathEngine(head)
        pieces = [piece for tid in sampled for piece in engine.segments(tid)]
        gaps = [_probe_gap(engine, piece) for piece in pieces]
    assert len(pieces) >= len(sampled) * SPOTS
    assert max(gaps) <= DEFAULT_SEGMENT_TOL


def test_json_and_document_round_trips_are_equal(scenario: Scenario) -> None:
    """Checks the head show survives JSON and stored-document round trips unchanged."""
    head = scenario.head
    with unchanged(head):
        assert import_json(export_json(head)) == head
        documents = encode_show(head)
        decoded = decode_show(documents, head.revision)
        assert decoded == head
    with unchanged(decoded):
        assert encode_show(decoded) == documents


def test_events_since_replays_to_head(scenario: Scenario) -> None:
    """Checks the second workspace's events replay from the base show to the head."""
    edits = [r.id for r in scenario.repo.revisions(scenario.head.id)][3:-1]
    assert len(edits) == SECOND_WORKSPACE_EDITS
    assert {e.revision for e in scenario.events if e.revision} >= set(edits)
    replayed = scenario.base
    for event in scenario.events:
        with unchanged(replayed, event):
            replayed = apply_event(replayed, event)
    with unchanged(replayed, scenario.head):
        assert encode_show(replayed) == encode_show(scenario.head)


def test_revert_restores_an_earlier_revision(scenario: Scenario) -> None:
    """Checks revert writes a new revision holding the base show."""
    assert scenario.reverted.revision != scenario.base_revision
    with unchanged(scenario.reverted, scenario.base):
        assert encode_show(scenario.reverted) == encode_show(scenario.base)
    assert scenario.reverted != scenario.head


def test_one_revision_never_changes_another(scenario: Scenario) -> None:
    """Checks every revision's show encodes as a fresh decode after all revisions are read."""
    log = [r.id for r in scenario.repo.revisions(scenario.head.id)]
    assert set(scenario.revision_docs) == set(log)
    assert scenario.reread_docs == scenario.revision_docs
    for revision in log:
        fresh = decode_show(scenario.repo.documents(scenario.head.id, revision), revision)
        with unchanged(fresh):
            assert scenario.revision_docs[revision] == encode_show(fresh), revision
    distinct = {tuple(sorted(docs.items())) for docs in scenario.revision_docs.values()}
    assert len(distinct) == len(log) - 2
    assert scenario.revision_docs[log[-1]] == scenario.revision_docs[scenario.base_revision]
    assert scenario.revision_docs[log[0]] == scenario.revision_docs[scenario.migrated_revision]


def test_two_runs_are_byte_identical(scenario: Scenario) -> None:
    """Checks a second run gives the same documents, hashes, revisions and report."""
    again = scenario.rerun
    assert again is not None
    with unchanged(again.head, scenario.head, again.base, scenario.base):
        assert encode_show(again.head) == encode_show(scenario.head)
        assert content_hash(again.head) == content_hash(scenario.head)
        assert encode_show(again.base) == encode_show(scenario.base)
    with unchanged(again.reverted, scenario.reverted):
        assert encode_show(again.reverted) == encode_show(scenario.reverted)
    assert again.head.revision == scenario.head.revision
    assert again.report == scenario.report
    assert again.labels == scenario.labels
    assert again.revision_docs == scenario.revision_docs
