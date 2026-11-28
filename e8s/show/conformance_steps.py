"""
conformance_steps.py --- the commits, state reads and expectations the conformance checks share

Contains:
    AT: stamps every revision a check writes, so checks never read the clock
    BrokenRuleError: reports a broken storage rule a conformance check found
    require(): raises BrokenRuleError with a reason unless a condition holds
    Commit: holds one commit's revision, tree and new blobs
    next_commit(): builds the commit that applies document changes on top of a parent
    land(): commits a built commit and returns it
    raised(): runs a call that must fail with one error class and returns the error
    BackendState: holds everything a reader can see of some shows
    state_of(): reads every show, head, log entry, tree and blob a backend shows for some shows
    require_unchanged(): raises BrokenRuleError unless the backend still shows the state read before
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import NamedTuple

from e8s.core.errors import E8sError
from e8s.show.backend import RepositoryBackend
from e8s.show.document_repository import revision_id
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class BrokenRuleError(Exception):
    """Reports a broken storage rule a conformance check found."""


def require(why: str, *, holds: bool) -> None:
    """Raises BrokenRuleError with `why` unless the rule `holds`."""
    if not holds:
        raise BrokenRuleError(why)


class Commit(NamedTuple):
    """Holds one commit: its revision, the whole tree after it and the blobs it adds."""

    revision: Revision
    tree: dict[str, str]
    blobs: dict[str, bytes]


def next_commit(
    parent: str, old_tree: Mapping[str, str], changes: Mapping[str, bytes | None]
) -> Commit:
    """Builds the commit that applies document changes on top of a parent revision.

    It rebuilds the tree and the changed paths on its own rather than calling
    DocumentRepository.transaction, so the kit checks a backend against an independent oracle of
    that logic. Only the revision ID comes from `revision_id`, so a backend that checks stored
    IDs against their hash accepts it.

    Args:
        parent: ID of the parent revision; the root revision for a show's first commit.
        old_tree: Path to blob digest at the parent.
        changes: Path to new bytes, or None to delete the path.

    Returns:
        commit: The revision, the new tree and the blobs of the written documents.
    """
    blobs = {blob_digest(data): data for data in changes.values() if data is not None}
    tree = {path: digest for path, digest in old_tree.items() if path not in changes}
    tree.update({path: blob_digest(data) for path, data in changes.items() if data is not None})
    changed = tuple(sorted(p for p in set(old_tree) | set(tree) if old_tree.get(p) != tree.get(p)))
    ids = {path: tree.get(path) for path in changed}
    revision = Revision(id=revision_id(parent, AT, ids), parent=parent, at=AT, changed=changed)
    return Commit(revision, tree, blobs)


def land(backend: RepositoryBackend, show_id: str, commit: Commit) -> Commit:
    """Commits a built commit to a show and returns it."""
    backend.commit(show_id, commit.revision, commit.tree, commit.blobs)
    return commit


def raised[E: E8sError](error_class: type[E], call: Callable[[], object], what: str) -> E:
    """Runs a call that must fail with `error_class` and returns the error it raised.

    Args:
        error_class: Error class the call must raise; a subclass counts.
        call: The operation to run.
        what: Names the operation in the failure reason.

    Returns:
        error: The error the call raised.
    """
    try:
        call()
    except error_class as error:
        return error
    except Exception as error:
        message = f"{what} raised {type(error).__name__} instead of {error_class.__name__}: {error}"
        raise BrokenRuleError(message) from error
    raise BrokenRuleError(f"{what} did not raise {error_class.__name__}")


class BackendState(NamedTuple):
    """Holds everything a reader can see of some shows: shows, heads, logs, trees and blobs."""

    shows: list[str]
    heads: dict[str, str]
    logs: dict[str, list[Revision]]
    trees: dict[tuple[str, str], dict[str, str]]
    blobs: dict[tuple[str, str], bytes]


def state_of(backend: RepositoryBackend, show_ids: tuple[str, ...]) -> BackendState:
    """Reads every show, head, log entry, tree and blob a backend shows for some shows."""
    heads = {show_id: backend.head(show_id) for show_id in show_ids}
    logs = {show_id: backend.log(show_id) for show_id in show_ids}
    trees = {
        (show_id, revision.id): dict(backend.tree(show_id, revision.id))
        for show_id, log in logs.items()
        for revision in log
    }
    blobs = {
        (show_id, digest): backend.blob(show_id, digest)
        for (show_id, _), tree in trees.items()
        for digest in tree.values()
    }
    return BackendState(sorted(backend.shows()), heads, logs, trees, blobs)


def require_unchanged(
    backend: RepositoryBackend, before: BackendState, show_ids: tuple[str, ...], what: str
) -> None:
    """Raises BrokenRuleError unless the backend still shows the state read before `what` failed."""
    after = state_of(backend, show_ids)
    for field in BackendState._fields:
        is_same = getattr(after, field) == getattr(before, field)
        require(f"{what} failed but changed the {field} a reader sees", holds=is_same)
