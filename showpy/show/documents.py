"""
documents.py --- splits a Show into canonical JSON documents at stored paths and back

Stored paths address sets by key (`/sets/<key>/…`, `/transitions/<key a>-<key b>/…`) and
show.json lists the set order as keys; workspaces map display IDs onto them.

Contains:
    SHOW_DOC: names the document holding show metadata, cast and set order
    split_path(): checks a workspace path and returns its segments, rejecting traversal
    dump_json(): writes JSON canonically: sorted keys, sorted sets, two-space indent
    dump_dumped_json(): writes the JSON-mode dump of a model canonically, skipping a second walk
    set_doc(): returns the path of a set folder's placements document
    script_doc(): returns the path of a set folder's build script
    forms_doc(): returns the path of a set folder's forms document
    placements_doc(): returns the path of a set folder's derived placements table
    paths_doc(): returns the path of a transition folder's paths document
    is_show_doc(): reports whether a path is part of the encoded Show
    is_storable_folder(): reports whether a set folder name can be stored
    expected_show_docs(): lists the stored show-document paths decode reads for a show
    encode_show(): splits a show into documents stored by set key
    encode_documents(): encodes only the named stored documents of a show
    decode_show(): assembles a show from documents, reusing cached per-document decodes
"""

import itertools
import json
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from functools import partial
from typing import Any

from pydantic import BaseModel

from showpy.choreo.models import Form, Set, TransitionSpec
from showpy.core.canonical import MAX_INT_DIGITS, MAX_NESTING, canonical_value
from showpy.core.errors import SchemaError, StorageError, field_errors
from showpy.core.ids import SetId, TransitionId, check_id
from showpy.show.decode_cache import DecodeCache
from showpy.show.manage import SET_ID_PATTERN
from showpy.show.models import Show
from showpy.show.set_keys import set_key

SHOW_DOC = "/show/show.json"
SHEET_DOC = "/show/sheet.json"
NOTES_DOC = "/show/notes.json"
TIMELINE_DOC = "/time/timeline.json"
SET_ORDER_FIELD = "sets"
SPLIT_FIELDS = frozenset({"sheet", "notes", "timeline", "sets", "forms", "transitions", "revision"})
JsonObject = dict[str, Any]  # Any: parsed JSON values of any type
JsonArray = list[Any]  # Any: parsed JSON values of any type
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.\-]*"
SEGMENT_PATTERN = re.compile(SEGMENT)
SET_DOC_NAME = "set.json"
SCRIPT_NAME = "set.py"
FORMS_NAME = "forms.json"
PLACEMENTS_NAME = "placements.csv"
PATHS_NAME = "paths.json"
SHOW_DOC_PATTERN = re.compile(
    rf"/(show/(show|sheet|notes)\.json|time/timeline\.json|sets/{SEGMENT}/(set|forms)\.json"
    rf"|transitions/{SEGMENT}/paths\.json)"
)
JSON_INDENT = 2
NEGATIVE_ZERO = re.compile(r"-0\.0\b")
RESERVED_KEY_PREFIX = "$showpy:"
TOO_DEEP = "\n" + " " * (JSON_INDENT * (MAX_NESTING + 1))


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
    if not segments or not all(SEGMENT_PATTERN.fullmatch(s) and ".." not in s for s in segments):
        raise StorageError(
            f"path {path!r} is not a workspace path",
            hint="use an absolute path such as /sets/1/set.py, without '..', '.' or empty parts",
            context={"path": path},
            code="BAD_PATH",
        )
    return segments


# Any: accepts any JSON-like value or model dump.
def dump_json(value: Any) -> bytes:
    r"""Writes JSON canonically so equal content always gives equal bytes and hashes.

    >>> dump_json({"b": frozenset({"y", "x"}), "a": 1})
    b'{\n  "a": 1,\n  "b": [\n    "x",\n    "y"\n  ]\n}\n'
    """
    text = json.dumps(
        canonical_value(value), indent=JSON_INDENT, sort_keys=True, ensure_ascii=False
    )
    return f"{text}\n".encode()


