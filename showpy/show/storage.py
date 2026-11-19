"""
storage.py --- the repository and asset protocols plus the file view of a show

Contains:
    ROOT_REVISION: names the revision before the first save
    Repository: versions a show's documents; every write checks the expected revision
    AssetStore: stores binary assets under content-hash URIs
    Workspace: exposes one show as files, reading at its own revision like a working copy
    Workspace.read(): returns a file's text or bytes
    Workspace.write(): validates and saves one file
    Workspace.ls(): lists a directory
    Workspace.commit(): saves a batch of file changes addressed by set ID as one revision
    Workspace.refresh(): moves the workspace to the head revision
    Workspace.show_at(): returns the show at a revision, or None before the first save
    Workspace.tree_at(): returns the stored documents at a revision
    open_workspace(): opens a workspace at the head revision
    load_show(): reads the whole show at the workspace revision
    save_show(): writes the whole show, refusing when another write landed meanwhile
"""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Protocol

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.errors import StorageError, UnknownRefError
from showpy.core.ids import SetId
from showpy.core.util import close_matches
from showpy.show.documents import SHOW_DOC, decode_show, encode_documents, is_show_doc, split_path
from showpy.show.files import (
    all_files,
    directories,
    read_file,
    show_changes,
    staged_changes,
)
from showpy.show.models import Revision, Show
from showpy.show.set_keys import SetFolders
from showpy.time.models import Timeline

ROOT_REVISION = ""


