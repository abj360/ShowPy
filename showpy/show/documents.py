"""
documents.py --- splits a Show into canonical JSON documents at workspace paths and back

Contains:
    SHOW_DOC: names the document holding show metadata, cast and set order
    split_path(): checks a workspace path and returns its segments, rejecting traversal
    dump_json(): writes JSON canonically: sorted keys, sorted sets, two-space indent
    set_doc(): returns the path of a set's placements document
    script_doc(): returns the path of a set's build script
    forms_doc(): returns the path of a set's forms document
    placements_doc(): returns the path of a set's derived placements table
    paths_doc(): returns the path of a transition's paths document
    is_show_doc(): reports whether a path is part of the encoded Show
    encode_show(): splits a show into documents
    decode_show(): assembles a show from documents
"""

import itertools
import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic_core import to_jsonable_python

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.errors import SchemaError, StorageError
from showpy.core.ids import SetId, TransitionId
from showpy.show.manage import SET_ID_PATTERN
from showpy.show.models import Show

SHOW_DOC = "/show/show.json"
SHEET_DOC = "/show/sheet.json"
NOTES_DOC = "/show/notes.json"
TIMELINE_DOC = "/time/timeline.json"
SET_ORDER_FIELD = "sets"
SPLIT_FIELDS = frozenset({"sheet", "notes", "timeline", "sets", "forms", "transitions", "revision"})
SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
SET_DOC_NAME = "set.json"
SCRIPT_NAME = "set.py"
FORMS_NAME = "forms.json"
PLACEMENTS_NAME = "placements.csv"
PATHS_NAME = "paths.json"
SHOW_DOC_PATTERN = re.compile(
    r"^/(show/(show|sheet|notes)\.json|time/timeline\.json|sets/[^/]+/(set|forms)\.json"
    r"|transitions/[^/]+/paths\.json)$"
)
JSON_INDENT = 2


def split_path(path: str) -> list[str]:
    r"""Checks a workspace path and returns its segments; `..`, `.`, `//` and `\\` are refused.

    >>> split_path("/sets/12A/set.py")
    ['sets', '12A', 'set.py']
    """
    segments = path.split("/")[1:] if path.startswith("/") else []
    if path.endswith("/") and len(segments) > 1:
        segments = segments[:-1]
    if path == "/":
        return []
    if not segments or not all(SEGMENT_PATTERN.match(s) and ".." not in s for s in segments):
        raise StorageError(
            f"path {path!r} is not a workspace path",
            hint="use an absolute path such as /sets/1/set.py, without '..', '.' or empty parts",
            context={"path": path},
            code="BAD_PATH",
        )
    return segments


# Any: canonicalizes arbitrary JSON-like values produced by model_dump.
def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v) for v in value)
    return to_jsonable_python(value)


# Any: accepts any JSON-like value or model dump.
def dump_json(value: Any) -> bytes:
    r"""Writes JSON canonically so equal content always gives equal bytes and hashes.

    >>> dump_json({"b": frozenset({"y", "x"}), "a": 1})
    b'{\n  "a": 1,\n  "b": [\n    "x",\n    "y"\n  ]\n}\n'
    """
    text = json.dumps(_canonical(value), indent=JSON_INDENT, sort_keys=True, ensure_ascii=False)
    return f"{text}\n".encode()


def set_doc(set_id: SetId) -> str:
    """Returns the path of a set's placements document, which storage keeps internal."""
    return f"/sets/{set_id}/{SET_DOC_NAME}"


def script_doc(set_id: SetId) -> str:
    """Returns the path of a set's build script."""
    return f"/sets/{set_id}/{SCRIPT_NAME}"


def forms_doc(set_id: SetId) -> str:
    """Returns the path of a set's forms document."""
    return f"/sets/{set_id}/{FORMS_NAME}"


def placements_doc(set_id: SetId) -> str:
    """Returns the path of a set's derived placements table."""
    return f"/sets/{set_id}/{PLACEMENTS_NAME}"


def paths_doc(tid: TransitionId) -> str:
    """Returns the path of a transition's paths document.

    >>> paths_doc(("12", "12A"))
    '/transitions/12-12A/paths.json'
    """
    return f"/transitions/{tid[0]}-{tid[1]}/{PATHS_NAME}"


def is_show_doc(path: str) -> bool:
    """Reports whether a path is one of the documents encode_show() writes.

    >>> is_show_doc("/sets/1/forms.json"), is_show_doc("/sets/1/set.py")
    (True, False)
    """
    return SHOW_DOC_PATTERN.match(path) is not None


