"""
repositories.py --- the shared repository logic, the in-memory repository and asset store

Contains:
    RepositoryBackend: stores revisions, trees and blobs; commits compare-and-swap on the head
    conflict_error(): builds the ConflictError that lists the revisions that landed meanwhile
    DocumentRepository: implements Repository over a backend of revisions, trees and blobs
    InMemoryRepository: keeps every revision in process memory
    InMemoryAssetStore: keeps assets in process memory under content-hash URIs
"""

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, override

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.errors import ConflictError, SchemaError, StorageError, UnknownRefError
from showpy.core.ids import SetId, TransitionId
from showpy.core.util import close_matches, content_hash
from showpy.show.documents import decode_show, script_doc, split_path
from showpy.show.files import LIMITS_DOC, WAIVERS_DOC, show_changes
from showpy.show.models import Principal, Revision, RevisionKind, Show
from showpy.show.storage import ROOT_REVISION
from showpy.time.models import Timeline

SHOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
ASSET_PREFIX = "asset:sha256:"
SHORT_ID = 7


def _require_show_id(show_id: str) -> None:
    if not SHOW_ID_PATTERN.match(show_id) or ".." in show_id:
        raise StorageError(
            f"show id {show_id!r} cannot be stored",
            hint="use letters, digits, _, . and - only, starting with a letter or digit",
            context={"show": show_id},
            code="BAD_PATH",
        )


def digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return hashlib.sha256(data).hexdigest()


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


def conflict_error(show_id: str, expected: str, landed: list[Revision]) -> ConflictError:
    """Builds the ConflictError listing each landed revision's actor, message and paths."""
    summary = "; ".join(f"{r.actor} {r.message} ({', '.join(r.changed)})" for r in landed)
    return ConflictError(
        f"{len(landed)} revision(s) landed on {show_id} since {expected[:SHORT_ID] or 'the start'}:"
        f" {summary}",
        hint="re-read at the head revision, reapply the edit and write again",
        context={
            "expected": expected,
            "head": landed[-1].id if landed else expected,
            "changes": [r.model_dump(mode="json") for r in landed],
        },
    )


