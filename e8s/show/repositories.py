"""
repositories.py --- the shared repository logic, the in-memory repository and asset store

Contains:
    RepositoryBackend: stores revisions, trees and blobs; commits compare-and-swap on the head
    revisions_since(): lists the revisions of a log that landed after a parent revision
    conflict_error(): builds the ConflictError that lists the revisions that landed meanwhile
    DocumentRepository: implements Repository over a backend of revisions, trees and blobs
    InMemoryRepository: keeps every revision in process memory
    InMemoryAssetStore: keeps assets in process memory under content-hash URIs
"""

import functools
import re
import threading
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, override

from e8s.choreo.form_models import Form
from e8s.choreo.match_ops import forms_of_set
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    ConflictError,
    SchemaError,
    StorageError,
    UnknownRefError,
    bounded_repr,
    join_listed,
)
from e8s.core.ids import WINDOWS_DEVICE_NAMES, SetId, is_device_name
from e8s.core.util import ASSET_PREFIX, asset_uri, close_matches, content_hash
from e8s.show.changes import require_form_owner
from e8s.show.decode_cache import DecodeCache
from e8s.show.documents import (
    decode_show,
    migrated_view,
    migration_changes,
    script_doc,
    split_path,
)
from e8s.show.files import LIMITS_DOC, WAIVERS_DOC, show_changes
from e8s.show.integrity import (
    LazyBlobs,
    blob_digest,
    require_known_path,
    require_valid_digests,
)
from e8s.show.models import Revision, Show
from e8s.show.set_keys import set_key
from e8s.show.storage import ROOT_REVISION
from e8s.show.structured import decode_text
from e8s.time.models import Timeline

SHOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
# Frozen at the old library name: every stored revision ID is hashed under it.
REVISION_HASH_DOMAIN = "showpy.revision"
SHORT_ID = 7


def _require_show_id(show_id: str) -> None:
    if not SHOW_ID_PATTERN.fullmatch(show_id) or ".." in show_id:
        raise StorageError(
            f"show id {show_id!r} cannot be stored",
            hint="use letters, digits, _, . and - only, starting with a letter or digit",
            context={"show": show_id},
            code=ErrorCode.BAD_PATH,
        )


def _require_new_show_id(show_id: str) -> None:
    """Refuses a Windows device name for a new show before its folder is looked up.

    On Windows that lookup opens the device itself. Shows already stored under such a name still
    load and take commits on their head.
    """
    if is_device_name(show_id):
        raise StorageError(
            f"show id {show_id!r} is a Windows device name and cannot start a new show",
            hint=f"pick another ID: {', '.join(sorted(WINDOWS_DEVICE_NAMES))}, alone or before a"
            " dot, name devices on Windows",
            context={"show": show_id},
            code=ErrorCode.BAD_PATH,
        )


def digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return blob_digest(data)


class RepositoryBackend(Protocol):
    """Stores revisions, path-to-digest trees and blobs; commit is compare-and-swap on head."""

    def show_ids(self) -> list[str]:
        """Returns the IDs of shows with at least one revision."""
        ...

    def log(self, show_id: str) -> list[Revision]:
        """Returns a show's revisions, oldest first."""
        ...

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        """Returns path to blob digest at a revision."""
        ...

    def blob(self, show_id: str, digest: str) -> bytes:
        """Returns the bytes of a blob."""
        ...

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        """Stores blobs and the tree, then lands the revision only if its parent is the head."""
        ...


def revisions_since(log: list[Revision], parent: str) -> list[Revision]:
    """Lists the revisions of a log that landed after a parent revision.

    Args:
        log: A show's revisions, oldest first.
        parent: Revision a commit was based on; the root revision, or one missing from the log,
            means every revision landed since.

    Returns:
        landed: The revisions after the parent, oldest first.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> log = [Revision(id=i, parent=p, at=at) for i, p in (("a", ""), ("b", "a"), ("c", "b"))]
    >>> [r.id for r in revisions_since(log, "a")], len(revisions_since(log, ""))
    (['b', 'c'], 3)
    """
    ids = [r.id for r in log]
    return log[ids.index(parent) + 1 :] if parent in ids else log


