#!/usr/bin/env python3

"""
test_legacy_prefix.py --- tests the legacy prefix an upgrade records in a show's manifest

Contains:
    test_upgrade_records_the_legacy_prefix_in_the_manifest: writes legacy_before, legacy_count and
        legacy_digest for the v0.4.0 fixture, and fsck accepts them
    test_regression_a_tampered_legacy_prefix_is_refused: refuses a manifest whose legacy keys
        were edited (legacy_before up or down, at the slot after the last, count, digest, a key
        alone) on log and on a tree of a legacy revision, on head where the keys disagree, and
        reports it in fsck
    test_regression_a_swapped_legacy_record_is_caught_by_the_digest: refuses a legacy slot and
        record replaced by another revision whose ID cannot be checked
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import StorageError
from e8s.show.document_dump import dump_json
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import copied

LEGACY_SHOW = "legacy-show"
# v0.4.0: the second of six revisions has an old-scheme ID; v0.1.1: all three have.
V040_PREFIX = {"legacy_before": 3, "legacy_count": 2}
OTHER_DIGEST = "0" * 64
# Each edit, and whether the keys then disagree with each other or with the last slot, which a
# head lookup catches; the others only a read of the legacy slots catches, by the digest.
TAMPERS: dict[str, tuple[dict[str, object], bool]] = {
    "down": ({"legacy_before": 2}, True),
    "down-consistent": ({"legacy_before": 2, "legacy_count": 1}, False),
    "up-consistent": ({"legacy_before": 5, "legacy_count": 4}, False),
    "after-last": ({"legacy_before": 7, "legacy_count": 6}, False),
    "past-last": ({"legacy_before": 8, "legacy_count": 7}, True),
    "count": ({"legacy_count": 3}, True),
    "digest": ({"legacy_digest": OTHER_DIGEST}, False),
    "keys-without-legacy-before": ({"legacy_before": None}, True),
    "legacy-before-alone": ({"legacy_count": None, "legacy_digest": None}, True),
}


def _upgraded(tmp_path: Path, release: str = "v0.4.0") -> tuple[Path, Path]:
    target = tmp_path / "new"
    upgrade_repository(copied(tmp_path, release), target)
    return target, target / "shows" / show_folder(LEGACY_SHOW)


def _edit_manifest(folder: Path, changes: dict[str, object]) -> None:
    stored = json.loads((folder / "show.json").read_bytes())
    for key, value in changes.items():
        if value is None:
            del stored[key]
        else:
            stored[key] = value
    (folder / "show.json").write_bytes(dump_json(stored))


def _expect_corrupt(read: Callable[[], object], file: str = "show.json") -> None:
    with pytest.raises(StorageError) as caught:
        read()
    assert caught.value.code == "CORRUPT"
    assert caught.value.context["file"] == file


def test_upgrade_records_the_legacy_prefix_in_the_manifest(tmp_path: Path) -> None:
    target, folder = _upgraded(tmp_path)
    stored = json.loads((folder / "show.json").read_bytes())
    assert {key: stored[key] for key in V040_PREFIX} == V040_PREFIX
    assert len(stored["legacy_digest"]) == len(OTHER_DIGEST)
    assert fsck(target).findings == ()


@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_regression_a_tampered_legacy_prefix_is_refused(tmp_path: Path, name: str) -> None:
    target, folder = _upgraded(tmp_path)
    history = FileBackend(target).log(LEGACY_SHOW)
    changes, is_structural = TAMPERS[name]
    _edit_manifest(folder, changes)
    # Shrunk to one slot, the prefix leaves the second, old-scheme ID to the record check.
    logged = f"{history[1].id}.json" if name == "down-consistent" else "show.json"
    _expect_corrupt(lambda: FileBackend(target).log(LEGACY_SHOW), logged)
    _expect_corrupt(lambda: FileBackend(target).tree(LEGACY_SHOW, history[0].id))
    if is_structural:
        _expect_corrupt(lambda: FileBackend(target).head(LEGACY_SHOW))
    findings = fsck(target).findings
    assert any(f.code == "BAD_RECORD" and f.path.endswith("show.json") for f in findings)


def test_regression_a_swapped_legacy_record_is_caught_by_the_digest(tmp_path: Path) -> None:
    target, folder = _upgraded(tmp_path, "v0.1.1")
    history = FileBackend(target).log(LEGACY_SHOW)
    head = history[-1]
    forged = Revision(id="e" * 64, parent=head.parent, at=head.at, changed=head.changed)
    record = json.loads((folder / "records" / f"{head.id}.json").read_bytes())
    (folder / "records" / f"{head.id}.json").unlink()
    record["revision"] = forged.to_json()
    (folder / "records" / f"{forged.id}.json").write_bytes(dump_json(record))
    slot = folder / "log" / f"{len(history):012d}"
    slot.unlink()
    slot.write_bytes(jcs(forged.to_json()) + b"\n")
    _expect_corrupt(lambda: FileBackend(target).log(LEGACY_SHOW))
    _expect_corrupt(lambda: FileBackend(target).tree(LEGACY_SHOW, forged.id))
    findings = fsck(target).findings
    assert any(f.code == "BAD_RECORD" and f.path.endswith("show.json") for f in findings)
