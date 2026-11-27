"""
file_backend.py --- the layout-2 file backend: numbered slots, encoded show folders, O(1) head

Contains:
    SLOT_DIGITS: sets the width of the zero-padded sequence number that names a slot
    SLOT_MAX_BYTES: caps the size of a slot read
    LOG_READ_MAX_BYTES: caps the bytes of slots and records one log() call reads
    MANIFEST_MAX_BYTES: caps the size of a show manifest read
    FileBackend: stores each show as numbered slots, revision records and blobs under a root
"""

from collections.abc import Mapping
from functools import partial
from pathlib import Path

from e8s.core.errors import ConflictError, UnknownRefError
from e8s.core.json_types import JsonValue
from e8s.show import durable_io
from e8s.show.backend import (
    MAX_REVISIONS,
    REVISION_ID_PATTERN,
    conflict_error,
    history_too_long_error,
    unknown_revision_error,
)
from e8s.show.backend_checks import (
    FIRST_SEQ,
    ReadBudget,
    Record,
    corrupt,
    last_present,
    missing_file,
    parsed_json,
    parsed_record,
    parsed_slot,
    refuse_redirect,
    require_blob_digest,
    require_caller_ids,
    require_plain_folders,
    require_readable_sizes,
    require_slot_record,
)
from e8s.show.commit_writes import (
    STALE_TEMP_AGE,
    FilePlacer,
    flush_directories,
    place_or_check,
    record_bytes,
    remove_lost_record,
    slot_bytes,
    store_blobs,
    store_record,
    write_layout_file,
)
from e8s.show.document_dump import dump_json
from e8s.show.durable_io import mapped_io, remove_stale_temps
from e8s.show.durable_reads import lstat_mode, read_capped
from e8s.show.fsck_findings import SET_ASIDE_PREFIX
from e8s.show.history_models import Revision
from e8s.show.layout import (
    HOUSEKEEPING_NAMES,
    TEMP_DIR,
)
from e8s.show.layout1 import BLOB_MAX_BYTES, TREE_MAX_BYTES
from e8s.show.manifests import (
    Manifest,
    parsed_manifest,
    require_legacy_bound,
    require_legacy_digest,
)
from e8s.show.show_folders import SHOW_FOLDER_PATTERN, show_folder
from e8s.show.storage import ROOT_REVISION
from e8s.show.torn_reads import is_unfinished_json, is_unfinished_line, read_settled

SLOT_DIGITS = 12
SHOWS_DIR = "shows"
LOG_DIR = "log"
RECORD_DIR = "records"
OBJECT_DIR = "objects"
MANIFEST_FILE = "show.json"
# A slot names at most one changed path per tree entry, so the tree record's cap bounds it too.
SLOT_MAX_BYTES = TREE_MAX_BYTES
# A log call reads every slot after `since` and its record. The bytes it reads are capped at the
# size one blob may have; a longer history is read in pieces with since.
LOG_READ_MAX_BYTES = BLOB_MAX_BYTES
# A manifest holds one show ID; stored layout-1 IDs have no length rule, but a folder name of
# theirs never passed 255 characters. Rounded up.
MANIFEST_MAX_BYTES = 4096