# Any: a model_dump(mode="json") result, whose JSON types and string keys are guaranteed.
def dump_dumped_json(dumped: Any) -> bytes:
    """Writes the JSON-mode dump of a model with the same bytes as dump_json, much faster.

    The dump already holds only JSON types and string keys, so one `json.dumps` pass is enough
    unless the text shows a value canonical_value must treat: `-0.0`, a reserved key prefix, a
    line long enough to hold a huge int or indented deeper than canonical_value allows. NaN,
    infinity and lone surrogates make the pass fail, which hands the value to dump_json so it
    raises the same SchemaError.

    >>> dump_dumped_json({"b": [1.5, None], "a": -0.0}) == dump_json({"b": [1.5, None], "a": -0.0})
    True
    """
    try:
        text = json.dumps(
            dumped, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False, allow_nan=False
        )
        encoded = f"{text}\n".encode()
    except (ValueError, UnicodeEncodeError):
        return dump_json(dumped)
    if _needs_normalizing(text):
        return dump_json(dumped)
    return encoded


def _needs_normalizing(text: str) -> bool:
    """Reports whether JSON text may hold a value that only canonical_value treats correctly.

    With one value per line and two spaces per level, an int past the digit cap of canonical_value
    makes a line longer than the cap, and a value nested past its depth cap makes a line indented
    past TOO_DEEP, so line length and indent stand in for a slow scan of every digit run.
    """
    if RESERVED_KEY_PREFIX in text:
        return True
    if "-0.0" in text and NEGATIVE_ZERO.search(text):
        return True
    if TOO_DEEP in text:
        return True
    return max(map(len, text.split("\n"))) > MAX_INT_DIGITS


def set_doc(folder: str) -> str:
    """Returns the path of a set's placements document, which storage keeps internal.

    The folder is the set key in stored paths and the display ID in workspace paths.
    """
    return f"/sets/{folder}/{SET_DOC_NAME}"


def script_doc(folder: str) -> str:
    """Returns the path of a set folder's build script."""
    return f"/sets/{folder}/{SCRIPT_NAME}"


def forms_doc(folder: str) -> str:
    """Returns the path of a set folder's forms document."""
    return f"/sets/{folder}/{FORMS_NAME}"


def placements_doc(folder: str) -> str:
    """Returns the path of a set folder's derived placements table."""
    return f"/sets/{folder}/{PLACEMENTS_NAME}"


def paths_doc(tid: TransitionId) -> str:
    """Returns the path of a transition folder's paths document: keys stored, IDs in workspaces.

    >>> paths_doc(("12", "12A"))
    '/transitions/12-12A/paths.json'
    """
    return f"/transitions/{tid[0]}-{tid[1]}/{PATHS_NAME}"


def is_show_doc(path: str) -> bool:
    """Reports whether a path is one of the documents encode_show() writes.

    >>> is_show_doc("/sets/1/forms.json"), is_show_doc("/sets/1/set.py")
    (True, False)
    """
    return SHOW_DOC_PATTERN.fullmatch(path) is not None


def _require_storable(show: Show) -> None:
    bad = [s for s in show.set_ids() if not SET_ID_PATTERN.fullmatch(s) or ".." in s]
    bad += [set_key(s) for s in show.sets if not is_storable_folder(set_key(s))]
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


def expected_show_docs(show: Show) -> frozenset[str]:
    """Returns every stored show-document path decode_show() reads for `show`, by set key.

    >>> from showpy.examples import two_set_show
    >>> "/transitions/1-2/paths.json" in expected_show_docs(two_set_show())
    True
    """
    order = [set_key(s) for s in show.sets]
    paths = {SHOW_DOC, SHEET_DOC, NOTES_DOC, TIMELINE_DOC}
    paths.update(set_doc(s) for s in order)
    paths.update(forms_doc(s) for s in order)
    paths.update(paths_doc(tid) for tid in itertools.pairwise(order))
    return frozenset(paths)


def encode_show(show: Show) -> dict[str, bytes]:
    """Splits a show into documents under set keys; empty forms and paths are left out.

    >>> from showpy.examples import two_set_show
    >>> sorted(encode_show(two_set_show()))[:3]
    ['/sets/1/set.json', '/sets/2/set.json', '/show/notes.json']
    """
    return {path: encode() for path, encode in _encoders(show).items()}


def encode_documents(show: Show, paths: Collection[str]) -> dict[str, bytes]:
    """Encodes only the named stored documents of a show; paths the show does not write are skipped.

    >>> from showpy.examples import two_set_show
    >>> sorted(encode_documents(two_set_show(), ["/show/sheet.json", "/sets/9/set.json"]))
    ['/show/sheet.json']
    """
    encoders = _encoders(show)
    return {path: encoders[path]() for path in paths if path in encoders}


