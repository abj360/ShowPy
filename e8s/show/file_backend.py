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
from pathlib import Path

from e8s.core.canonical import jcs
from e8s.core.errors import SchemaError, UnknownRefError, VersionError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show import document_repository
from e8s.show.backend import (
    MAX_REVISIONS,
    conflict_error,
    history_too_long_error,
    unknown_revision_error,
)
from e8s.show.backend_checks import ReadBudget, Record, corrupt, require_caller_ids
from e8s.show.document_dump import dump_json
from e8s.show.durable_io import (
    is_redirect,
    link_new,
    lstat_mode,
    make_folder,
    mapped_io,
    read_capped,
    remove_file,
    write_temp,
)
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.layout import (
    CURRENT_LAYOUT,
    HOUSEKEEPING_NAMES,
    LAYOUT_FILE,
    LAYOUT_FORMAT,
    TEMP_DIR,
    read_layout,
)
from e8s.show.layout1 import (
    BLOB_MAX_BYTES,
    REVISION_ID_PATTERN,
    TREE_MAX_BYTES,
    _require_readable_sizes,
)
from e8s.show.show_folders import SHOW_FOLDER_PATTERN, show_folder
from e8s.show.storage import ROOT_REVISION

SLOT_DIGITS = 12
SHOWS_DIR = "shows"
LOG_DIR = "log"
RECORD_DIR = "records"
OBJECT_DIR = "objects"
MANIFEST_FILE = "show.json"
SLOT_KEYS = frozenset({"id", "parent", "at", "changed"})
RECORD_KEYS = frozenset({"revision", "seq", "tree"})
MANIFEST_KEYS = frozenset({"id", "legacy_before"})
FIRST_SEQ = 1
# A slot names at most one changed path per tree entry, so the tree record's cap bounds it too.
SLOT_MAX_BYTES = TREE_MAX_BYTES
# A log read holds every slot and record it reads in memory at once; one blob may be this large,
# so a history may be too, and a larger one is read in pieces with since.
LOG_READ_MAX_BYTES = BLOB_MAX_BYTES
# A manifest holds one show ID; stored layout-1 IDs have no length rule, but a folder name of
# theirs never passed 255 characters. Rounded up.
MANIFEST_MAX_BYTES = 4096
LAYOUT_BYTES = dump_json({"format": LAYOUT_FORMAT, "layout": CURRENT_LAYOUT})


