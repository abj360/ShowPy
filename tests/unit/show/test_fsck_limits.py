#!/usr/bin/env python3

"""
test_fsck_limits.py --- tests the bounds fsck keeps: legacy_before and the caps every read keeps

Contains:
    test_regression_fsck_reports_a_legacy_before_past_the_head: reports a manifest whose
        legacy_before would skip the ID check of every slot, which hid a swapped record
    test_fsck_accepts_legacy_before_up_to_the_slot_after_the_last: passes legacy_before at the
        slot after the last and reports it one slot further
    test_fsck_reports_a_history_past_the_caps_reads_keep: reports a show with more slots than
        MAX_REVISIONS, or more slot and record bytes than LOG_READ_MAX_BYTES, and stops its chain
"""

import json
from pathlib import Path

import pytest

from e8s.show import fsck_show
from e8s.show.backend import digest
from e8s.show.document_dump import dump_json
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from tests.fixtures.fsck_repos import (
    AT,
    DAY,
    SHOW_ID,
    codes,
    landed_revisions,
    record_path,
    relative,
    show_path,
    slot_path,
)

pytestmark = pytest.mark.usefixtures("no_reread_pauses")


@pytest.mark.parametrize("legacy_before", [10**9, 50])
def test_regression_fsck_reports_a_legacy_before_past_the_head(
    tmp_path: Path, legacy_before: int
) -> None:
    """A swapped record whose ID no longer verifies passes when legacy_before skips the check."""
    landed = landed_revisions(tmp_path, 3)
    path = record_path(tmp_path, landed[2])
    stored = json.loads(path.read_bytes())
    stored["tree"]["/render.txt"] = digest(b"render 1")
    path.write_bytes(dump_json(stored))
    manifest = show_path(tmp_path) / "show.json"
    manifest.write_bytes(dump_json({"id": SHOW_ID, "legacy_before": legacy_before}))
    report = fsck(tmp_path)
    assert ("BAD_RECORD", relative(tmp_path, manifest)) in codes(tmp_path)
    assert "legacy_before" in next(f.message for f in report.findings if f.path.endswith("json"))
    assert not any(f.code == "UNREACHABLE" for f in report.findings)
    assert not report.ok
    assert gc(tmp_path, at=AT + 3 * DAY, confirm=True).removed == ()


@pytest.mark.parametrize(("past_last", "is_ok"), [(1, True), (2, False)])
def test_fsck_accepts_legacy_before_up_to_the_slot_after_the_last(
    tmp_path: Path, past_last: int, *, is_ok: bool
) -> None:
    """An upgrade writes the slot after its last legacy slot, never past the slot after the last."""
    landed_revisions(tmp_path, 3)
    manifest = show_path(tmp_path) / "show.json"
    manifest.write_bytes(dump_json({"id": SHOW_ID, "legacy_before": 3 + past_last}))
    report = fsck(tmp_path)
    expected = [] if is_ok else [("BAD_RECORD", relative(tmp_path, manifest))]
    assert [(f.code, f.path) for f in report.findings] == expected
    assert report.ok is is_ok


@pytest.mark.parametrize(
    ("cap", "slots_within"),
    [("MAX_REVISIONS", 0), ("LOG_READ_MAX_BYTES", 2), ("LOG_READ_MAX_BYTES", 3)],
)
def test_fsck_reports_a_history_past_the_caps_reads_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: str, slots_within: int
) -> None:
    """The bytes cap runs out at the third slot, or at its record; the third record is damaged."""
    landed = landed_revisions(tmp_path, 3)
    record_path(tmp_path, landed[2]).write_bytes(b"{}")
    slots = sum(len(slot_path(tmp_path, seq).read_bytes()) for seq in range(1, slots_within + 1))
    records = sum(len(record_path(tmp_path, revision).read_bytes()) for revision in landed[:2])
    monkeypatch.setattr(fsck_show, cap, 2 if cap == "MAX_REVISIONS" else slots + records)
    report = fsck(tmp_path)
    log = relative(tmp_path, show_path(tmp_path) / "log")
    assert [(f.code, f.path) for f in report.findings] == [("BAD_RECORD", log)]
    assert "reads refuse" in report.findings[0].message
    assert not report.ok
