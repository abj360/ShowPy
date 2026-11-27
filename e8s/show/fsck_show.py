"""
fsck_show.py --- checks one show folder of a layout-2 repository for fsck and gc

A show folder holds a manifest, numbered slots, revision records and blobs. The check reads each
slot, each record a slot names and each blob a tree names once, in slot order, keeping only the
previous tree and the IDs reached so far. Records and blobs no slot reaches are reported as
unreachable only when every slot and record was read, so a damaged show never offers a file that
may still be needed for removal.

Contains:
    MISSING_BLOB: codes a blob a tree names that is not stored
    BAD_BLOB: codes a stored blob whose bytes do not hash to its name, or that is not a file
    BAD_RECORD: codes a manifest, slot or revision record that fails its checks
    SLOT_GAP: codes a run of missing slots before a later slot
    TORN_SLOT: codes a slot that is empty or cut short
    UNREACHABLE: codes a record or blob that no slot reaches
    STALE_TEMP: codes a temp file a crashed writer left behind
    UNKNOWN_FILE: codes an entry that is not part of a layout-2 repository, or a link
    TORN_FILE: codes a manifest or layout file that a crashed first write left empty or cut short
    FAILING_CODES: lists the codes that fail a check; the others are left for a clean-up
    ShowScan: holds what checking one show folder found
    finding(): builds a finding whose path is relative to the repository root
    relative_path(): names a path under the root relative to it, with `/` separators
    is_plain_folder(): tells whether an lstat result is a folder and not a link or junction
    is_torn_manifest(): tells whether manifest bytes are a shorter start of a show's manifest
    scan_show(): checks one show folder: manifest, slot chain, records, trees and blobs
"""

import os
import re
import stat
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.backend_checks import (
    FIRST_SEQ,
    Record,
    corrupt,
    parsed_json,
    parsed_manifest,
    parsed_record,
    parsed_slot,
    require_blob_digest,
    require_slot_record,
)
from e8s.show.durable_io import is_redirect, listed_entries, lstat_mode, read_capped
from e8s.show.file_backend import (
    LOG_DIR,
    MANIFEST_FILE,
    MANIFEST_MAX_BYTES,
    OBJECT_DIR,
    RECORD_DIR,
    SLOT_DIGITS,
    SLOT_MAX_BYTES,
)
from e8s.show.history_models import Revision
from e8s.show.layout import HOUSEKEEPING_NAMES
from e8s.show.layout1 import BLOB_MAX_BYTES, TREE_MAX_BYTES
from e8s.show.show_folders import show_folder
from e8s.show.storage import ROOT_REVISION
from e8s.show.storage_models import FsckFinding
from e8s.show.torn_reads import is_unfinished_json, is_unfinished_line, read_settled

MISSING_BLOB = "MISSING_BLOB"
BAD_BLOB = "BAD_BLOB"
BAD_RECORD = "BAD_RECORD"
SLOT_GAP = "SLOT_GAP"
TORN_SLOT = "TORN_SLOT"
UNREACHABLE = "UNREACHABLE"
STALE_TEMP = "STALE_TEMP"
UNKNOWN_FILE = "UNKNOWN_FILE"
TORN_FILE = "TORN_FILE"
FAILING_CODES = frozenset(
    {MISSING_BLOB, BAD_BLOB, BAD_RECORD, SLOT_GAP, TORN_SLOT, UNKNOWN_FILE, TORN_FILE}
)
SLOT_NAME = re.compile(rf"^[0-9]{{{SLOT_DIGITS}}}$")
RECORD_NAME = re.compile(r"^([0-9a-f]{64})\.json$")
BLOB_NAME = re.compile(r"^([0-9a-f]{64})$")
SHOW_SUBFOLDERS = (LOG_DIR, RECORD_DIR, OBJECT_DIR)
# A manifest is written as dump_json({"id": show_id}); a show ID is written verbatim between its
# quotes only when it holds none of the characters JSON escapes, which stored show IDs never do.
MANIFEST_HEAD = b'{\n  "id": "'
MANIFEST_TAIL = b'"\n}\n'
VERBATIM_ID = re.compile(rb"^[A-Za-z0-9_.\-]*$")