class DocumentRepository:
    """Implements Repository over a backend that stores revisions, trees and blobs.

    Attributes:
        backend: Where revisions, trees and blobs live.
    """

    def __init__(self, backend: RepositoryBackend):
        self.backend = backend

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

    def _documents(self, show_id: str, revision: str | None) -> dict[str, bytes]:
        tree = self._tree(show_id, revision)
        return {p: self.backend.blob(show_id, d) for p, d in tree.items()}

    def _require_head(self, show_id: str, expected: str) -> list[Revision]:
        log = self.revisions(show_id)
        ids = [r.id for r in log]
        if expected != ROOT_REVISION and expected not in ids:
            self.get_revision(show_id, expected)
        landed = log[ids.index(expected) + 1 :] if expected else log
        if landed:
            raise conflict_error(show_id, expected, landed)
        return log

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        actor: Principal,
        at: datetime,
        message: str,
        kind: RevisionKind = "edit",
        target: str | None = None,
    ) -> Revision:
        """Writes or deletes documents atomically as one revision with a deterministic ID.

        Args:
            show_id: Show written.
            changes: Path to new bytes, or None to delete.
            expected_revision: Head the caller read at; ConflictError when the head moved.
            actor: Who writes.
            at: Time stamp.
            message: What the change is for.
            kind: Revision kind.
            target: Revision an undo, redo or revert acts on.

        Returns:
            revision: The new head, or the unchanged head when nothing differs.

        >>> from datetime import UTC
        >>> repo = InMemoryRepository()
        >>> at = datetime(2026, 11, 11, tzinfo=UTC)
        >>> rev = repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision="",
        ...                        actor="agent", at=at, message="render")
        >>> repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision=rev.id,
        ...                  actor="agent", at=at, message="again") == rev
        True
        """
        for path in changes:
            split_path(path)
        log = self._require_head(show_id, expected_revision)
        old = self._tree(show_id, expected_revision)
        blobs = {digest(d): d for d in changes.values() if d is not None}
        new = {**old, **{p: digest(d) for p, d in changes.items() if d is not None}}
        for path in (p for p, d in changes.items() if d is None):
            new.pop(path, None)
        changed = tuple(sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p)))
        if not changed and kind != "snapshot":
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
            "actor": actor,
            "message": message,
            "kind": kind,
            "target": target,
            "changes": {p: new.get(p) for p in changed},
        }
        revision = Revision(
            id=content_hash(body),
            parent=expected_revision,
            at=at,
            actor=actor,
            message=message,
            changed=changed,
            kind=kind,
            target=target,
        )
        self.backend.commit(show_id, revision, new, blobs)
        return revision

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        rev = self.head(show_id) if revision is None else revision
        docs = self._documents(show_id, rev)
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
        actor: Principal,
        at: datetime,
        message: str,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has."""
        return self.transaction(
            show_id,
            show_changes(show, self._documents(show_id, expected_revision)),
            expected_revision=expected_revision,
            actor=actor,
            at=at,
            message=message,
        )

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        return self.get_show(show_id).set_ids()

    def get_set(self, show_id: str, set_id: SetId) -> Set:
        """Returns one set."""
        return self.get_show(show_id).set(set_id)

    def put_set(
        self,
        show_id: str,
        new_set: Set,
        *,
        expected_revision: str,
        actor: Principal,
        at: datetime,
        message: str,
    ) -> Revision:
        """Replaces one existing set; add sets through put_show()."""
        show = self.get_show(show_id, expected_revision or None)
        position = show.set_index(new_set.id)
        sets = (*show.sets[:position], new_set, *show.sets[position + 1 :])
        return self.put_show(
            show_id,
            show.model_copy(update={"sets": sets}),
            expected_revision=expected_revision,
            actor=actor,
            at=at,
            message=message,
        )

    def get_script(self, show_id: str, set_id: SetId) -> str:
        """Returns a set's build script."""
        self.get_set(show_id, set_id)
        source = self.read(show_id, script_doc(set_id))
        if source is None:
            raise UnknownRefError(
                f"set {set_id} has no script",
                hint="write one with put_script()",
                context={"set": set_id},
            )
        return source.decode()

    def put_script(
        self,
        show_id: str,
        set_id: SetId,
        source: str,
        *,
        expected_revision: str,
        actor: Principal,
        at: datetime,
        message: str,
    ) -> Revision:
        """Replaces a set's build script."""
        self.get_show(show_id, expected_revision or None).set(set_id)
        return self.transaction(
            show_id,
            {script_doc(set_id): source.encode()},
            expected_revision=expected_revision,
            actor=actor,
            at=at,
            message=message,
        )

    def get_paths(self, show_id: str, tid: TransitionId) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        return self.get_show(show_id).transition(tid)

    def put_paths(
        self,
        show_id: str,
        spec: TransitionSpec,
        *,
        expected_revision: str,
        actor: Principal,
        at: datetime,
        message: str,
    ) -> Revision:
        """Replaces a transition's paths; counts follow the destination set (ADR 0001 item 16)."""
        show = self.get_show(show_id, expected_revision or None)
        stored = spec.model_copy(update={"counts": show.transition(spec.id).counts})
        specs = (*(s for s in show.transitions if s.id != spec.id), stored)
        return self.put_show(
            show_id,
            show.model_copy(update={"transitions": specs}),
            expected_revision=expected_revision,
            actor=actor,
            at=at,
            message=message,
        )

    def get_forms(self, show_id: str, set_id: SetId) -> tuple[Form, ...]:
        """Returns a set's forms sorted by ID."""
        show = self.get_show(show_id)
        show.set(set_id)
        return tuple(f for _, f in sorted(show.forms.items()) if f.set == set_id)

    def put_forms(
        self,
        show_id: str,
        set_id: SetId,
        forms: tuple[Form, ...],
        *,
        expected_revision: str,
        actor: Principal,
        at: datetime,
        message: str,
    ) -> Revision:
        """Replaces a set's forms; every form must name that set."""
        wrong = [f.id for f in forms if f.set != set_id]
        if wrong:
            raise SchemaError(
                f"forms {', '.join(wrong)} do not belong to set {set_id}",
                hint=f"set form.set to {set_id!r}",
                context={"set": set_id, "forms": wrong},
            )
        show = self.get_show(show_id, expected_revision or None)
        show.set(set_id)
        kept = {k: f for k, f in show.forms.items() if f.set != set_id}
        edited = show.model_copy(update={"forms": {**kept, **{f.id: f for f in forms}}})
        return self.put_show(
            show_id,
            edited,
            expected_revision=expected_revision,
            actor=actor,
            at=at,
            message=message,
        )

    def get_timeline(self, show_id: str) -> Timeline:
        """Returns the show timeline."""
        return self.get_show(show_id).timeline

    def get_limits(self, show_id: str) -> str:
        """Returns the designer's limits YAML text; empty when none is stored."""
        return (self.read(show_id, LIMITS_DOC) or b"").decode()

    def get_waivers(self, show_id: str) -> str:
        """Returns the designer's waivers YAML text; empty when none is stored."""
        return (self.read(show_id, WAIVERS_DOC) or b"").decode()


class _MemoryBackend(RepositoryBackend):
    def __init__(self) -> None:
        self._logs: dict[str, list[Revision]] = {}
        self._trees: dict[tuple[str, str], dict[str, str]] = {}
        self._blobs: dict[str, bytes] = {}

    def show_ids(self) -> list[str]:
        return list(self._logs)

    def log(self, show_id: str) -> list[Revision]:
        return list(self._logs.get(show_id, []))

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        return dict(self._trees[(show_id, revision_id)])

    @override
    def blob(self, show_id: str, digest: str) -> bytes:
        return self._blobs[digest]

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        log = self._logs.setdefault(show_id, [])
        if (log[-1].id if log else ROOT_REVISION) != revision.parent:
            raise conflict_error(show_id, revision.parent, log)
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
        uri = f"{ASSET_PREFIX}{digest(data)}"
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