def _encoders(show: Show) -> dict[str, Callable[[], bytes]]:
    """Maps each stored document path of a show to a call that encodes it."""
    _require_storable(show)
    keys = {s.id: set_key(s) for s in show.sets}
    meta = show.model_dump(mode="json", exclude=set(SPLIT_FIELDS))
    encoders: dict[str, Callable[[], bytes]] = {
        SHOW_DOC: lambda: dump_dumped_json({**meta, SET_ORDER_FIELD: list(keys.values())}),
        SHEET_DOC: lambda: dump_dumped_json(show.sheet.model_dump(mode="json")),
        NOTES_DOC: lambda: dump_dumped_json([n.model_dump(mode="json") for n in show.notes]),
        TIMELINE_DOC: lambda: dump_dumped_json(show.timeline.model_dump(mode="json")),
    }
    for current in show.sets:
        folder = keys[current.id]
        encoders[set_doc(folder)] = partial(_dump_model, current)
        forms = [f for _, f in sorted(show.forms.items()) if f.set == current.id]
        if forms:
            encoders[forms_doc(folder)] = partial(_dump_models, forms)
    for spec in show.transitions:
        folder_pair = (keys[spec.id[0]], keys[spec.id[1]])
        encoders[paths_doc(folder_pair)] = partial(_dump_model, spec)
    return encoders


def _dump_model(model: BaseModel) -> bytes:
    return dump_dumped_json(model.model_dump(mode="json"))


def _dump_models(models: Sequence[BaseModel]) -> bytes:
    return dump_dumped_json([m.model_dump(mode="json") for m in models])


def _load_json(docs: Mapping[str, bytes], path: str) -> object:
    raw = docs.get(path)
    if raw is None:
        raise StorageError(
            f"document {path} is missing",
            hint="write the whole show with save_show() first",
            context={"path": path},
        )
    return _parse(path, raw)


def _parse(path: str, raw: bytes) -> object:
    """Parses JSON, raising SchemaError for bad UTF-8 or JSON, deep nesting or an overlong int."""
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise SchemaError(
            f"{path} is not valid JSON: {exc}",
            hint=f"write UTF-8 JSON nested at most {MAX_NESTING} levels, ints of at most"
            f" {MAX_INT_DIGITS} digits, as save_show() does",
            context={"path": path},
        ) from exc


def _shape_error(path: str, name: str, value: object) -> SchemaError:
    return SchemaError(
        f"{path} must hold a JSON {name}, not {type(value).__name__}",
        hint=f"write a JSON {name}, as save_show() does",
        context={"path": path},
    )


def _load_object(docs: Mapping[str, bytes], path: str, *, is_optional: bool = False) -> JsonObject:
    """Parses a document that must hold a JSON object; an absent optional one reads as `{}`."""
    if is_optional and path not in docs:
        return {}
    value = _load_json(docs, path)
    if not isinstance(value, dict):
        raise _shape_error(path, "object", value)
    return value


def _load_array(docs: Mapping[str, bytes], path: str, *, is_optional: bool = False) -> JsonArray:
    """Parses a document that must hold a JSON array; an absent optional one reads as `[]`."""
    if is_optional and path not in docs:
        return []
    value = _load_json(docs, path)
    if not isinstance(value, list):
        raise _shape_error(path, "array", value)
    return value


def _cached[T](
    cache: DecodeCache | None, digests: Mapping[str, str], path: str, decode: Callable[[], T]
) -> T:
    digest = digests.get(path)
    if cache is None or digest is None:
        return decode()
    return cache.get_or_decode(path, digest, decode)


def _set(docs: Mapping[str, bytes], key: str) -> Set:
    raw = _load_object(docs, set_doc(key))
    if isinstance(raw.get("key"), str):
        check_id(raw["key"], kind="set key")
    if "key" not in raw:
        raw = {**raw, "key": key}
    loaded = Set.model_validate(raw)
    if loaded.key != key:
        raise SchemaError(
            f"{set_doc(key)} holds the set keyed {loaded.key}",
            hint=f"store each set under its key; drop key or set it to {key!r}",
            context={"path": set_doc(key), "key": loaded.key},
        )
    return loaded


def _forms(docs: Mapping[str, bytes], key: str) -> tuple[Form, ...]:
    return tuple(
        Form.model_validate(f) for f in _load_array(docs, forms_doc(key), is_optional=True)
    )


def _require_forms_for(key: str, set_id: SetId, forms: tuple[Form, ...]) -> None:
    wrong = [f.id for f in forms if f.set != set_id]
    if wrong:
        raise SchemaError(
            f"{forms_doc(key)} holds forms of another set: {', '.join(wrong)}",
            hint=f"give every form in this file set={set_id!r}",
            context={"set": set_id, "forms": wrong},
        )


