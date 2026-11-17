"""
files.py --- maps workspace file paths to show documents: what each path reads, writes and owns

Workspace paths address sets by display ID; stored trees address them by key, and every
crossing goes through showpy.show.set_keys.

Contains:
    INDEX_DOC: names the derived set index table
    LIMITS_DOC: names the designer's limits file
    WAIVERS_DOC: names the designer's waivers file
    CLAIMS_DOC: names the published claims file
    all_files(): lists every readable file path
    directories(): lists every directory path, each ending in `/`
    read_file(): returns a file's text or bytes
    show_changes(): lists the stored-document writes and deletes that store a show
    staged_changes(): validates one file write and returns the stored-document changes it makes
"""

import csv
import io
from collections.abc import Mapping

from showpy.choreo.models import Set
from showpy.core.csv_cells import neutralize_cell
from showpy.core.errors import LockedError, StorageError, UnknownRefError
from showpy.core.ids import TransitionId
from showpy.core.selection import Selection
from showpy.core.util import close_matches
from showpy.show.documents import (
    FORMS_NAME,
    NOTES_DOC,
    PATHS_NAME,
    PLACEMENTS_NAME,
    SCRIPT_NAME,
    SHEET_DOC,
    SHOW_DOC,
    TIMELINE_DOC,
    decode_show,
    dump_json,
    encode_documents,
    encode_show,
    forms_doc,
    is_show_doc,
    paths_doc,
    placements_doc,
    script_doc,
    split_path,
)
from showpy.show.manage import index
from showpy.show.models import Show
from showpy.show.set_keys import SetFolders, set_key, to_stored_path
from showpy.show.structured import (
    COMMENTS_DOC,
    SELECTION_DOC,
    TEXT_SUFFIXES,
    decode_text,
    require_structured,
)

INDEX_DOC = "/show/index.csv"
LIMITS_DOC = "/config/limits.yaml"
WAIVERS_DOC = "/config/waivers.yaml"
CLAIMS_DOC = "/publish/claims.json"
FREE_ROOTS = ("/renders/", "/exports/", "/lib/drafts/")
FIXED_DIRS = (
    "/", "/canvas/", "/config/", "/exports/", "/lib/", "/lib/drafts/", "/publish/", "/renders/",
    "/sets/", "/show/", "/time/", "/transitions/",
)  # fmt: skip
INDEX_COLUMNS = ("set", "counts", "start_count", "measure", "seconds", "cue")
EMPTY_LIST = dump_json([])
DEFAULTS = {
    LIMITS_DOC: b"",
    WAIVERS_DOC: b"",
    SELECTION_DOC: dump_json(Selection(ids=()).model_dump(mode="json")),
    COMMENTS_DOC: EMPTY_LIST,
}


def _set_files(show: Show, tree: Mapping[str, bytes]) -> list[str]:
    folders = SetFolders(show)
    files: list[str] = []
    for set_id in show.set_ids():
        files += [forms_doc(set_id), placements_doc(set_id)]
        files += [script_doc(set_id)] if folders.to_stored(script_doc(set_id)) in tree else []
    return files


def all_files(show: Show | None, tree: Mapping[str, bytes]) -> list[str]:
    """Lists every readable file path: fixed files, per-set and per-transition files, stored files.

    >>> all_files(None, {"/renders/a.png": b""})
    ['/canvas/comments.json', '/canvas/selection.json', '/config/limits.yaml', \
'/config/waivers.yaml', '/renders/a.png']
    """
    files = sorted(DEFAULTS)
    files += [CLAIMS_DOC] if CLAIMS_DOC in tree else []
    files += sorted(p for p in tree if p.startswith(FREE_ROOTS))
    if show is None:
        return files
    files += [SHOW_DOC, INDEX_DOC, SHEET_DOC, NOTES_DOC, TIMELINE_DOC]
    files += _set_files(show, tree)
    files += [paths_doc(tid) for tid in show.transition_ids()]
    return files


def directories(show: Show | None, tree: Mapping[str, bytes]) -> set[str]:
    """Lists every directory path, each ending in `/`.

    >>> sorted(directories(None, {"/lib/drafts/x/y.py": b""}))[:3]
    ['/', '/canvas/', '/config/']
    """
    found: set[str] = set(FIXED_DIRS)
    for path in all_files(show, tree):
        parts = path.split("/")[1:-1]
        found.update("/" + "".join(f"{p}/" for p in parts[:n]) for n in range(1, len(parts) + 1))
    return found


def _text_or_bytes(path: str, raw: bytes) -> str | bytes:
    return decode_text(path, raw) if path.endswith(TEXT_SUFFIXES) else raw


def _unknown(path: str, show: Show | None, tree: Mapping[str, bytes]) -> UnknownRefError:
    close = close_matches(path, all_files(show, tree))
    return UnknownRefError(
        f"no file {path}",
        hint=f"did you mean {', '.join(close)}?" if close else "ls('/') lists what exists",
        context={"path": path},
    )


def _is_directory(path: str) -> StorageError:
    return StorageError(
        f"{path} is a directory",
        hint="name a file path such as /renders/set-2.png; ls() lists directories",
        context={"path": path},
        code="BAD_PATH",
    )


def _require_show(show: Show | None, path: str) -> Show:
    if show is None:
        raise StorageError(
            f"{path} needs a saved show",
            hint="save the show first with save_show()",
            context={"path": path},
        )
    return show