def conflict_error(show_id: str, expected: str, landed: list[Revision]) -> ConflictError:
    """Builds the ConflictError listing the first landed revisions' short ID, time and paths.

    The message lists five revisions, each with its paths cut to 80 characters, and counts the
    rest, so a long run of landed revisions still gives a short message.
    """
    summary = join_listed(
        [
            f"{r.id[:SHORT_ID]} {r.at.isoformat()} ({bounded_repr(', '.join(r.changed))})"
            for r in landed
        ]
    )
    return ConflictError(
        f"{len(landed)} revision(s) landed on {show_id} since {expected[:SHORT_ID] or 'the start'}:"
        f" {summary}",
        hint="re-read at the head revision, reapply the edit and write again",
        context={
            "expected": expected,
            "head": landed[-1].id if landed else expected,
            "changes": [r.to_json() for r in landed],
        },
    )


def _with_stored_key(new_set: Set, stored: Set) -> Set:
    if new_set.key == stored.key:
        return new_set
    if "key" not in new_set.model_fields_set:
        return new_set.replace(key=stored.key)
    raise SchemaError(
        f"set {new_set.id} has key {new_set.key}, but the stored set has key {stored.key}",
        hint="keys never change; leave key unset or pass the stored key",
        context={"set": new_set.id, "key": new_set.key, "stored_key": stored.key},
    )


