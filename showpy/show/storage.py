#!/usr/bin/env python3

"""
storage.py --- the repository and asset protocols plus the agent's file view of a show

Contains:
    ROOT_REVISION: names the revision before the first save
    Repository: versions a show's documents; every write checks the expected revision
    AssetStore: stores binary assets under content-hash URIs
    Workspace: exposes one show as files to one principal, with ownership rules
    Workspace.read(): returns a file's text or bytes
    Workspace.write(): validates and saves one file
    Workspace.ls(): lists a directory
    Workspace.commit(): saves a batch of document changes under the principal's rules
    Workspace.refresh(): moves the workspace to the head revision
    Workspace.show_at(): returns the show at a revision, or None before the first save
    Workspace.tree_at(): returns the stored documents at a revision
    open_workspace(): opens a workspace at the head revision
    load_show(): reads the whole show at the workspace revision
    save_show(): writes the whole show, refusing when the designer changed it meanwhile
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.errors import LockedError, StorageError, UnknownRefError
from showpy.core.ids import SetId, TransitionId
from showpy.core.util import close_matches
from showpy.show.documents import SHOW_DOC, decode_show, is_show_doc, split_path
from showpy.show.files import (
    DESIGNER_PATHS,
    all_files,
    directories,
    read_file,
    show_changes,
    staged_changes,
)
from showpy.show.locks import require_unlocked
from showpy.show.models import Principal, Revision, RevisionKind, Show
from showpy.time.models import Timeline

ROOT_REVISION = ""
DESIGNER_PRINCIPALS: frozenset[Principal] = frozenset({"designer", "system"})
SAVE_MESSAGE = "save show"


class Repository(Protocol):
    """Versions each show's documents in a linear history of revisions.

    Paths are workspace paths such as `/sets/1/set.json`. Every write takes the revision the
    caller read at and raises ConflictError, listing what landed since, when the head moved.
    """

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows."""
        ...

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        ...

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision, oldest first."""
        ...

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision."""
        ...

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        ...

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        ...

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
        """Writes (bytes) or deletes (None) documents atomically as one revision.

        An edit that changes nothing returns the head revision unchanged (idempotent upsert);
        a `snapshot` records an empty revision.
        """
        ...

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        ...

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
        ...

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        ...

    def get_set(self, show_id: str, set_id: SetId) -> Set:
        """Returns one set."""
        ...

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
        """Replaces one existing set."""
        ...

    def get_script(self, show_id: str, set_id: SetId) -> str:
        """Returns a set's build script."""
        ...

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
        ...

    def get_paths(self, show_id: str, tid: TransitionId) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        ...

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
        """Replaces a transition's paths; counts follow the destination set."""
        ...

    def get_forms(self, show_id: str, set_id: SetId) -> tuple[Form, ...]:
        """Returns a set's forms sorted by ID."""
        ...

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
        """Replaces a set's forms."""
        ...

    def get_timeline(self, show_id: str) -> Timeline:
        """Returns the show timeline."""
        ...

    def get_limits(self, show_id: str) -> str:
        """Returns the designer's limits YAML text; empty when none is stored."""
        ...

    def get_waivers(self, show_id: str) -> str:
        """Returns the designer's waivers YAML text; empty when none is stored."""
        ...