class FileBackend:
    """Stores shows under `<root>/shows/<folder>/` as numbered slots, records and blobs.

    Slot `n` (`log/000000000001` and on) holds the n-th revision as one line of canonical JSON.
    A commit writes its blobs and its record, then claims slot `parent seq + 1` with an exclusive
    link, or an exclusive create where the file system cannot hard-link: the writer that creates
    the slot lands, every other writer gets ConflictError. The backend remembers the last slot it
    saw per show, so a warm head() looks up one name.

    Attributes:
        root: Repository root folder.

    >>> import tempfile
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.document_repository import DocumentRepository
    >>> at = datetime(2026, 11, 24, tzinfo=UTC)
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     repo = DocumentRepository(FileBackend(Path(folder)))
    ...     landed = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    ...     names = sorted(entry.name for entry in Path(folder).iterdir())
    ...     names, FileBackend(Path(folder)).head("show_1") == landed.id
    (['layout.json', 'shows', 'tmp'], True)
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._seen: dict[str, tuple[int, str]] = {}
        self._manifests: dict[str, Manifest] = {}
        self._legacy_checked: set[str] = set()
        self._has_layout = False
        self._flushed: set[str] = set()
        self._placer = FilePlacer(root / TEMP_DIR)

    def _folder(self, show_id: str) -> Path:
        return self.root / SHOWS_DIR / show_folder(show_id)

    def _slot_path(self, show_id: str, seq: int) -> Path:
        return self._folder(show_id) / LOG_DIR / f"{seq:0{SLOT_DIGITS}d}"

    def _record_path(self, show_id: str, revision_id: str) -> Path:
        return self._folder(show_id) / RECORD_DIR / f"{revision_id}.json"

    def _read(
        self, show_id: str, path: Path, kind: str, cap: int, budget: ReadBudget | None = None
    ) -> JsonValue:
        """Reads and parses one stored JSON file, mapping every refusal to CORRUPT."""
        return parsed_json(show_id, path, kind, self._read_raw(show_id, path, kind, cap, budget))

    def _read_raw(
        self, show_id: str, path: Path, kind: str, cap: int, budget: ReadBudget | None
    ) -> bytes:
        """Reads a stored file; one still being filled returns what it held, for its checks."""
        fail = partial(corrupt, show_id)
        raw = read_capped(path, kind, cap, fail, allows_growth=True)
        if budget is not None:
            budget.spend(len(raw))
        return raw

    def _manifest(self, show_id: str, folder: Path) -> Manifest:
        """Reads a manifest: the show ID its folder must encode and its legacy prefix."""
        path = folder / MANIFEST_FILE
        read = partial(self._read_raw, show_id, path, "manifest", MANIFEST_MAX_BYTES, None)
        stored = parsed_json(show_id, path, "manifest", read_settled(read, is_unfinished_json))
        return parsed_manifest(show_id, path, stored)

    def _check_manifest(self, show_id: str) -> None:
        """Checks once per backend that no folder is a link and the manifest names this show."""
        if show_id in self._manifests:
            return
        self._require_plain_folders(show_id)
        folder = self._folder(show_id)
        if lstat_mode(show_id, folder / MANIFEST_FILE) is None:
            return
        manifest = self._manifest(show_id, folder)
        if manifest.show_id != show_id:
            raise corrupt(show_id, folder / MANIFEST_FILE, "manifest names another show")
        self._manifests[show_id] = manifest

    def _legacy_before(self, show_id: str) -> int:
        return self._manifests.get(show_id, Manifest(show_id)).legacy_before

    def _require_legacy_bound(self, show_id: str, last: int | None = None) -> None:
        """Refuses a manifest whose `legacy_before` passes the slot after the last slot.

        Only an upgraded show's manifest names legacy slots, so no other show looks anything up.
        """
        legacy_before = self._legacy_before(show_id)
        if legacy_before == FIRST_SEQ:
            return
        seq = self._head(show_id)[0] if last is None else last
        require_legacy_bound(show_id, self._folder(show_id) / MANIFEST_FILE, legacy_before, seq)

    def _require_legacy_prefix(self, show_id: str, read: list[Revision] | None = None) -> None:
        """Checks once per backend that the legacy slots hold the IDs the manifest's digest names.

        `read` holds the revisions of the slots from the first on, when a log already read them.
        """
        manifest = self._manifests.get(show_id)
        if manifest is None or not manifest.legacy_digest or show_id in self._legacy_checked:
            return
        legacy = range(FIRST_SEQ, manifest.legacy_before)
        ids = [r.id for r in read or [self._read_slot(show_id, seq) for seq in legacy]]
        path = self._folder(show_id) / MANIFEST_FILE
        require_legacy_digest(show_id, path, manifest, ids[: len(legacy)])
        self._legacy_checked.add(show_id)

    def _require_plain_folders(self, show_id: str) -> None:
        """Refuses a symlink or junction in place of any folder a read or write passes through."""
        folder = self._folder(show_id)
        require_plain_folders(
            show_id,
            self.root / SHOWS_DIR,
            folder,
            folder / LOG_DIR,
            folder / RECORD_DIR,
            folder / OBJECT_DIR,
            self.root / TEMP_DIR,
        )

    def shows(self) -> list[str]:
        """Returns the IDs of shows with at least one slot, read from their manifests."""
        folder = self.root / SHOWS_DIR
        with mapped_io("", folder):
            mode = lstat_mode("", folder)
            if mode is None:
                return []
            refuse_redirect(folder, mode)
            names = sorted(p.name for p in folder.iterdir() if p.name not in HOUSEKEEPING_NAMES)
            return [show for show in map(self._listed_show, names) if show is not None]

    def _listed_show(self, name: str) -> str | None:
        """Returns the show a folder under shows/ holds, or None before its first slot."""
        folder = self.root / SHOWS_DIR / name
        if not SHOW_FOLDER_PATTERN.fullmatch(name):
            raise corrupt("", folder, "entry under shows/ is not a show folder")
        mode = lstat_mode("", folder)
        if mode is not None:
            refuse_redirect(folder, mode)
        if lstat_mode("", folder / MANIFEST_FILE) is None:
            return None
        show_id = self._manifest("", folder).show_id
        return show_id if self._has_slot(show_id, 1) else None

    def _has_slot(self, show_id: str, seq: int) -> bool:
        return lstat_mode(show_id, self._slot_path(show_id, seq)) is not None

    def _read_slot(self, show_id: str, seq: int, budget: ReadBudget | None = None) -> Revision:
        """Reads a slot, rereading it while it is not yet one finished line."""
        path = self._slot_path(show_id, seq)
        read = partial(self._read_raw, show_id, path, "slot", SLOT_MAX_BYTES, budget)
        return parsed_slot(show_id, path, read_settled(read, is_unfinished_line))

    def _remember(self, show_id: str, seq: int, revision_id: str) -> None:
        if seq >= self._seen.get(show_id, (0, ROOT_REVISION))[0]:
            self._seen[show_id] = (seq, revision_id)

    def head(self, show_id: str) -> str:
        """Returns the revision in the last slot, or the root revision before the first.

        The manifest is checked once per backend, as for every other read, so a warm head still
        looks up one slot. A warm head does not look for a gap after the slot it remembers; the
        next commit does, and so does log().
        """
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            seq, revision_id = self._head(show_id)
            self._require_legacy_bound(show_id, seq)
            return revision_id

    def _head(self, show_id: str) -> tuple[int, str]:
        """Finds the last slot: one lookup when warm, a gallop and a bisection otherwise.

        Whenever the search ran, the slot after the next is looked up too, so a deleted slot right
        after the last one found is reported instead of rolling the head back.
        """
        seen = self._seen.get(show_id)
        seq, revision_id = seen or (0, ROOT_REVISION)
        if not self._has_slot(show_id, seq + 1):
            if seen is None:
                self._require_no_gap(show_id, seq + 1)
                self._remember(show_id, seq, revision_id)
            return seq, revision_id
        last = last_present(partial(self._has_slot, show_id), seq)
        self._require_no_gap(show_id, last + 1)
        found = self._read_slot(show_id, last)
        self._remember(show_id, last, found.id)
        return last, found.id

    def _require_no_gap(self, show_id: str, seq: int) -> None:
        """Raises CORRUPT when slot `seq` is missing and slot `seq + 1` is present.

        The later slot is looked up first: a slot is only created after the one before it, so
        once the later one is seen, a missing earlier one is a gap and never a race.
        """
        if self._has_slot(show_id, seq + 1) and not self._has_slot(show_id, seq):
            path = self._slot_path(show_id, seq)
            raise corrupt(show_id, path, f"slot {path.name} is missing before a later slot")

    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        """Returns the revisions after `since`, oldest first, reading only the later slots.

        A revision no slot holds, such as the record of a commit that lost its race, raises
        UnknownRefError. Every slot read is checked against its record, so the read costs the
        bytes of the slots and records after `since`, up to LOG_READ_MAX_BYTES, and a history of
        more than MAX_REVISIONS revisions is refused.
        """
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            last, head = self._head(show_id)
            self._require_legacy_bound(show_id, last)
            if last > MAX_REVISIONS:
                raise history_too_long_error(show_id, MAX_REVISIONS)
            if since == head:
                return []
            first = 0 if since == ROOT_REVISION else self._landed_seq(show_id, since, last)
            landed = self._slots_after(show_id, first, last, since)
            if first < self._legacy_before(show_id) - FIRST_SEQ:
                self._require_legacy_prefix(show_id, landed if first == 0 else None)
            return landed

    def _landed_seq(self, show_id: str, revision_id: str, last: int) -> int:
        """Returns the slot number of a landed revision, read from its record and its slot."""
        if self._has_record(show_id, revision_id):
            seq = self._record(show_id, revision_id).seq
            if seq <= last and self._read_slot(show_id, seq).id == revision_id:
                return seq
        raise unknown_revision_error(show_id, revision_id, [])

    def _has_record(self, show_id: str, revision_id: str) -> bool:
        """Tells whether a revision ID is well formed and has a record, raising when it is lost.

        A record a stopped gc holds aside, and a missing record the head slot names, are damage
        fsck reports, never an unknown revision; one confirmed gc puts a set-aside record back.
        """
        if not REVISION_ID_PATTERN.fullmatch(revision_id):
            return False
        path = self._record_path(show_id, revision_id)
        if lstat_mode(show_id, path) is not None:
            return True
        if lstat_mode(show_id, path.with_name(SET_ASIDE_PREFIX + path.name)) is not None:
            raise missing_file(show_id, path, "the revision's record is set aside")
        if self._head(show_id)[1] == revision_id:
            raise missing_file(show_id, path, "the head slot names a revision that has no record")
        return False

    def _slots_after(self, show_id: str, first: int, last: int, parent: str) -> list[Revision]:
        """Reads slots `first + 1` to `last`, checking each against its parent and its record."""
        budget = ReadBudget(show_id, LOG_READ_MAX_BYTES)
        landed: list[Revision] = []
        for seq in range(first + 1, last + 1):
            revision = self._read_slot(show_id, seq, budget)
            path = self._slot_path(show_id, seq)
            if revision.parent != parent:
                raise corrupt(show_id, path, "slot's parent is not the revision of the slot before")
            self._check_slot_record(show_id, path, seq, revision, budget)
            landed.append(revision)
            parent = revision.id
        return landed

    def _check_slot_record(
        self, show_id: str, path: Path, seq: int, revision: Revision, budget: ReadBudget
    ) -> None:
        """Requires the slot's revision to have a record that holds the same revision and seq."""
        if lstat_mode(show_id, self._record_path(show_id, revision.id)) is None:
            raise missing_file(show_id, path, "slot names a revision that has no record")
        require_slot_record(
            show_id, path, seq, revision, self._record(show_id, revision.id, budget)
        )

    def _record(self, show_id: str, revision_id: str, budget: ReadBudget | None = None) -> Record:
        """Reads a revision record, checking it names itself and that its ID is verified."""
        path = self._record_path(show_id, revision_id)
        stored = self._read(show_id, path, "revision record", TREE_MAX_BYTES, budget)
        legacy_before = self._legacy_before(show_id)
        return parsed_record(show_id, path, stored, revision_id, legacy_before)

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        """Returns path to blob digest at a landed revision, read from its record."""
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            self._require_legacy_bound(show_id)
            if self._has_record(show_id, revision_id):
                stored = self._record(show_id, revision_id)
                if self._has_slot(show_id, stored.seq) and (
                    self._read_slot(show_id, stored.seq).id == revision_id
                ):
                    if stored.seq < self._legacy_before(show_id):
                        self._require_legacy_prefix(show_id)
                    return stored.tree
            raise unknown_revision_error(show_id, revision_id, [])

    def blob(self, show_id: str, digest: str) -> bytes:
        """Returns the bytes of a blob after checking they hash to its digest."""
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            objects = self._folder(show_id) / OBJECT_DIR
            if not REVISION_ID_PATTERN.fullmatch(digest):
                raise corrupt(show_id, objects, f"{digest!r} is not a 64-digit hex id")
            if lstat_mode(show_id, objects / digest) is None:
                raise missing_file(show_id, objects / digest, "a tree names this blob")
            return self._verified_blob(show_id, objects / digest)

    def _verified_blob(self, show_id: str, path: Path) -> bytes:
        """Reads a stored object, raising CORRUPT unless its bytes hash to its file name."""
        data = read_capped(
            path, "blob", BLOB_MAX_BYTES, lambda source, why: corrupt(show_id, source, why)
        )
        require_blob_digest(show_id, path, data)
        return data

    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        """Writes the blobs and the record, then lands the revision by claiming its slot.

        Raises ConflictError, listing what landed, when the parent is no longer the head.
        """
        with mapped_io(show_id, self._folder(show_id)):
            self._require_plain_folders(show_id)
            self._check_manifest(show_id)
            require_caller_ids(show_id, revision, tree, blobs)
            require_readable_sizes(show_id, tree, blobs)
            seq = self._parent_seq(show_id, revision.parent) + 1
            self._require_legacy_bound(show_id)
            self._prepare(show_id)
            remove_stale_temps(self.root / TEMP_DIR, revision.at - STALE_TEMP_AGE)
            self._store_blobs(show_id, blobs)
            self._store_record(show_id, revision, seq, tree)
            folder = self._folder(show_id)
            flush_directories(folder / OBJECT_DIR, folder / RECORD_DIR)
            try:
                self._claim_slot(show_id, revision, seq)
            except ConflictError:
                winner = self._read_slot(show_id, seq).id
                remove_lost_record(self._record_path(show_id, revision.id), winner, revision.id)
                raise
            self._remember(show_id, seq, revision.id)

    def _parent_seq(self, show_id: str, parent: str) -> int:
        """Returns the slot number of the commit's parent; an unlanded parent is a conflict."""
        if parent == ROOT_REVISION:
            return 0
        seen = self._seen.get(show_id)
        if seen is not None and seen[1] == parent:
            return seen[0]
        try:
            return self._landed_seq(show_id, parent, self._head(show_id)[0])
        except UnknownRefError:
            raise conflict_error(show_id, parent, self.log(show_id)) from None

    def _prepare(self, show_id: str) -> None:
        """Writes what is missing of the layout file, folders and manifest, and flushes them.

        Whichever process placed an entry, every process flushes the folders that hold them once
        per show, so a commit never returns while an entry it relies on is still volatile.
        """
        self._ensure_layout(show_id)
        if show_id in self._flushed:
            return
        folder = self._folder(show_id)
        for name in (LOG_DIR, RECORD_DIR, OBJECT_DIR):
            durable_io.make_folder(folder / name)
        if show_id not in self._manifests:
            check = partial(self._manifest, show_id, folder)
            manifest, data = folder / MANIFEST_FILE, dump_json({"id": show_id})
            if place_or_check(self._placer, show_id, manifest, data, check):
                self._manifests[show_id] = Manifest(show_id)
            else:
                self._check_manifest(show_id)
        durable_io.flush_directory(self.root, follows_links=True)
        flush_directories(self.root / SHOWS_DIR, folder)
        self._flushed.add(show_id)

    def _ensure_layout(self, show_id: str) -> None:
        """Links `layout.json` into place, after `tmp/` and before anything else is written."""
        if self._has_layout:
            return
        write_layout_file(self.root, show_id, self._placer)
        self._has_layout = True

    def _store_blobs(self, show_id: str, blobs: Mapping[str, bytes]) -> None:
        """Stores each blob, keeping one already there only when it holds the same bytes."""
        store_blobs(self._placer, show_id, self._folder(show_id) / OBJECT_DIR, blobs)

    def _store_record(
        self, show_id: str, revision: Revision, seq: int, tree: Mapping[str, str]
    ) -> None:
        """Places the revision record, or checks the one an identical commit placed first."""
        record = record_bytes(revision, seq, tree)
        store_record(self._placer, show_id, self._record_path(show_id, revision.id), record)

    def _claim_slot(self, show_id: str, revision: Revision, seq: int) -> None:
        """Claims slot `seq`; a later slot without this one is a gap and is never refilled."""
        self._require_no_gap(show_id, seq)
        slot = slot_bytes(revision)
        path = self._slot_path(show_id, seq)
        check = partial(self._read_slot, show_id, seq)
        if not place_or_check(self._placer, show_id, path, slot, check):
            raise conflict_error(show_id, revision.parent, self.log(show_id, since=revision.parent))
        flush_directories(path.parent, is_commit_point=True)
