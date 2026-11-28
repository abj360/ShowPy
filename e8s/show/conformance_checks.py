"""
conformance_checks.py --- the ten storage rules every repository backend must keep

Contains:
    one_winner_per_parent(): checks that one commit lands per parent, racing writers included
    read_your_writes(): checks that heads, logs, trees and blobs read back what was committed
    failed_commit_changes_nothing(): checks that a refused commit leaves every read unchanged
    log_since_is_ordered(): checks that log() is oldest first and log(since=) returns the rest
    head_matches_log(): checks that the head is always the last log entry
    blob_reads_are_verified(): checks blob bytes against digests and missing blobs fail closed
    ids_are_never_trusted(): checks malformed IDs and other shows' revisions and blobs are refused
    errors_are_storage_errors(): checks every failure is a library error with a message and hint
    unicode_and_case_paths(): checks paths and show IDs differing in case or Unicode form
    long_names(): checks long show IDs and long document paths
"""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import cast

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, StorageError, UnknownRefError
from e8s.show.backend import RepositoryBackend
from e8s.show.conformance_steps import (
    Commit,
    land,
    next_commit,
    raised,
    require,
    require_unchanged,
    state_of,
)
from e8s.show.document_repository import DocumentRepository
from e8s.show.integrity import LazyBlobs, blob_digest
from e8s.show.storage import ROOT_REVISION

SHOW_ID = "show_1"
OTHER_SHOW_ID = "show_2"
A_PATH = "/renders/a.txt"
B_PATH = "/renders/b.txt"
HISTORY = 5
RIVALS = 4
# Generous enough for a slow network file system; a writer that takes longer fails the check.
WRITER_TIMEOUT_S = 60.0
UNKNOWN_ID = blob_digest(b"conformance: never stored")
CASE_PATHS = (
    "/renders/A.txt",
    "/renders/a.txt",
    "/renders/Équipe.txt",
    "/renders/équipe.txt",
    "/renders/équipe.txt",
    "/renders/STRASSE.txt",
    "/renders/straße.txt",
    "/renders/日本.txt",
)
CASE_SHOW_PAIRS = (
    ("Case_show", "case_show"),
    ("show_É", "show_é"),
    ("nfc_é", "nfc_é"),
)
LONG_NAME_CHARS = 1_024
LONG_SEGMENT_CHARS = 255


def _first(backend: RepositoryBackend, show_id: str, content: bytes) -> Commit:
    return land(backend, show_id, next_commit(ROOT_REVISION, {}, {A_PATH: content}))


def _landing(backend: RepositoryBackend, show_id: str, count: int) -> Iterator[Commit]:
    """Lands `count` commits on a show, one per step, each changing one or two documents."""
    parent, tree = ROOT_REVISION, dict[str, str]()
    for step in range(count):
        changes = {A_PATH: f"{show_id} step {step}".encode()}
        if step % 2:
            changes[B_PATH] = f"{show_id} side {step}".encode()
        commit = land(backend, show_id, next_commit(parent, tree, changes))
        parent, tree = commit.revision.id, commit.tree
        yield commit


def _history(backend: RepositoryBackend, show_id: str, count: int) -> list[Commit]:
    return list(_landing(backend, show_id, count))