class AssetStore(Protocol):
    """Stores binary assets such as captured video and audio under content-hash URIs."""

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; storing equal bytes returns the same URI."""
        ...

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        ...


class Workspace:
    """Exposes one show as files to one principal.

    The workspace reads at its own revision, like a working copy. Writes default to expecting
    that revision, so an edit made against a stale view raises ConflictError instead of
    overwriting the designer. `refresh()` moves to the head; each successful write moves to
    the revision it created. Agents may not write designer-owned files or touch pins and
    pinned items.

    Attributes:
        repo: Storage backend.
        show_id: Show being edited.
        principal: Who is editing.
    """

    def __init__(self, repo: Repository, show_id: str, principal: Principal, revision: str):
        self.repo = repo
        self.show_id = show_id
        self.principal: Principal = principal
        self._revision = revision
        self._trees: dict[str, dict[str, bytes]] = {}
        self._shows: dict[str, Show | None] = {}

    @property
    def revision(self) -> str:
        """Returns the revision this workspace reads at."""
        return self._revision

    def refresh(self) -> str:
        """Moves the workspace to the head revision and returns it."""
        self._revision = self.repo.head(self.show_id)
        return self._revision

    def tree_at(self, revision: str) -> dict[str, bytes]:
        """Returns the stored documents at a revision; revisions never change, so it caches."""
        if revision == ROOT_REVISION:
            return {}
        if revision not in self._trees:
            stored = {
                p: self.repo.read(self.show_id, p, revision)
                for p in self.repo.paths(self.show_id, revision)
            }
            self._trees = {revision: {p: d for p, d in stored.items() if d is not None}}
        return dict(self._trees[revision])

    def show_at(self, revision: str) -> Show | None:
        """Returns the show at a revision, or None before the first save; cached like trees."""
        if revision not in self._shows:
            tree = self.tree_at(revision)
            self._shows = {revision: decode_show(tree, revision) if SHOW_DOC in tree else None}
        return self._shows[revision]

    def _show(self) -> Show | None:
        return self.show_at(self._revision)

    def _tree(self) -> dict[str, bytes]:
        return self.tree_at(self._revision)

    def read(self, path: str) -> str | bytes:
        """Returns a file: text for documents, bytes for binary renders and exports.

        >>> from showpy.show.repositories import InMemoryRepository
        >>> from showpy.examples import two_set_show
        >>> from datetime import datetime, UTC
        >>> repo = InMemoryRepository()
        >>> _ = repo.put_show("show-1", two_set_show(), expected_revision="", actor="designer",
        ...                   at=datetime(2026, 11, 11, tzinfo=UTC), message="create")
        >>> open_workspace(repo, "show-1", "agent").read("/sets/2/placements.csv").splitlines()[1]
        'T1,0.0,9.0,0.0,,,False,True,'
        """
        split_path(path)
        return read_file(path, self._show(), self._tree())

    def ls(self, path: str = "/") -> list[str]:
        """Lists a directory: files by name, subdirectories with a trailing `/`."""
        segments = split_path(path)
        prefix = "/" + "".join(f"{s}/" for s in segments)
        show, tree = self._show(), self._tree()
        known = directories(show, tree)
        if prefix not in known:
            close = close_matches(prefix, sorted(known))
            raise UnknownRefError(
                f"no directory {path}",
                hint=f"did you mean {', '.join(close)}?" if close else "ls('/') lists the top",
                context={"path": path},
            )
        children = {
            f.removeprefix(prefix).split("/")[0] + ("/" if "/" in f.removeprefix(prefix) else "")
            for f in [*all_files(show, tree), *known]
            if f.startswith(prefix) and f != prefix
        }
        return sorted(children)

    def _require_allowed(self, changes: Mapping[str, bytes | None]) -> None:
        if self.principal in DESIGNER_PRINCIPALS:
            return
        owned = sorted(p for p in changes if p in DESIGNER_PATHS)
        if owned:
            raise LockedError(
                f"{', '.join(owned)} belong to the designer",
                hint="read them, and ask the designer to change them",
                context={"paths": owned, "principal": self.principal},
            )

    def _require_pins_kept(self, changes: Mapping[str, bytes | None], base: str) -> None:
        if self.principal in DESIGNER_PRINCIPALS or not any(map(is_show_doc, changes)):
            return
        before = self.show_at(base)
        if before is None:
            return
        after = dict(self.tree_at(base))
        for path, data in changes.items():
            if data is None:
                after.pop(path, None)
            else:
                after[path] = data
        require_unlocked(before, decode_show(after, base))

    def commit(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        message: str,
        expected_revision: str | None = None,
        kind: RevisionKind = "edit",
        target: str | None = None,
    ) -> Revision:
        """Saves document changes as one revision under the principal's ownership rules.

        Args:
            changes: Path to new bytes, or None to delete.
            at: Time stamp of the revision.
            message: What the change is for.
            expected_revision: Revision the change was made against; default the workspace's.
            kind: Revision kind, for undo and redo bookkeeping.
            target: Revision an undo, redo or revert acts on.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        self._require_allowed(changes)
        self._require_pins_kept(changes, base)
        revision = self.repo.transaction(
            self.show_id,
            changes,
            expected_revision=base,
            actor=self.principal,
            at=at,
            message=message,
            kind=kind,
            target=target,
        )
        self._revision = revision.id
        return revision

    def write(
        self,
        path: str,
        data: str | bytes,
        expected_revision: str | None = None,
        *,
        at: datetime,
        message: str = "",
    ) -> Revision:
        """Validates and saves one file.

        Args:
            path: Workspace path.
            data: New content; text files take UTF-8.
            expected_revision: Revision the edit was made against; default the workspace's.
            at: Time stamp of the revision.
            message: What the change is for; default `write <path>`.

        Returns:
            revision: The new head.
        """
        split_path(path)
        raw = data.encode() if isinstance(data, str) else data
        self._require_allowed({path: raw})
        changes = staged_changes(path, raw, self._show(), self._tree())
        return self.commit(
            changes, at=at, message=message or f"write {path}", expected_revision=expected_revision
        )


def open_workspace(repo: Repository, show_id: str, principal: Principal) -> Workspace:
    """Opens a workspace on a show at its head revision.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> open_workspace(InMemoryRepository(), "new-show", "designer").revision
    ''
    """
    if principal not in ("agent", "designer", "system"):
        raise UnknownRefError(
            f"unknown principal {principal!r}",
            hint="use agent, designer or system",
            context={"principal": principal},
        )
    return Workspace(repo, show_id, principal, repo.head(show_id))


def load_show(ws: Workspace) -> Show:
    """Reads the whole show at the workspace revision; `show.revision` records it."""
    show = ws.show_at(ws.revision)
    if show is None:
        raise StorageError(
            f"show {ws.show_id} has no revisions yet",
            hint="save a show first with save_show()",
            context={"show": ws.show_id},
        )
    return show


def save_show(ws: Workspace, show: Show, at: datetime, message: str = SAVE_MESSAGE) -> Revision:
    """Writes the whole show against `show.revision`, the revision it was loaded at.

    Raises ConflictError listing every revision (actor, message, paths) saved since, so the
    agent sees what the designer changed; reload, reapply and save again.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> from showpy.examples import two_set_show
    >>> from datetime import datetime, UTC
    >>> ws = open_workspace(InMemoryRepository(), "show-1", "designer")
    >>> save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)).parent
    ''
    """
    changes = show_changes(show, ws.tree_at(show.revision))
    return ws.commit(changes, at=at, message=message, expected_revision=show.revision)