def _paths(docs: Mapping[str, bytes], folder: TransitionId) -> tuple[TransitionSpec, ...]:
    raw = _load_object(docs, paths_doc(folder), is_optional=True)
    if not raw:
        return ()
    return (TransitionSpec.model_validate(raw),)


def _require_paths_for(
    folder: TransitionId, tid: TransitionId, specs: tuple[TransitionSpec, ...]
) -> None:
    for spec in specs:
        if spec.id != tid:
            raise SchemaError(
                f"{paths_doc(folder)} holds paths for {spec.id}",
                hint=f"set id to {list(tid)}",
                context={"tid": tid, "found": spec.id},
            )


def is_storable_folder(folder: object) -> bool:
    """Reports whether a set folder name can be stored: a v0.2 set ID or a key that passes check_id.

    Stored IDs that predate the ID pattern keep loading until they are migrated.

    >>> is_storable_folder("con"), is_storable_folder("_k"), is_storable_folder("1-2")
    (True, True, False)
    """
    if not isinstance(folder, str):
        return False
    if SET_ID_PATTERN.fullmatch(folder) and ".." not in folder:
        return True
    try:
        return check_id(folder, kind="set key") == folder
    except SchemaError:
        return False


def _set_order(order: object) -> list[str]:
    """Checks the set order show.json lists: an array of storable set keys, never text."""
    if not isinstance(order, list):
        raise _shape_error(SHOW_DOC, "array under 'sets'", order)
    bad = [k for k in order if not is_storable_folder(k)]
    if bad:
        raise SchemaError(
            f"{SHOW_DOC} lists set keys that cannot be stored: {', '.join(map(str, bad))}",
            hint="use set keys of letters, digits, _ and ., with no doubled .",
            context={"bad_set_keys": bad},
        )
    return order


def _transition_specs(
    docs: Mapping[str, bytes],
    stored: list[tuple[str, Set]],
    cache: DecodeCache | None,
    digests: Mapping[str, str],
) -> list[TransitionSpec]:
    specs: list[TransitionSpec] = []
    for (key_a, set_a), (key_b, set_b) in itertools.pairwise(stored):
        folder = (key_a, key_b)
        found = _cached(cache, digests, paths_doc(folder), partial(_paths, docs, folder))
        _require_paths_for(folder, (set_a.id, set_b.id), found)
        specs.extend(found)
    return specs


def _assemble(
    docs: Mapping[str, bytes], revision: str, cache: DecodeCache | None, digests: Mapping[str, str]
) -> Show:
    meta = _load_object(docs, SHOW_DOC)
    order = _set_order(meta.pop(SET_ORDER_FIELD, []))
    stored = [(k, _cached(cache, digests, set_doc(k), partial(_set, docs, k))) for k in order]
    forms: dict[str, Form] = {}
    for key, current in stored:
        found = _cached(cache, digests, forms_doc(key), partial(_forms, docs, key))
        _require_forms_for(key, current.id, found)
        forms.update((f.id, f) for f in found)
    sets = [current for _, current in stored]
    counts = {s.id: s.counts for s in sets}
    specs = [
        spec.replace(counts=counts[spec.id[1]])
        for spec in _transition_specs(docs, stored, cache, digests)
    ]
    return Show.model_validate(
        {
            **meta,
            "sheet": _load_object(docs, SHEET_DOC),
            "notes": _load_array(docs, NOTES_DOC, is_optional=True),
            "timeline": _load_object(docs, TIMELINE_DOC),
            "sets": sets,
            "forms": forms,
            "transitions": specs,
            "revision": revision,
        }
    )


def decode_show(
    docs: Mapping[str, bytes],
    revision: str = "",
    *,
    cache: DecodeCache | None = None,
    digests: Mapping[str, str] | None = None,
) -> Show:
    """Assembles a show from documents; paths take the destination set's counts.

    Args:
        docs: Path to document bytes; documents other than show documents are ignored.
        revision: Revision the documents were read at.
        cache: Decoded documents to reuse; the show is still validated whole.
        digests: Path to blob digest of each document, naming the cache entries; without it
            nothing is cached.

    Returns:
        show: The assembled show, raising SchemaError when a document does not fit.

    >>> from showpy.examples import two_set_show
    >>> decode_show(encode_show(two_set_show())) == two_set_show()
    True
    """
    try:
        return _assemble(docs, revision, cache, digests or {})
    except SchemaError as exc:
        errors = field_errors(exc)
        raise SchemaError(
            "show documents do not match the Show schema",
            hint="write documents produced by save_show(), or fix the fields named in context",
            context={"errors": errors},
        ) from exc