class DocumentRepository:
    """Implements Repository over a backend that stores revisions, trees and blobs.

    Every write checks validity: known paths, decodable structured files and a show that
    decodes. No write checks pins or locks. Edit functions enforce those for every caller, the
    designer included; a host that writes raw documents owns them.

    Attributes:
        backend: Where revisions, trees and blobs live.
    """

    def __init__(self, backend: RepositoryBackend):
        self.backend = backend
        self._decode_cache = DecodeCache()

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows, sorted."""
        return sorted(self.backend.show_ids())

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision of a show, oldest first."""
        _require_show_id(show_id)
        return self.backend.log(show_id)

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        log = self.revisions(show_id)
        return log[-1].id if log else ROOT_REVISION

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision, raising UnknownRefError with close matches."""
        log = self.revisions(show_id)
        for revision in log:
            if revision.id == revision_id:
                return revision
        close = close_matches(revision_id, [r.id for r in log])
        raise UnknownRefError(
            f"no revision {revision_id[:SHORT_ID] or '(root)'} in show {show_id}",
            hint=f"did you mean {', '.join(c[:SHORT_ID] for c in close)}?"
            if close
            else "list revisions with repo.revisions(show_id)",
            context={"show": show_id, "revision": revision_id},
        )

    def _tree(self, show_id: str, revision: str | None) -> dict[str, str]:
        rev = self.head(show_id) if revision is None else revision
        if rev == ROOT_REVISION:
            return {}
        self.get_revision(show_id, rev)
        return self.backend.tree(show_id, rev)

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        return sorted(self._tree(show_id, revision))

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        digest = self._tree(show_id, revision).get(path)
        return None if digest is None else self.backend.blob(show_id, digest)

    def documents(self, show_id: str, revision: str | None = None) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision (default head), reading blobs on lookup.

        The revision is resolved once; each blob is read the first time a path holding it is
        looked up, so listing paths or decoding the show never loads renders or exports.

        >>> from datetime import UTC
        >>> repo = InMemoryRepository()
        >>> at = datetime(2026, 11, 11, tzinfo=UTC)
        >>> _ = repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision="", at=at)
        >>> repo.documents("s")["/renders/a.txt"]
        b'hi'
        """
        blob = functools.cache(functools.partial(self.backend.blob, show_id))
        return LazyBlobs(self._tree(show_id, revision), blob)

    def _require_head(self, show_id: str, expected: str) -> list[Revision]:
        log = self.revisions(show_id)
        ids = [r.id for r in log]
        if expected != ROOT_REVISION and expected not in ids:
            self.get_revision(show_id, expected)
        landed = revisions_since(log, expected)
        if landed:
            raise conflict_error(show_id, expected, landed)
        return log

    def _require_valid_tree(
        self, show_id: str, old: dict[str, str], new: dict[str, str], added: dict[str, bytes]
    ) -> None:
        def blob(blob_digest: str) -> bytes:
            return (
                added[blob_digest]
                if blob_digest in added
                else self.backend.blob(show_id, blob_digest)
            )

        require_valid_digests(old, new, blob, self._decode_cache)

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes or deletes documents atomically as one revision with a deterministic ID.

        Args:
            show_id: Show written.
            changes: Path to new bytes, or None to delete.
            expected_revision: Head the caller read at; ConflictError when the head moved.
            at: Time stamp.

        Returns:
            revision: The new head, or the unchanged head when nothing differs.

        >>> from datetime import UTC
        >>> repo = InMemoryRepository()
        >>> at = datetime(2026, 11, 11, tzinfo=UTC)
        >>> rev = repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision="", at=at)
        >>> repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision=rev.id, at=at) == rev
        True
        """
        for path in changes:
            split_path(path)
            require_known_path(path)
        if expected_revision == ROOT_REVISION:
            _require_new_show_id(show_id)
        log = self._require_head(show_id, expected_revision)
        old = self._tree(show_id, expected_revision)
        blobs = {digest(d): d for d in changes.values() if d is not None}
        new = {**old, **{p: digest(d) for p, d in changes.items() if d is not None}}
        for path in (p for p, d in changes.items() if d is None):
            new.pop(path, None)
        changed = tuple(sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p)))
        if not changed:
            if not log:
                raise StorageError(
                    f"nothing to save for {show_id}",
                    hint="write at least one document",
                    context={"show": show_id},
                )
            return log[-1]
        body = {
            "parent": expected_revision,
            "at": at.isoformat(),
            "changes": {p: new.get(p) for p in changed},
        }
        revision = Revision(
            id=content_hash(body, domain=REVISION_HASH_DOMAIN),
            parent=expected_revision,
            at=at,
            changed=changed,
        )
        self._require_valid_tree(show_id, old, new, blobs)
        self.backend.commit(show_id, revision, new, blobs)
        return revision

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        rev = self.head(show_id) if revision is None else revision
        docs = self.documents(show_id, rev)
        if not docs:
            close = close_matches(show_id, self.shows())
            raise UnknownRefError(
                f"show {show_id} has no saved revision",
                hint=f"did you mean {', '.join(close)}?" if close else "save it with put_show()",
                context={"show": show_id},
            )
        return decode_show(docs, rev)

    def put_show(
        self,
        show_id: str,
        show: Show,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        return self.transaction(
            show_id,
            show_changes(show, self.documents(show_id, expected_revision)),
            expected_revision=expected_revision,
            at=at,
        )

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        return self.get_show(show_id).set_ids()

    def get_set(self, show_id: str, set_id: str) -> Set:
        """Returns one set."""
        return self.get_show(show_id).set(set_id)

    def put_set(
        self,
        show_id: str,
        new_set: Set,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces one existing set, keeping its stored key; add sets through put_show().

        A set built without a key takes the replaced set's key, so its folder and build script
        stay; an explicit key that differs from the stored one raises SchemaError.
        Checks validity only, never pins or locks; edit functions enforce those.
        """
        show = self.get_show(show_id, expected_revision or None)
        position = show.set_index(new_set.id)
        kept = _with_stored_key(new_set, show.sets[position])
        sets = (*show.sets[:position], kept, *show.sets[position + 1 :])
        return self.put_show(
            show_id,
            show.replace(sets=sets),
            expected_revision=expected_revision,
            at=at,
        )

    def get_script(self, show_id: str, set_id: str) -> str:
        """Returns a set's build script, stored in the folder of the set's key.

        On a head of an older format version the script is read where the first save stores it,
        so a set whose key the migration renamed reads its script from the new folder.
        """
        key = set_key(self.get_set(show_id, set_id))
        source = migrated_view(self.documents(show_id)).get(script_doc(key))
        if source is None:
            raise UnknownRefError(
                f"set {set_id} has no script",
                hint="write one with put_script()",
                context={"set": set_id},
            )
        return decode_text(script_doc(key), source)

    def put_script(
        self,
        show_id: str,
        set_id: str,
        source: str,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's build script, stored in the folder of the set's key.

        On a head of an older format version the migration lands first, in its own revision,
        so the set's files, the script being replaced included, move to the renamed folder and
        stay in history there; the new script follows in the next revision.
        Checks validity only, never pins or locks; edit functions enforce those.
        """
        key = set_key(self.get_show(show_id, expected_revision or None).set(set_id))
        migration = migration_changes(self.documents(show_id, expected_revision or None))
        if migration:
            migrated = self.transaction(
                show_id, migration, expected_revision=expected_revision, at=at
            )
            expected_revision = migrated.id
        return self.transaction(
            show_id,
            {script_doc(key): source.encode()},
            expected_revision=expected_revision,
            at=at,
        )

    def get_paths(self, show_id: str, tid: tuple[str, str]) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        return self.get_show(show_id).transition(tid)

    def put_paths(
        self,
        show_id: str,
        spec: TransitionSpec,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a transition's paths; the destination set's counts win.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        show = self.get_show(show_id, expected_revision or None)
        stored = spec.replace(counts=show.transition(spec.id).counts)
        specs = (*(s for s in show.transitions if s.id != spec.id), stored)
        return self.put_show(
            show_id,
            show.replace(transitions=specs),
            expected_revision=expected_revision,
            at=at,
        )

    def get_forms(self, show_id: str, set_id: str) -> tuple[Form, ...]:
        """Returns the forms a set lists, sorted by ID; stale forms stay out."""
        show = self.get_show(show_id)
        return tuple(f for _, f in sorted(forms_of_set(show, set_id).items()))

    def put_forms(
        self,
        show_id: str,
        set_id: str,
        forms: tuple[Form, ...],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's forms; every form must name that set. Stale forms are kept.

        A form ID that is stored under another set raises SchemaError.

        A set that lists forms lists the put forms afterwards, so `get_forms` returns them.
        Checks validity only, never pins or locks; edit functions enforce those.
        """
        wrong = [f.id for f in forms if f.set != set_id]
        if wrong:
            raise SchemaError(
                f"forms {', '.join(wrong)} do not belong to set {set_id}",
                hint=f"set form.set to {set_id!r}",
                context={"set": set_id, "forms": wrong},
            )
        show = self.get_show(show_id, expected_revision or None)
        for put in forms:
            require_form_owner(show, put)
        replaced = forms_of_set(show, set_id)
        kept = {k: f for k, f in show.forms.items() if k not in replaced}
        edited = show.replace(forms={**kept, **{f.id: f for f in forms}})
        if show.set(set_id).forms and forms:
            listed = show.set(set_id).replace(forms=tuple(f.id for f in forms))
            edited = edited.replace(
                sets=tuple(listed if s.id == set_id else s for s in edited.sets)
            )
        return self.put_show(
            show_id,
            edited,
            expected_revision=expected_revision,
            at=at,
        )

    def get_timeline(self, show_id: str) -> Timeline:
        """Returns the show timeline."""
        return self.get_show(show_id).timeline

    def get_limits(self, show_id: str) -> str:
        """Returns the designer's limits YAML text; empty when none is stored."""
        return decode_text(LIMITS_DOC, self.read(show_id, LIMITS_DOC) or b"")

    def get_waivers(self, show_id: str) -> str:
        """Returns the designer's waivers YAML text; empty when none is stored."""
        return decode_text(WAIVERS_DOC, self.read(show_id, WAIVERS_DOC) or b"")


class _MemoryBackend(RepositoryBackend):
    def __init__(self) -> None:
        self._logs: dict[str, list[Revision]] = {}
        self._trees: dict[tuple[str, str], dict[str, str]] = {}
        self._blobs: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def show_ids(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def log(self, show_id: str) -> list[Revision]:
        with self._lock:
            return list(self._logs.get(show_id, []))

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._trees[(show_id, revision_id)])

    @override
    def blob(self, show_id: str, digest: str) -> bytes:
        with self._lock:
            return self._blobs[digest]

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        with self._lock:
            log = self._logs.setdefault(show_id, [])
            if (log[-1].id if log else ROOT_REVISION) != revision.parent:
                raise conflict_error(
                    show_id, revision.parent, revisions_since(log, revision.parent)
                )
            self._blobs.update(blobs)
            self._trees[(show_id, revision.id)] = dict(tree)
            log.append(revision)


class InMemoryRepository(DocumentRepository):
    """Keeps every revision in process memory; for tests and single-process sessions."""

    def __init__(self) -> None:
        super().__init__(_MemoryBackend())


class InMemoryAssetStore:
    """Keeps assets in process memory under `asset:sha256:<hex>` URIs."""

    def __init__(self) -> None:
        self._assets: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; equal bytes give the same URI.

        >>> InMemoryAssetStore().put(b"x").startswith(ASSET_PREFIX)
        True
        """
        uri = asset_uri(data)
        self._assets[uri] = data
        return uri

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        if not uri.startswith(ASSET_PREFIX):
            raise SchemaError(
                f"{uri!r} is not an asset URI",
                hint=f"pass a URI returned by put(), starting with {ASSET_PREFIX}",
                context={"uri": uri},
            )
        if uri not in self._assets:
            raise UnknownRefError(
                f"no asset {uri}", hint="store it first with put()", context={"uri": uri}
            )
        return self._assets[uri]