class Repository(Protocol):
    """Versions each show's documents in a linear history of revisions.

    Paths are stored paths, which address sets by key (`/sets/<key>/set.json`).
    Every write takes the revision the caller read at and raises ConflictError, listing what
    landed since, when the head moved. Writes check validity, never pins or locks: edit
    functions enforce those for every caller; a host that writes raw documents
    owns them.
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

    def documents(self, show_id: str, revision: str | None = None) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision (default head), reading bytes on lookup."""
        ...

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes (bytes) or deletes (None) documents atomically as one revision.

        An edit that changes nothing returns the head revision unchanged (idempotent upsert).
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
        at: datetime,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def list_sets(self, show_id: str) -> list[SetId]:
        """Returns set IDs in show order."""
        ...

    def get_set(self, show_id: str, set_id: str) -> Set:
        """Returns one set."""
        ...

    def put_set(
        self,
        show_id: str,
        new_set: Set,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces one existing set.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_script(self, show_id: str, set_id: str) -> str:
        """Returns a set's build script."""
        ...

    def put_script(
        self,
        show_id: str,
        set_id: str,
        source: str,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's build script.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_paths(self, show_id: str, tid: tuple[str, str]) -> TransitionSpec:
        """Returns a transition's paths, defaulting to straight moves."""
        ...

    def put_paths(
        self,
        show_id: str,
        spec: TransitionSpec,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a transition's paths; counts follow the destination set.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        ...

    def get_forms(self, show_id: str, set_id: str) -> tuple[Form, ...]:
        """Returns a set's forms sorted by ID."""
        ...

    def put_forms(
        self,
        show_id: str,
        set_id: str,
        forms: tuple[Form, ...],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Replaces a set's forms.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
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
    """Exposes one show as files, addressing sets by display ID.

    Paths such as `/sets/2/set.py` map to the stored folder of the set's key and back, so a
    renumber moves no file. The workspace reads at its own revision, like a working
    copy. Writes default to expecting
    that revision, so an edit made against a stale view raises ConflictError instead of
    overwriting a newer save. `refresh()` moves to the head; each successful write moves to
    the revision it created. Who may write what is the host's decision.

    Attributes:
        repo: Storage backend.
        show_id: Show being edited.
    """

    def __init__(self, repo: Repository, show_id: str, revision: str):
        self.repo = repo
        self.show_id = show_id
        self._revision = revision
        self._trees: dict[str, Mapping[str, bytes]] = {}
        self._shows: dict[str, Show | None] = {}
        self._canonical_revisions: set[str] = set()

    @property
    def revision(self) -> str:
        """Returns the revision this workspace reads at."""
        return self._revision

    def refresh(self) -> str:
        """Moves the workspace to the head revision and returns it."""
        self._revision = self.repo.head(self.show_id)
        return self._revision

    def tree_at(self, revision: str) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision, reading each file's bytes on lookup.

        Revisions never change, so the last tree is cached.
        """
        if revision == ROOT_REVISION:
            return {}
        if revision not in self._trees:
            self._trees = {revision: self.repo.documents(self.show_id, revision)}
        return self._trees[revision]

    def show_at(self, revision: str) -> Show | None:
        """Returns the show at a revision, or None before the first save; cached like trees."""
        if revision not in self._shows:
            tree = self.tree_at(revision)
            self._shows = {revision: decode_show(tree, revision) if SHOW_DOC in tree else None}
        return self._shows[revision]

    def _show(self) -> Show | None:
        return self.show_at(self._revision)

    def _tree(self) -> Mapping[str, bytes]:
        return self.tree_at(self._revision)

    def read(self, path: str) -> str | bytes:
        """Returns a file: text for documents, bytes for binary renders and exports.

        >>> from showpy.show.repositories import InMemoryRepository
        >>> from showpy.examples import two_set_show
        >>> from datetime import datetime, UTC
        >>> repo = InMemoryRepository()
        >>> _ = repo.put_show("show-1", two_set_show(), expected_revision="",
        ...                   at=datetime(2026, 11, 11, tzinfo=UTC))
        >>> open_workspace(repo, "show-1").read("/sets/2/placements.csv").splitlines()[1]
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

    def commit(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
    ) -> Revision:
        """Saves file changes addressed by display set ID as one revision.

        A folder that names no set but matches another set's key, ignoring case, is refused
        with StorageError code AMBIGUOUS_PATH rather than written into that set's folder.

        Args:
            changes: Workspace path to new bytes, or None to delete.
            at: Time stamp of the revision.
            expected_revision: Revision the change was made against; default the workspace's.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        for path in changes:
            split_path(path)
        folders = SetFolders(self.show_at(base))
        stored = {folders.to_stored(path): data for path, data in changes.items()}
        return self._commit_normalizing(stored, at=at, expected_revision=base)

    def _commit_stored(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
    ) -> Revision:
        """Saves stored-document changes, addressed by set key, as one revision.

        Args:
            changes: Stored path to new bytes, or None to delete.
            at: Time stamp of the revision.
            expected_revision: Revision the change was made against; default the workspace's.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        revision = self.repo.transaction(
            self.show_id,
            changes,
            expected_revision=base,
            at=at,
        )
        self._revision = revision.id
        return revision

    def _commit_normalizing(
        self,
        changes: Mapping[str, bytes | None],
        *,
        at: datetime,
        expected_revision: str | None = None,
        is_complete: bool = False,
    ) -> Revision:
        """Saves stored-document changes after re-encoding non-canonical documents on their own.

        When any stored show document of the base revision is not canonical, one revision holding
        only those re-encoded documents lands first and the change follows, so the change's own
        revision lists only what it edits and later writes add no second one. If the
        change then conflicts, the normalizing revision stays; it changes no content. A revision
        found or made canonical is remembered, so the check runs once per revision.

        Args:
            changes: Stored path to new bytes, or None to delete.
            at: Time stamp of the revisions.
            expected_revision: Revision the change was made against; default the workspace's.
            is_complete: Whether `changes` holds every show document that differs from the
                canonical encoding of the edited show, as show_changes() lists them; then only
                those documents need re-encoding to find the non-canonical ones.

        Returns:
            revision: The new head; the workspace moves to it.
        """
        base = self._revision if expected_revision is None else expected_revision
        normalizing = self._normalizing_for(base, changes, is_complete=is_complete)
        if not normalizing:
            return self._remembered(
                self._commit_stored(changes, at=at, expected_revision=base),
                changes,
                is_complete=is_complete,
            )
        normalized = self._commit_stored(normalizing, at=at, expected_revision=base)
        self._canonical_revisions.add(normalized.id)
        stored = self.tree_at(normalized.id)
        remaining = {p: d for p, d in changes.items() if d is None or stored.get(p) != d}
        return self._remembered(
            self._commit_stored(remaining, at=at, expected_revision=normalized.id),
            changes,
            is_complete=is_complete,
        )

    def _normalizing_for(
        self, base: str, changes: Mapping[str, bytes | None], *, is_complete: bool
    ) -> dict[str, bytes | None]:
        """Lists the re-encodings a change over `base` needs first, empty for a known revision."""
        if base in self._canonical_revisions:
            return {}
        tree = self.tree_at(base)
        if is_complete:
            paths = [p for p, d in changes.items() if d is not None and p in tree and tree[p] != d]
        else:
            paths = [p for p in tree if is_show_doc(p)]
        normalizing = _normalizing_changes(self.show_at(base), tree, paths)
        if not normalizing:
            self._canonical_revisions.add(base)
        return normalizing

    def _remembered(
        self, revision: Revision, changes: Mapping[str, bytes | None], *, is_complete: bool
    ) -> Revision:
        """Remembers a new revision as canonical when its change wrote only canonical documents."""
        if is_complete or not any(is_show_doc(p) for p in changes):
            self._canonical_revisions.add(revision.id)
        return revision

    def write(
        self,
        path: str,
        data: str | bytes,
        expected_revision: str | None = None,
        *,
        at: datetime,
    ) -> Revision:
        """Validates and saves one file.

        Args:
            path: Workspace path.
            data: New content; text files take UTF-8.
            expected_revision: Revision the edit was made against; default the workspace's.
            at: Time stamp of the revision.

        Returns:
            revision: The new head.
        """
        split_path(path)
        raw = data.encode() if isinstance(data, str) else data
        changes = staged_changes(path, raw, self._show(), self._tree())
        return self._commit_normalizing(
            changes, at=at, expected_revision=expected_revision, is_complete=is_show_doc(path)
        )


def open_workspace(repo: Repository, show_id: str) -> Workspace:
    """Opens a workspace on a show at its head revision.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> open_workspace(InMemoryRepository(), "new-show").revision
    ''
    """
    return Workspace(repo, show_id, repo.head(show_id))


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


def save_show(ws: Workspace, show: Show, at: datetime) -> Revision:
    """Writes the whole show against `show.revision`, the revision it was loaded at.

    Raises ConflictError listing every revision (short ID, time, paths) saved since, so the
    caller sees what changed meanwhile; reload, reapply and save again. Stored documents that
    are not canonical are re-encoded in a revision of their own first; if the edit
    then conflicts, that revision stays, it changes no content, and the retry builds on it.

    >>> from showpy.show.repositories import InMemoryRepository
    >>> from showpy.examples import two_set_show
    >>> from datetime import datetime, UTC
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)).parent
    ''
    """
    changes = show_changes(show, ws.tree_at(show.revision))
    return ws._commit_normalizing(changes, at=at, expected_revision=show.revision, is_complete=True)


def _normalizing_changes(
    stored: Show | None, tree: Mapping[str, bytes], paths: Collection[str]
) -> dict[str, bytes | None]:
    """Lists the named stored show documents whose bytes differ from their canonical encoding.

    Only documents are listed, never scripts or other files, and nothing is deleted, so the
    decoded show is the same before and after.
    """
    if stored is None:
        return {}
    canonical = encode_documents(stored, paths)
    return {p: d for p, d in canonical.items() if tree[p] != d}