class FileBackend:
    """Stores shows under `<root>/shows/<folder>/` as numbered slots, records and blobs.

    Slot `n` (`log/000000000001` and on) holds the n-th revision as one line of canonical JSON.
    A commit writes its blobs and its record, then claims slot `parent seq + 1` with an exclusive
    link: the writer that creates the slot lands, every other writer gets ConflictError. The
    backend remembers the last slot it saw per show, so a warm head() looks up one name.

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
        self._manifests: dict[str, int] = {}
        self._has_layout = False

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
        raw = read_capped(path, kind, cap, lambda source, why: corrupt(show_id, source, why))
        if budget is not None:
            budget.spend(len(raw))
        try:
            return parse_strict_json(raw, document=kind)
        except SchemaError as error:
            raise corrupt(show_id, path, f"{kind} is not valid JSON") from error

    def _revision(self, show_id: str, path: Path, stored: JsonValue) -> Revision:
        try:
            revision = Revision._validated(stored)
        except SchemaError as error:
            raise corrupt(show_id, path, "stored revision is malformed") from error
        ids = [revision.id] if revision.parent == ROOT_REVISION else [revision.id, revision.parent]
        if not all(REVISION_ID_PATTERN.fullmatch(value) for value in ids):
            raise corrupt(show_id, path, "stored revision ID is not a 64-digit hex id")
        return revision

    def _manifest(self, show_id: str, folder: Path) -> tuple[str, int]:
        """Reads a manifest: the show ID its folder must encode and its `legacy_before` slot."""
        path = folder / MANIFEST_FILE
        stored = self._read(show_id, path, "manifest", MANIFEST_MAX_BYTES)
        if not isinstance(stored, dict) or not {"id"} <= stored.keys() <= MANIFEST_KEYS:
            raise corrupt(show_id, path, "manifest must hold id and may hold legacy_before")
        listed, legacy_before = stored["id"], stored.get("legacy_before", FIRST_SEQ)
        if not isinstance(listed, str) or show_folder(listed) != folder.name:
            raise corrupt(show_id, path, "manifest names a show stored in another folder")
        if (
            not isinstance(legacy_before, int)
            or isinstance(legacy_before, bool)
            or legacy_before < 1
        ):
            raise corrupt(show_id, path, "manifest legacy_before is not a positive integer")
        return listed, legacy_before

    def _check_manifest(self, show_id: str) -> None:
        """Checks once per backend that no folder is a link and the manifest names this show."""
        if show_id in self._manifests:
            return
        self._require_plain_folders(show_id)
        folder = self._folder(show_id)
        if lstat_mode(show_id, folder / MANIFEST_FILE) is None:
            return
        listed, legacy_before = self._manifest(show_id, folder)
        if listed != show_id:
            raise corrupt(show_id, folder / MANIFEST_FILE, "manifest names another show")
        self._manifests[show_id] = legacy_before

    def _require_plain_folders(self, show_id: str) -> None:
        """Refuses a symlink or junction in place of any folder a read or write passes through."""
        folder = self._folder(show_id)
        for path in (
            self.root / SHOWS_DIR,
            folder,
            folder / LOG_DIR,
            folder / RECORD_DIR,
            folder / OBJECT_DIR,
            self.root / TEMP_DIR,
        ):
            mode = lstat_mode(show_id, path)
            if mode is not None and is_redirect(path, mode):
                raise corrupt(show_id, path, "folder is a symbolic link or junction")

    def shows(self) -> list[str]:
        """Returns the IDs of shows with at least one slot, read from their manifests."""
        folder = self.root / SHOWS_DIR
        with mapped_io("", folder):
            if lstat_mode("", folder) is None:
                return []
            names = sorted(p.name for p in folder.iterdir() if p.name not in HOUSEKEEPING_NAMES)
            return [show for show in map(self._listed_show, names) if show is not None]

    def _listed_show(self, name: str) -> str | None:
        """Returns the show a folder under shows/ holds, or None before its first slot."""
        folder = self.root / SHOWS_DIR / name
        if not SHOW_FOLDER_PATTERN.fullmatch(name):
            raise corrupt("", folder, "entry under shows/ is not a show folder")
        if lstat_mode("", folder / MANIFEST_FILE) is None:
            return None
        show_id, _ = self._manifest("", folder)
        return show_id if self._has_slot(show_id, 1) else None

    def _has_slot(self, show_id: str, seq: int) -> bool:
        return lstat_mode(show_id, self._slot_path(show_id, seq)) is not None

    def _read_slot(self, show_id: str, seq: int, budget: ReadBudget | None = None) -> Revision:
        path = self._slot_path(show_id, seq)
        stored = self._read(show_id, path, "slot", SLOT_MAX_BYTES, budget)
        if not isinstance(stored, dict) or stored.keys() != SLOT_KEYS:
            raise corrupt(show_id, path, f"slot must hold exactly {sorted(SLOT_KEYS)}")
        return self._revision(show_id, path, stored)

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
            return self._head(show_id)[1]

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
        last = self._last_slot(show_id, seq)
        self._require_no_gap(show_id, last + 1)
        found = self._read_slot(show_id, last)
        self._remember(show_id, last, found.id)
        return last, found.id

    def _last_slot(self, show_id: str, seq: int) -> int:
        """Gallops from slot `seq + 1`, known present, and bisects back to the last present."""
        present, step = seq + 1, 2
        while self._has_slot(show_id, seq + step):
            present, step = seq + step, step * 2
        absent = seq + step
        while absent - present > 1:
            middle = (present + absent) // 2
            if self._has_slot(show_id, middle):
                present = middle
            else:
                absent = middle
        return present

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
            if last > MAX_REVISIONS:
                raise history_too_long_error(show_id, MAX_REVISIONS)
            if since == head:
                return []
            first = 0 if since == ROOT_REVISION else self._landed_seq(show_id, since, last)
            return self._slots_after(show_id, first, last, since)

    def _landed_seq(self, show_id: str, revision_id: str, last: int) -> int:
        """Returns the slot number of a landed revision, read from its record and its slot."""
        is_known = REVISION_ID_PATTERN.fullmatch(revision_id) and lstat_mode(
            show_id, self._record_path(show_id, revision_id)
        )
        if is_known:
            seq = self._record(show_id, revision_id).seq
            if seq <= last and self._read_slot(show_id, seq).id == revision_id:
                return seq
        raise unknown_revision_error(show_id, revision_id, [])

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
            raise corrupt(show_id, path, "slot names a revision that has no record")
        stored = self._record(show_id, revision.id, budget)
        if stored.seq != seq:
            raise corrupt(show_id, path, f"record of this slot holds seq {stored.seq}, not {seq}")
        if stored.revision != revision:
            raise corrupt(show_id, path, "slot differs from the revision in its record")

    def _record(self, show_id: str, revision_id: str, budget: ReadBudget | None = None) -> Record:
        """Reads a revision record, checking it names itself and that its ID is verified."""
        path = self._record_path(show_id, revision_id)
        stored = self._read(show_id, path, "revision record", TREE_MAX_BYTES, budget)
        if not isinstance(stored, dict) or stored.keys() != RECORD_KEYS:
            raise corrupt(show_id, path, f"record must hold exactly {sorted(RECORD_KEYS)}")
        seq, tree = stored["seq"], stored["tree"]
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise corrupt(show_id, path, "record seq is not a positive integer")
        revision = self._revision(show_id, path, stored["revision"])
        if revision.id != revision_id:
            raise corrupt(show_id, path, "record holds another revision than its name")
        if not isinstance(tree, dict) or not all(
            isinstance(value, str) and REVISION_ID_PATTERN.fullmatch(value)
            for value in tree.values()
        ):
            raise corrupt(show_id, path, "tree is not a mapping of paths to digests")
        checked = {key: str(value) for key, value in tree.items()}
        self._require_scheme_id(show_id, path, seq, revision, checked)
        return Record(seq, revision, checked)

    def _require_scheme_id(
        self, show_id: str, path: Path, seq: int, revision: Revision, tree: Mapping[str, str]
    ) -> None:
        """Requires the ID to be recomputable from the record, except before `legacy_before`."""
        if seq < self._manifests.get(show_id, FIRST_SEQ):
            return
        changes = {changed: tree.get(changed) for changed in revision.changed}
        if document_repository.revision_id(revision.parent, revision.at, changes) != revision.id:
            raise corrupt(show_id, path, "record does not hash to its name")

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        """Returns path to blob digest at a landed revision, read from its record."""
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            path = self._record_path(show_id, revision_id)
            if REVISION_ID_PATTERN.fullmatch(revision_id) and lstat_mode(show_id, path):
                stored = self._record(show_id, revision_id)
                if self._has_slot(show_id, stored.seq) and (
                    self._read_slot(show_id, stored.seq).id == revision_id
                ):
                    return stored.tree
            raise unknown_revision_error(show_id, revision_id, [])

    def blob(self, show_id: str, digest: str) -> bytes:
        """Returns the bytes of a blob after checking they hash to its digest."""
        with mapped_io(show_id, self._folder(show_id)):
            self._check_manifest(show_id)
            objects = self._folder(show_id) / OBJECT_DIR
            if not REVISION_ID_PATTERN.fullmatch(digest):
                raise corrupt(show_id, objects, f"{digest!r} is not a 64-digit hex id")
            return self._verified_blob(show_id, objects / digest)

    def _verified_blob(self, show_id: str, path: Path) -> bytes:
        """Reads a stored object, raising CORRUPT unless its bytes hash to its file name."""
        data = read_capped(
            path, "blob", BLOB_MAX_BYTES, lambda source, why: corrupt(show_id, source, why)
        )
        if blob_digest(data) != path.name:
            raise corrupt(show_id, path, "blob bytes do not match their digest")
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
            _require_readable_sizes(show_id, tree, blobs)
            seq = self._parent_seq(show_id, revision.parent) + 1
            self._prepare(show_id)
            self._store_blobs(show_id, blobs)
            self._store_record(show_id, revision, seq, tree)
            self._claim_slot(show_id, revision, seq)
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
        """Writes the layout file, the show's folders and its manifest when missing."""
        self._ensure_layout(show_id)
        if show_id in self._manifests:
            return
        folder = self._folder(show_id)
        for name in (LOG_DIR, RECORD_DIR, OBJECT_DIR):
            make_folder(folder / name)
        if self._place(show_id, folder / MANIFEST_FILE, dump_json({"id": show_id})):
            self._manifests[show_id] = FIRST_SEQ
        else:
            self._check_manifest(show_id)

    def _ensure_layout(self, show_id: str) -> None:
        """Links `layout.json` into place, after `tmp/` and before anything else is written."""
        if self._has_layout:
            return
        if read_layout(self.root) != CURRENT_LAYOUT:
            raise VersionError(
                f"repository at {self.root} is layout 1, which this backend does not write",
                hint="open it with FileRepository, which reads layout 1",
                context={"layout": 1, "supported": CURRENT_LAYOUT, "root": str(self.root)},
            )
        make_folder(self.root / TEMP_DIR)
        if not self._place(show_id, self.root / LAYOUT_FILE, LAYOUT_BYTES):
            read_layout(self.root)
        self._has_layout = True

    def _place(self, show_id: str, target: Path, data: bytes) -> bool:
        """Writes `data` under `target` through a temp file; False when the name was taken."""
        temp = write_temp(self.root / TEMP_DIR, data)
        try:
            with mapped_io(show_id, target, is_link=True):
                return link_new(temp, target)
        finally:
            remove_file(temp)

    def _store_blobs(self, show_id: str, blobs: Mapping[str, bytes]) -> None:
        """Stores each blob, keeping one already there only when it holds the same bytes."""
        objects = self._folder(show_id) / OBJECT_DIR
        for digest, data in blobs.items():
            target = objects / digest
            if lstat_mode(show_id, target) is None and self._place(show_id, target, data):
                continue
            self._verified_blob(show_id, target)

    def _store_record(
        self, show_id: str, revision: Revision, seq: int, tree: Mapping[str, str]
    ) -> None:
        """Places the revision record, or checks the one an identical commit placed first."""
        path = self._record_path(show_id, revision.id)
        record = dump_json({"revision": revision.to_json(), "seq": seq, "tree": dict(tree)})
        if self._place(show_id, path, record):
            return
        stored = read_capped(
            path, "revision record", TREE_MAX_BYTES, lambda p, why: corrupt(show_id, p, why)
        )
        if stored != record:
            raise corrupt(show_id, path, "revision record differs from this commit")

    def _claim_slot(self, show_id: str, revision: Revision, seq: int) -> None:
        """Claims slot `seq`; a later slot without this one is a gap and is never refilled."""
        self._require_no_gap(show_id, seq)
        slot = jcs(revision.to_json()) + b"\n"
        if not self._place(show_id, self._slot_path(show_id, seq), slot):
            raise conflict_error(show_id, revision.parent, self.log(show_id, since=revision.parent))