def _pair(show: Show, segment: str) -> TransitionId:
    for tid in show.transition_ids():
        if f"{tid[0]}-{tid[1]}" == segment:
            return tid
    names = [f"{a}-{b}" for a, b in show.transition_ids()]
    close = close_matches(segment, names)
    raise UnknownRefError(
        f"no transition {segment}",
        hint=f"did you mean {', '.join(close)}?" if close else f"known: {', '.join(names)}",
        context={"transition": segment},
    )


def _index_csv(show: Show) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(INDEX_COLUMNS)
    for e in index(show).entries:
        label = "" if e.measure is None else f"{e.measure.measure}:{e.measure.count_in_measure:g}"
        writer.writerow(
            [e.set, e.counts, e.start_count, label, e.seconds, neutralize_cell(e.cue or "")]
        )
    return out.getvalue().encode()


def _placements_csv(current: Set) -> bytes:
    table = current.table().map(
        lambda cell: neutralize_cell(cell) if isinstance(cell, str) else cell
    )
    return table.to_csv(index=False, lineterminator="\n").encode()


def _show_file(path: str, segments: list[str], show: Show) -> bytes | None:
    if path == INDEX_DOC:
        return _index_csv(show)
    if segments[0] == "sets" and len(segments) == 3:
        current = show.set(segments[1])
        if segments[2] == PLACEMENTS_NAME:
            return _placements_csv(current)
        if segments[2] == FORMS_NAME:
            stored = to_stored_path(path, show)
            return encode_documents(show, [stored]).get(stored, EMPTY_LIST)
    if segments[0] == "transitions" and len(segments) == 3 and segments[2] == PATHS_NAME:
        tid = _pair(show, segments[1])
        return dump_json(show.transition(tid).model_dump(mode="json"))
    if path in (SHOW_DOC, SHEET_DOC, NOTES_DOC, TIMELINE_DOC):
        return encode_documents(show, [path])[path]
    return None


def read_file(path: str, show: Show | None, tree: Mapping[str, bytes]) -> str | bytes:
    """Returns a file: derived tables are computed, missing designer files read as defaults.

    Args:
        path: Workspace path.
        show: Show at the workspace revision; None before the first save.
        tree: Stored documents at the workspace revision, addressed by set key.

    Returns:
        content: Text for text files, bytes otherwise.

    >>> read_file("/config/limits.yaml", None, {})
    ''
    """
    segments = split_path(path)
    if not segments:
        raise _is_directory(path)
    if segments[0] in ("show", "time", "sets", "transitions"):
        found = _show_file(path, segments, _require_show(show, path))
        if found is not None:
            return _text_or_bytes(path, found)
    stored = tree.get(to_stored_path(path, show), DEFAULTS.get(path))
    is_listed = path in DEFAULTS or path == CLAIMS_DOC or path.startswith(FREE_ROOTS)
    if stored is None or not (is_listed or path.endswith(f"/{SCRIPT_NAME}")):
        raise _unknown(path, show, tree)
    return _text_or_bytes(path, stored)


def show_changes(show: Show, tree: Mapping[str, bytes]) -> dict[str, bytes | None]:
    """Lists the stored writes and deletes that store `show` over `tree`, with removed sets' files.

    Paths are stored paths: set folders are keys, so a renumbered set keeps its folder.

    >>> from showpy.examples import two_set_show
    >>> changes = show_changes(two_set_show(), {"/sets/9/set.py": b""})
    >>> changes["/sets/9/set.py"], len(changes)
    (None, 7)
    """
    docs = encode_show(show)
    kept = tuple(f"/sets/{set_key(s)}/" for s in show.sets)
    stale = [
        p
        for p in tree
        if p not in docs and (is_show_doc(p) or (p.startswith("/sets/") and not p.startswith(kept)))
    ]
    changes: dict[str, bytes | None] = {p: d for p, d in docs.items() if tree.get(p) != d}
    changes.update(dict.fromkeys(stale))
    return changes


def _staged_show_doc(
    path: str, raw: bytes, show: Show, tree: Mapping[str, bytes]
) -> dict[str, bytes | None]:
    segments = split_path(path)
    if segments[0] == "sets":
        show.set(segments[1])
    if segments[0] == "transitions":
        _pair(show, segments[1])
    stored = to_stored_path(path, show)
    edited = decode_show({**encode_show(show), stored: raw}, show.revision)
    return show_changes(edited, tree)


def staged_changes(
    path: str, raw: bytes, show: Show | None, tree: Mapping[str, bytes]
) -> dict[str, bytes | None]:
    """Validates one file write and returns the stored-document changes it makes.

    Show documents are re-encoded canonically from the edited show, so a write can also
    delete documents (writing `[]` to forms.json or `{}` to paths.json clears them).

    >>> staged_changes("/renders/a.png", b"png", None, {})
    {'/renders/a.png': b'png'}
    """
    segments = split_path(path)
    if path.endswith("/"):
        raise _is_directory(path)
    if path == INDEX_DOC or (segments[0] == "sets" and segments[-1] == PLACEMENTS_NAME):
        raise LockedError(
            f"{path} is derived and read-only",
            hint="edit forms.json, set.py or paths instead; the table follows",
            context={"path": path},
            code="READ_ONLY",
        )
    if is_show_doc(path) and not path.endswith("/set.json"):
        return _staged_show_doc(path, raw, _require_show(show, path), tree)
    if segments[0] == "sets" and len(segments) == 3 and segments[2] == SCRIPT_NAME:
        _require_show(show, path).set(segments[1])
    elif not (path in DEFAULTS or path == CLAIMS_DOC or path.startswith(FREE_ROOTS)):
        raise _unknown(path, show, tree)
    require_structured(path, raw)
    return {to_stored_path(path, show): raw}