def finding(root: Path, code: str, path: Path, message: str, show: str = "") -> FsckFinding:
    """Builds a finding whose path is relative to the repository root, with `/` separators.

    Args:
        root: Repository root folder.
        code: Finding code.
        path: File or folder the problem is at, under `root`.
        message: What is wrong.
        show: ID of the show the problem is in; empty when no manifest names it.
    """
    return FsckFinding(code=code, path=relative_path(root, path), message=message, show=show)


def relative_path(root: Path, path: Path) -> str:
    """Names a path under the root relative to it, with `/` separators."""
    return path.relative_to(root).as_posix()


def is_plain_folder(path: Path, info: os.stat_result) -> bool:
    """Tells whether an lstat result is a folder, and not a symlink or a junction."""
    return stat.S_ISDIR(info.st_mode) and not is_redirect(path, info.st_mode)


def is_torn_manifest(data: bytes, folder_name: str) -> bool:
    """Tells whether bytes are a shorter start of the manifest a show in this folder would hold.

    Where the show ID is complete it must be the one the folder name encodes; where it is cut
    short, its readable part must agree with the start of the folder name.

    Args:
        data: Bytes read from the manifest.
        folder_name: Name of the show folder that holds it.

    Returns:
        is_torn: True only when some manifest of this folder starts with `data` and is longer.
    """
    if len(data) <= len(MANIFEST_HEAD):
        return MANIFEST_HEAD.startswith(data)
    if not data.startswith(MANIFEST_HEAD):
        return False
    body = data[len(MANIFEST_HEAD) :]
    closing = body.find(b'"')
    if closing < 0:
        slug = (
            show_folder(body.decode("ascii")).rpartition("-")[0]
            if VERBATIM_ID.match(body)
            else None
        )
        return slug is not None and folder_name.startswith(slug)
    show_id, tail = body[:closing], body[closing:]
    return (
        bool(VERBATIM_ID.match(show_id))
        and len(tail) < len(MANIFEST_TAIL)
        and MANIFEST_TAIL.startswith(tail)
        and show_folder(show_id.decode("ascii")) == folder_name
    )


class ShowScan(NamedTuple):
    """Holds what checking one show folder found.

    Attributes:
        folder: The show folder.
        show_id: ID its manifest names; empty when the manifest is missing or damaged.
        findings: Problems found; records and blobs are reported unreachable only when every
            slot and record was read.
        last_seq: Number of the last slot, 0 before the first.
    """

    folder: Path
    show_id: str
    findings: list[FsckFinding]
    last_seq: int


def _applied(
    previous: Mapping[str, str], revision: Revision, tree: Mapping[str, str]
) -> dict[str, str]:
    """Returns the previous tree with each changed path set as the new tree has it."""
    applied = {path: digest for path, digest in previous.items() if path not in revision.changed}
    applied.update({path: tree[path] for path in revision.changed if path in tree})
    return applied