def _require_storable(show: Show) -> None:
    bad = [s for s in show.set_ids() if not SET_ID_PATTERN.match(s) or ".." in s]
    pairs = set(show.transition_ids())
    loose = [spec.id for spec in show.transitions if spec.id not in pairs]
    orphans = [f for f, form in show.forms.items() if form.set not in show.set_ids()]
    if bad or loose or orphans:
        raise SchemaError(
            "show cannot be stored",
            hint="use set ids of letters, digits, _ and .; store paths only for consecutive "
            "sets; give every form an existing set",
            context={"bad_set_ids": bad, "loose_transitions": loose, "orphan_forms": orphans},
        )


def encode_show(show: Show) -> dict[str, bytes]:
    """Splits a show into documents; empty forms and stored-path documents are left out.

    >>> from showpy.examples import two_set_show
    >>> sorted(encode_show(two_set_show()))[:3]
    ['/sets/1/set.json', '/sets/2/set.json', '/show/notes.json']
    """
    _require_storable(show)
    meta = show.model_dump(mode="python", exclude=set(SPLIT_FIELDS))
    docs = {
        SHOW_DOC: dump_json({**meta, SET_ORDER_FIELD: show.set_ids()}),
        SHEET_DOC: dump_json(show.sheet.model_dump(mode="python")),
        NOTES_DOC: dump_json([n.model_dump(mode="python") for n in show.notes]),
        TIMELINE_DOC: dump_json(show.timeline.model_dump(mode="python")),
    }
    for current in show.sets:
        docs[set_doc(current.id)] = dump_json(current.model_dump(mode="python"))
        forms = [f for _, f in sorted(show.forms.items()) if f.set == current.id]
        if forms:
            docs[forms_doc(current.id)] = dump_json([f.model_dump(mode="python") for f in forms])
    for spec in show.transitions:
        docs[paths_doc(spec.id)] = dump_json(spec.model_dump(mode="python"))
    return docs


# Any: parsed JSON documents hold arbitrary JSON values.
def _load(docs: Mapping[str, bytes], path: str, default: Any = None) -> Any:
    raw = docs.get(path)
    if raw is None:
        if default is None:
            raise StorageError(
                f"document {path} is missing",
                hint="write the whole show with save_show() first",
                context={"path": path},
            )
        return default
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(
            f"{path} is not valid JSON: {exc}",
            hint="write UTF-8 JSON",
            context={"path": path},
        ) from exc


def _forms(docs: Mapping[str, bytes], set_id: SetId) -> dict[str, Form]:
    forms = [Form.model_validate(f) for f in _load(docs, forms_doc(set_id), [])]
    wrong = [f.id for f in forms if f.set != set_id]
    if wrong:
        raise SchemaError(
            f"{forms_doc(set_id)} holds forms of another set: {', '.join(wrong)}",
            hint=f"give every form in this file set={set_id!r}",
            context={"set": set_id, "forms": wrong},
        )
    return {f.id: f for f in forms}


def _paths(docs: Mapping[str, bytes], tid: TransitionId) -> list[TransitionSpec]:
    raw = _load(docs, paths_doc(tid), {})
    if not raw:
        return []
    spec = TransitionSpec.model_validate(raw)
    if spec.id != tid:
        raise SchemaError(
            f"{paths_doc(tid)} holds paths for {spec.id}",
            hint=f"set id to {list(tid)}",
            context={"tid": tid, "found": spec.id},
        )
    return [spec]


def _assemble(docs: Mapping[str, bytes], revision: str) -> Show:
    meta = dict(_load(docs, SHOW_DOC))
    order: list[SetId] = list(meta.pop(SET_ORDER_FIELD, []))
    sets = [Set.model_validate(_load(docs, set_doc(s))) for s in order]
    forms = {k: v for s in order for k, v in _forms(docs, s).items()}
    pairs = list(itertools.pairwise(order))
    specs = [spec for tid in pairs for spec in _paths(docs, tid)]
    counts = {s.id: s.counts for s in sets}
    specs = [spec.model_copy(update={"counts": counts[spec.id[1]]}) for spec in specs]
    return Show.model_validate(
        {
            **meta,
            "sheet": _load(docs, SHEET_DOC),
            "notes": _load(docs, NOTES_DOC, []),
            "timeline": _load(docs, TIMELINE_DOC),
            "sets": sets,
            "forms": forms,
            "transitions": specs,
            "revision": revision,
        }
    )


def decode_show(docs: Mapping[str, bytes], revision: str = "") -> Show:
    """Assembles a show from documents; paths take the destination set's counts.

    Args:
        docs: Path to document bytes; documents other than show documents are ignored.
        revision: Revision the documents were read at.

    Returns:
        show: The assembled show, raising SchemaError when a document does not fit.

    >>> from showpy.examples import two_set_show
    >>> decode_show(encode_show(two_set_show())) == two_set_show()
    True
    """
    try:
        return _assemble(docs, revision)
    except SchemaError as exc:
        if "errors" not in exc.context:
            raise
        raise SchemaError(
            "show documents do not match the Show schema",
            hint="write documents produced by save_show(), or fix the fields named in context",
            context={"errors": exc.context["errors"]},
        ) from exc