def _race(backend: RepositoryBackend, parent: Commit) -> list[Commit]:
    """Starts RIVALS commits on one parent at once and returns the ones that landed."""
    rivals = [
        next_commit(parent.revision.id, parent.tree, {A_PATH: f"rival {n}".encode()})
        for n in range(RIVALS)
    ]
    start = threading.Barrier(RIVALS)

    def write(rival: Commit) -> bool:
        start.wait(WRITER_TIMEOUT_S)
        try:
            land(backend, SHOW_ID, rival)
        except ConflictError:
            return False
        return True

    pool = ThreadPoolExecutor(max_workers=RIVALS)
    try:
        futures = [pool.submit(write, rival) for rival in rivals]
        outcomes = [future.result(WRITER_TIMEOUT_S) for future in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return [rival for rival, has_landed in zip(rivals, outcomes, strict=True) if has_landed]


def one_winner_per_parent(backend: RepositoryBackend) -> None:
    """Checks that one commit lands per parent and every other writer gets ConflictError."""
    base = _first(backend, SHOW_ID, b"base")
    first = land(backend, SHOW_ID, next_commit(base.revision.id, base.tree, {A_PATH: b"first"}))
    second = next_commit(base.revision.id, base.tree, {A_PATH: b"second"})
    lost = raised(ConflictError, partial(land, backend, SHOW_ID, second), "a second commit")
    require(
        "the ConflictError hides the winner", holds=lost.context.get("head") == first.revision.id
    )
    require("a losing commit moved the head", holds=backend.head(SHOW_ID) == first.revision.id)
    raised(UnknownRefError, partial(backend.tree, SHOW_ID, second.revision.id), "a lost tree")
    winners = _race(backend, first)
    require(
        f"{len(winners)} of {RIVALS} racing commits on one parent landed", holds=len(winners) == 1
    )
    landed = [r.id for r in backend.log(SHOW_ID, since=first.revision.id)]
    require("the log after a race is not the one winner", holds=landed == [winners[0].revision.id])
    require(
        "the head is not the race's winner", holds=backend.head(SHOW_ID) == winners[0].revision.id
    )


def _require_documents(
    backend: RepositoryBackend, commit: Commit, wanted: dict[str, bytes]
) -> None:
    documents = DocumentRepository(backend).documents(SHOW_ID, commit.revision.id)
    require(
        f"documents at {commit.revision.id[:7]} are not as written", holds=dict(documents) == wanted
    )


def read_your_writes(backend: RepositoryBackend) -> None:
    """Checks that heads, logs, trees and blobs read back exactly what was committed."""
    is_empty = backend.shows() == [] and backend.log(SHOW_ID) == []
    require("a new backend holds a show", holds=is_empty and backend.head(SHOW_ID) == ROOT_REVISION)
    buffer = bytearray(b"first a")
    first = next_commit(ROOT_REVISION, {}, {A_PATH: b"first a", B_PATH: b"first b"})
    # A caller may hand in a bytearray and change it later; the backend must keep a copy.
    handed = {**first.blobs, blob_digest(b"first a"): cast("bytes", buffer)}
    backend.commit(SHOW_ID, first.revision, first.tree, handed)
    buffer[:] = b"changed"
    _require_documents(backend, first, {A_PATH: b"first a", B_PATH: b"first b"})
    changes = {A_PATH: b"second a", B_PATH: None}
    second = land(backend, SHOW_ID, next_commit(first.revision.id, first.tree, changes))
    require("the head is not the last commit", holds=backend.head(SHOW_ID) == second.revision.id)
    log = backend.log(SHOW_ID)
    require(
        "the log does not hold the revisions sent", holds=log == [first.revision, second.revision]
    )
    for commit in (first, second):
        tree = dict(backend.tree(SHOW_ID, commit.revision.id))
        require(
            f"the tree of {commit.revision.id[:7]} is not the one sent", holds=tree == commit.tree
        )
    _require_documents(backend, first, {A_PATH: b"first a", B_PATH: b"first b"})
    _require_documents(backend, second, {A_PATH: b"second a"})
    require("shows() does not list exactly the show written", holds=backend.shows() == [SHOW_ID])


def _forged(base: Commit) -> list[tuple[str, Commit]]:
    """Lists commits on `base` that a backend must refuse, each with what is wrong with it."""
    child = next_commit(base.revision.id, base.tree, {A_PATH: b"child"})
    revision, digest = child.revision, blob_digest(b"child")
    forged_blobs = {digest: b"forged"}
    return [
        ("a commit on a stale parent", next_commit(ROOT_REVISION, {}, {A_PATH: b"stale"})),
        ("a commit on an unknown parent", next_commit(UNKNOWN_ID, base.tree, {A_PATH: b"x"})),
        ("a commit whose blob bytes are not its digest", child._replace(blobs=forged_blobs)),
        ("a commit whose tree names a blob not stored", child._replace(blobs={})),
        (
            "a commit with an upper-case revision ID",
            child._replace(revision=revision.replace(id=revision.id.upper())),
        ),
        (
            "a commit with a path in its revision ID",
            child._replace(revision=revision.replace(id="../" + revision.id[3:])),
        ),
        (
            "a commit with a malformed parent ID",
            child._replace(revision=revision.replace(parent=base.revision.id.upper())),
        ),
        (
            "a commit whose revision is its own parent",
            child._replace(revision=revision.replace(id=base.revision.id)),
        ),
        (
            "a commit with a path as a blob digest",
            child._replace(
                tree={**base.tree, A_PATH: "../" + digest[3:]}, blobs={"../" + digest[3:]: b"x"}
            ),
        ),
    ]


def failed_commit_changes_nothing(backend: RepositoryBackend) -> None:
    """Checks that a refused commit, including a show's first, leaves every read unchanged."""
    first = next_commit(ROOT_REVISION, {}, {A_PATH: b"claimed"})
    refused = first._replace(blobs={blob_digest(b"claimed"): b"forged"})
    raised(StorageError, partial(land, backend, SHOW_ID, refused), "a forged first commit")
    no_show = backend.shows() == [] and backend.head(SHOW_ID) == ROOT_REVISION
    require("a refused first commit left a show behind", holds=no_show)
    base = _first(backend, SHOW_ID, b"base")
    before = state_of(backend, (SHOW_ID,))
    for what, commit in _forged(base):
        raised(StorageError, partial(land, backend, SHOW_ID, commit), what)
        require_unchanged(backend, before, (SHOW_ID,), what)


def log_since_is_ordered(backend: RepositoryBackend) -> None:
    """Checks that log() lists revisions oldest first and log(since=) returns the later ones."""
    commits = _history(backend, SHOW_ID, HISTORY)
    other = _history(backend, OTHER_SHOW_ID, 2)
    ids = [commit.revision.id for commit in commits]
    log = backend.log(SHOW_ID)
    require("log() does not list the revisions oldest first", holds=[r.id for r in log] == ids)
    parents = [r.parent for r in log]
    require(
        "a log entry's parent is not the one before", holds=parents == [ROOT_REVISION, *ids[:-1]]
    )
    for position, revision_id in enumerate(ids):
        later = backend.log(SHOW_ID, since=revision_id)
        require(f"log(since=) after entry {position} is wrong", holds=later == log[position + 1 :])
    unknown = partial(backend.log, SHOW_ID, since=UNKNOWN_ID)
    raised(UnknownRefError, unknown, "log() since a revision the show does not hold")
    foreign = partial(backend.log, SHOW_ID, since=other[0].revision.id)
    raised(UnknownRefError, foreign, "log() since another show's revision")


def head_matches_log(backend: RepositoryBackend) -> None:
    """Checks that the head is the last log entry after every commit, show by show."""
    require("an unknown show has a head", holds=backend.head(SHOW_ID) == ROOT_REVISION)
    for count, commit in enumerate(_landing(backend, SHOW_ID, HISTORY), start=1):
        log, head = backend.log(SHOW_ID), backend.head(SHOW_ID)
        require(
            f"the log holds {len(log)} revisions after {count} commits", holds=len(log) == count
        )
        require(
            "the head is not the last log entry", holds=head == log[-1].id == commit.revision.id
        )
        require("log(since=head) is not empty", holds=backend.log(SHOW_ID, since=head) == [])
    head = backend.head(SHOW_ID)
    _history(backend, OTHER_SHOW_ID, 1)
    require("a commit to another show moved this show's head", holds=backend.head(SHOW_ID) == head)
    shows = sorted(backend.shows())
    require(
        "shows() does not list exactly the shows written", holds=shows == [SHOW_ID, OTHER_SHOW_ID]
    )


def blob_reads_are_verified(backend: RepositoryBackend) -> None:
    """Checks that every blob a tree names hashes to its digest and a missing one fails closed."""
    _history(backend, SHOW_ID, HISTORY)
    for revision in backend.log(SHOW_ID):
        for path, digest in backend.tree(SHOW_ID, revision.id).items():
            data = backend.blob(SHOW_ID, digest)
            require(
                f"the blob read for {path} is not its digest", holds=blob_digest(data) == digest
            )
    raised(StorageError, partial(backend.blob, SHOW_ID, UNKNOWN_ID), "reading a missing blob")
    lazy = LazyBlobs({A_PATH: UNKNOWN_ID}, partial(backend.blob, SHOW_ID))
    raised(StorageError, partial(lazy.__getitem__, A_PATH), "a document whose blob is missing")
    raised(StorageError, partial(lazy.get, A_PATH), "get() of a document whose blob is missing")


def _require_refused_reads(backend: RepositoryBackend, revision_id: str, digest: str) -> None:
    """Requires reads by IDs a show does not hold, or that are malformed, to fail closed."""
    bad_revisions = (revision_id.upper(), "../" + revision_id[3:], revision_id[:-1])
    for bad in bad_revisions:
        raised(UnknownRefError, partial(backend.tree, SHOW_ID, bad), f"tree({bad!r})")
        raised(UnknownRefError, partial(backend.log, SHOW_ID, since=bad), f"log(since={bad!r})")
    for bad in (digest.upper(), "../" + digest[3:], digest[:-1], ""):
        raised(StorageError, partial(backend.blob, SHOW_ID, bad), f"blob({bad!r})")


def ids_are_never_trusted(backend: RepositoryBackend) -> None:
    """Checks that malformed IDs, and other shows' revisions and blobs, are refused."""
    base = _first(backend, SHOW_ID, b"base")
    other = _first(backend, OTHER_SHOW_ID, b"only in show_2")
    foreign = other.tree[A_PATH]
    raised(StorageError, partial(backend.blob, SHOW_ID, foreign), "reading another show's blob")
    foreign_tree = partial(backend.tree, SHOW_ID, other.revision.id)
    raised(UnknownRefError, foreign_tree, "reading another show's tree")
    shows = (SHOW_ID, OTHER_SHOW_ID)
    before = state_of(backend, shows)
    borrowed = next_commit(base.revision.id, base.tree, {B_PATH: b"only in show_2"})
    what = "a commit naming another show's blob without its bytes"
    raised(StorageError, partial(land, backend, SHOW_ID, borrowed._replace(blobs={})), what)
    require_unchanged(backend, before, shows, what)
    _require_refused_reads(backend, base.revision.id, base.tree[A_PATH])


def _failing_calls(
    backend: RepositoryBackend, base: Commit
) -> list[tuple[type[StorageError] | type[UnknownRefError], Callable[[], object], str]]:
    """Lists calls that must fail on a show holding `base`, each with its error class."""
    stale = next_commit(ROOT_REVISION, {}, {A_PATH: b"stale"})
    forged = next_commit(base.revision.id, base.tree, {A_PATH: b"x"})
    forged = forged._replace(blobs={blob_digest(b"x"): b"y"})
    return [
        (ConflictError, partial(land, backend, SHOW_ID, stale), "a stale commit"),
        (StorageError, partial(land, backend, SHOW_ID, forged), "a forged commit"),
        (StorageError, partial(backend.blob, SHOW_ID, UNKNOWN_ID), "a missing blob"),
        (StorageError, partial(backend.blob, OTHER_SHOW_ID, UNKNOWN_ID), "an unknown show's blob"),
        (UnknownRefError, partial(backend.tree, SHOW_ID, UNKNOWN_ID), "an unknown tree"),
        (UnknownRefError, partial(backend.tree, OTHER_SHOW_ID, base.revision.id), "a show's tree"),
        (UnknownRefError, partial(backend.log, SHOW_ID, since=UNKNOWN_ID), "an unknown since"),
    ]


def errors_are_storage_errors(backend: RepositoryBackend) -> None:
    """Checks that every failure is a library error of its kind, with a code, message and hint."""
    base = _first(backend, SHOW_ID, b"base")
    for error_class, call, what in _failing_calls(backend, base):
        error = raised(error_class, call, what)
        is_taught = bool(error.message) and bool(error.hint) and error.code in ErrorCode
        require(f"{what} raised an error without a code, message or hint", holds=is_taught)


def _stored_or_refused(backend: RepositoryBackend, show_id: str, commit: Commit) -> bool:
    """Lands a commit, or requires its StorageError to leave the show unchanged.

    Returns:
        is_stored: Whether the commit landed and reads back exactly as sent.
    """
    before = state_of(backend, (show_id,))
    try:
        land(backend, show_id, commit)
    except StorageError:
        require_unchanged(backend, before, (show_id,), f"a commit to {show_id[:40]!r}")
        return False
    require(f"{show_id[:40]!r} lost its commit", holds=backend.head(show_id) == commit.revision.id)
    tree = dict(backend.tree(show_id, commit.revision.id))
    require(f"the tree of {show_id[:40]!r} is not the one sent", holds=tree == commit.tree)
    for digest, data in commit.blobs.items():
        require(f"a blob of {show_id[:40]!r} changed", holds=backend.blob(show_id, digest) == data)
    return True


def unicode_and_case_paths(backend: RepositoryBackend) -> None:
    """Checks that names differing in case or Unicode form stay apart or fail, never merge."""
    paths = {path: path.encode() for path in CASE_PATHS}
    _stored_or_refused(backend, SHOW_ID, next_commit(ROOT_REVISION, {}, paths))
    for first_id, second_id in CASE_SHOW_PAIRS:
        first = _first(backend, first_id, first_id.encode())
        second = next_commit(ROOT_REVISION, {}, {A_PATH: second_id.encode()})
        is_apart = _stored_or_refused(backend, second_id, second)
        kept_blob = backend.blob(first_id, first.tree[A_PATH]) == first_id.encode()
        kept = backend.head(first_id) == first.revision.id and kept_blob
        require(f"writing show {second_id!r} changed show {first_id!r}", holds=kept)
        if is_apart:
            require("shows() merges two IDs", holds={first_id, second_id} <= set(backend.shows()))


def long_names(backend: RepositoryBackend) -> None:
    """Checks that a long show ID and long document paths are stored whole or refused."""
    show_id = "s" * LONG_NAME_CHARS
    deep = "/renders/" + "/".join(["d" * (LONG_SEGMENT_CHARS - 1)] * 4)
    paths = {
        deep[: LONG_NAME_CHARS - len(".txt")] + ".txt": b"deep",
        "/renders/" + "n" * (LONG_SEGMENT_CHARS - len(".txt")) + ".txt": b"wide",
    }
    first = next_commit(ROOT_REVISION, {}, paths)
    if not _stored_or_refused(backend, show_id, first):
        return
    second = next_commit(first.revision.id, first.tree, {A_PATH: b"after"})
    if _stored_or_refused(backend, show_id, second):
        require(
            "a long show ID's log misses a revision",
            holds=backend.log(show_id) == [first.revision, second.revision],
        )