class _ShowCheck:
    """Checks one show folder, collecting its findings."""

    def __init__(self, root: Path, folder: Path) -> None:
        self.root = root
        self.folder = folder
        self.show_id = ""
        self.legacy_before = FIRST_SEQ
        self.problems: list[tuple[str, Path, str]] = []
        self.records: set[str] = set()
        self.blobs: set[str] = set()
        self.unreadable: set[str] = set()
        self.is_proven = True
        self.last_seq = 0

    def report(self, code: str, path: Path, message: str) -> None:
        self.problems.append((code, path, message))

    def result(self) -> ShowScan:
        """Returns the scan, naming the show in every finding once the manifest was read."""
        findings = [finding(self.root, *problem, show=self.show_id) for problem in self.problems]
        return ShowScan(
            self.folder,
            self.show_id,
            findings,
            self.last_seq,
        )

    def run(self) -> None:
        """Checks the folder's entries, then its manifest, slots, records, blobs and leftovers."""
        entries = self._entries()
        seqs = self._slot_seqs() if LOG_DIR not in self.unreadable else []
        self.last_seq = seqs[-1] if seqs else 0
        self._check_manifest(is_present=MANIFEST_FILE in entries, has_slots=bool(seqs))
        self._report_gaps(seqs)
        self._check_chain(seqs)
        self._list_leftovers(RECORD_DIR, RECORD_NAME, self.records)
        self._list_leftovers(OBJECT_DIR, BLOB_NAME, self.blobs)

    def _entries(self) -> set[str]:
        """Lists the folder, reporting unknown entries and subfolders that are not plain folders."""
        names: set[str] = set()
        for name, info in listed_entries(self.folder):
            path = self.folder / name
            if name in HOUSEKEEPING_NAMES:
                continue
            if name in SHOW_SUBFOLDERS and not is_plain_folder(path, info):
                self.unreadable.add(name)
                self.is_proven = False
                self.report(UNKNOWN_FILE, path, "is not a plain folder; fsck never follows a link")
            elif name not in (MANIFEST_FILE, *SHOW_SUBFOLDERS):
                self.report(UNKNOWN_FILE, path, "is not part of a show folder")
            names.add(name)
        return names

    def _check_manifest(self, *, is_present: bool, has_slots: bool) -> None:
        """Reads the manifest for the show ID and `legacy_before`, reporting a damaged one."""
        path = self.folder / MANIFEST_FILE
        if not is_present:
            if has_slots:
                self.report(BAD_RECORD, path, "manifest is missing, so no show owns the slots")
            return
        fail = partial(corrupt, "")
        read = partial(read_capped, path, "manifest", MANIFEST_MAX_BYTES, fail, allows_growth=True)
        try:
            raw = read_settled(read, is_unfinished_json)
        except StorageError as error:
            self.report(BAD_RECORD, path, error.message)
            return
        try:
            stored = parsed_json("", path, "manifest", raw)
            self.show_id, self.legacy_before = parsed_manifest("", path, stored)
        except StorageError as error:
            if is_torn_manifest(raw, self.folder.name):
                self.report(
                    TORN_FILE, path, "manifest is empty or cut short: a first write stopped"
                )
            else:
                self.report(BAD_RECORD, path, error.message)

    def _slot_seqs(self) -> list[int]:
        """Lists the slot numbers in log/, reporting every other entry as unknown."""
        log = self.folder / LOG_DIR
        if lstat_mode("", log) is None:
            return []
        seqs: list[int] = []
        for name, _ in listed_entries(log):
            if name in HOUSEKEEPING_NAMES:
                continue
            if SLOT_NAME.fullmatch(name) and int(name) >= FIRST_SEQ:
                seqs.append(int(name))
            else:
                self.report(UNKNOWN_FILE, log / name, "is not a slot name")
        return seqs

    def _slot_path(self, seq: int) -> Path:
        return self.folder / LOG_DIR / f"{seq:0{SLOT_DIGITS}d}"

    def _report_gaps(self, seqs: list[int]) -> None:
        """Reports each run of missing slots before a later slot as one SLOT_GAP."""
        expected = FIRST_SEQ
        for seq in seqs:
            if seq > expected:
                self.is_proven = False
                missing = (
                    f"slot {expected}"
                    if seq == expected + 1
                    else (f"slots {expected} to {seq - 1}")
                )
                self.report(
                    SLOT_GAP, self._slot_path(expected), f"{missing} missing before slot {seq}"
                )
            expected = seq + 1

    def _check_chain(self, seqs: list[int]) -> None:
        """Reads each slot and its record in order, checking parents, trees and blobs."""
        parent: str | None = ROOT_REVISION
        tree: dict[str, str] | None = {}
        previous = 0
        for seq in seqs:
            if seq != previous + 1:
                parent, tree = None, None
            previous = seq
            revision = self._slot(seq, is_last=seq == seqs[-1], follows_intact=parent is not None)
            if revision is None:
                parent, tree = None, None
                continue
            if parent is not None and revision.parent != parent:
                self.report(
                    BAD_RECORD, self._slot_path(seq), "slot's parent is not the slot before"
                )
            self.records.add(revision.id)
            parent = revision.id
            record = self._record(seq, revision)
            if (
                record is not None
                and tree is not None
                and record.tree != _applied(tree, revision, record.tree)
            ):
                self.report(
                    BAD_RECORD,
                    self._record_path(revision.id),
                    "tree is not the previous tree with this revision's changes",
                )
            tree = None if record is None else record.tree
            if record is not None:
                self._check_blobs(record.tree)

    def _slot(self, seq: int, *, is_last: bool, follows_intact: bool) -> Revision | None:
        """Reads and checks one slot, reporting a torn or damaged one and returning None."""
        path = self._slot_path(seq)
        fail = partial(corrupt, self.show_id)
        read = partial(read_capped, path, "slot", SLOT_MAX_BYTES, fail, allows_growth=True)
        try:
            return parsed_slot(self.show_id, path, read_settled(read, is_unfinished_line))
        except StorageError as error:
            self.is_proven = False
            if error.code != ErrorCode.TORN_SLOT:
                self.report(BAD_RECORD, path, error.message)
            elif is_last and follows_intact:
                self.report(TORN_SLOT, path, "last slot is empty or cut short: its writer stopped")
            else:
                self.report(
                    TORN_SLOT,
                    path,
                    "slot is empty or cut short and is not a torn last slot after an intact one,"
                    " so no repair removes it: restore it from a backup",
                )
            return None

    def _record_path(self, revision_id: str) -> Path:
        return self.folder / RECORD_DIR / f"{revision_id}.json"

    def _record(self, seq: int, revision: Revision) -> Record | None:
        """Reads the record a slot names and checks it, reporting a missing or damaged one."""
        path = self._record_path(revision.id)
        if RECORD_DIR in self.unreadable:
            return None
        if lstat_mode(self.show_id, path) is None:
            self.is_proven = False
            self.report(BAD_RECORD, self._slot_path(seq), "slot names a revision with no record")
            return None
        fail = partial(corrupt, self.show_id)
        try:
            raw = read_capped(path, "revision record", TREE_MAX_BYTES, fail, allows_growth=True)
            stored = parsed_json(self.show_id, path, "revision record", raw)
            record = parsed_record(self.show_id, path, stored, revision.id, self.legacy_before)
            require_slot_record(self.show_id, self._slot_path(seq), seq, revision, record)
        except StorageError as error:
            self.is_proven = False
            self.report(BAD_RECORD, path, error.message)
            return None
        return record

    def _check_blobs(self, tree: Mapping[str, str]) -> None:
        """Checks each blob a tree names that no earlier tree named: present, and hashing right."""
        if OBJECT_DIR in self.unreadable:
            return
        for digest in sorted(set(tree.values()) - self.blobs):
            self.blobs.add(digest)
            path = self.folder / OBJECT_DIR / digest
            if lstat_mode(self.show_id, path) is None:
                self.report(MISSING_BLOB, path, "a tree names this blob, which is not stored")
                continue
            fail = partial(corrupt, self.show_id)
            try:
                require_blob_digest(
                    self.show_id, path, read_capped(path, "blob", BLOB_MAX_BYTES, fail)
                )
            except StorageError as error:
                self.report(BAD_BLOB, path, error.message)

    def _list_leftovers(self, name: str, pattern: re.Pattern[str], reached: set[str]) -> None:
        """Lists a folder of records or blobs, reporting unknown entries and unreached files."""
        folder = self.folder / name
        if name in self.unreadable or lstat_mode(self.show_id, folder) is None:
            return
        for entry, info in listed_entries(folder):
            path = folder / entry
            matched = pattern.fullmatch(entry)
            if entry in HOUSEKEEPING_NAMES or (matched and matched.group(1) in reached):
                continue
            if matched is None or not stat.S_ISREG(info.st_mode):
                self.report(UNKNOWN_FILE, path, f"is not a {name} file name or not a regular file")
            elif self.is_proven:
                self.report(UNREACHABLE, path, "no slot reaches this file")


def scan_show(root: Path, folder: Path) -> ShowScan:
    """Checks one show folder: its manifest, slot chain, records, trees and blobs.

    Args:
        root: Repository root folder, which finding paths are relative to.
        folder: The show folder, already known to be a plain folder under `<root>/shows/`.

    Returns:
        scan: The show's ID, findings and last slot.
    """
    check = _ShowCheck(root, folder)
    check.run()
    return check.result()
