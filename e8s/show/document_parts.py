"""
document_parts.py --- decodes the parts of a stored show one document at a time, through a cache

Each part is decoded from its own documents and checked against the documents it names: a set
against its folder, the forms of a set against the set's ID and the paths of a transition
against its two sets. A part decoded once is reused for every tree that holds the same bytes.

Contains:
    PartReading: holds how documents are read: the tree, the validation context and the cache
    PartReading.cached(): returns a cached decode of a document, or decodes it
    OlderTreeError: carries the show.json of an older format out of a cached decode
    read_current_meta(): returns a tree's show.json, raising OlderTreeError for an older format
    set_order(): checks the set keys show.json lists
    read_set(): decodes the set stored under a key
    read_forms(): decodes the forms stored with a set, refusing forms of another set
    read_paths(): decodes the paths of a transition folder, given its destination set's counts
    read_part(): decodes a show-level document, or returns its object when it is invalid
    read_checked_part(): decodes a show-level document, raising SchemaError when it is invalid
    is_same_float(): tells whether two floats are one value, signed zeros told apart
"""

import math
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import NamedTuple

from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import SetId, TransitionId, check_id
from e8s.core.types import FrozenModel
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_paths import (
    forms_doc,
    is_storable_folder,
    paths_doc,
    role_docs,
    set_doc,
)
from e8s.show.document_sources import (
    JsonObject,
    Source,
    load_array,
    load_object,
    shape_error,
)
from e8s.show.migrate import CURRENT_VERSION, SHOW_DOC, version_of
from e8s.show.models import require_set_count
from e8s.show.role_documents import read_set_object


class PartReading(NamedTuple):
    """Holds how one decode reads documents.

    Attributes:
        source: The tree's documents.
        context: Validation context of every model built from them.
        cache: Decoded documents to reuse, or None.
        digests: Path to blob digest, naming the cache entries.
    """

    source: Source
    context: Mapping[str, bool] | None = None
    cache: DecodeCache | None = None
    digests: Mapping[str, str] = {}

    def cached[T](
        self, path: str, decode: Callable[[], T], also: Sequence[str] = (), variant: str = ""
    ) -> T:
        """Returns a cached decode of a document and the `also` documents it reads, or decodes it.

        The cache key joins the digests of the documents present, so a change to any of them,
        or one appearing or going, misses the cache. `variant` names any other input the decode
        reads, such as the counts given to paths, so each input value gets its own entry.
        """
        digest = self.digests.get(path)
        present = [p for p in also if self.source.has(p)]
        if self.cache is None or digest is None or any(p not in self.digests for p in present):
            return decode()
        parts = [
            digest,
            *(f"{p}={self.digests[p]}" for p in present),
            *([variant] if variant else []),
        ]
        return self.cache.get_or_decode(path, " ".join(parts), decode)


class OlderTreeError(Exception):
    """Carries the show.json of an older format out of a cached decode, so none is cached.

    Attributes:
        meta: The parsed show.json.
        version: Its format version.
    """

    def __init__(self, meta: JsonObject, version: int) -> None:
        super().__init__(version)
        self.meta = meta
        self.version = version


def _current_meta(source: Source) -> JsonObject:
    meta = load_object(source, SHOW_DOC)
    version = version_of(meta, name=SHOW_DOC)
    if version < CURRENT_VERSION:
        raise OlderTreeError(meta, version)
    return meta


def read_current_meta(reading: PartReading) -> JsonObject:
    """Returns a tree's parsed show.json, decoded once per digest.

    Args:
        reading: How the tree's documents are read.

    Returns:
        meta: show.json with its header; an older format raises OlderTreeError, so the cache
            keeps none.
    """
    return reading.cached(SHOW_DOC, partial(_current_meta, reading.source))


def set_order(order: object) -> list[str]:
    """Checks the set order show.json lists: an array of at most MAX_SETS storable set keys.

    >>> set_order(["1", "2"])
    ['1', '2']
    """
    if not isinstance(order, list):
        raise shape_error(SHOW_DOC, "array under 'sets'", order)
    require_set_count(len(order))
    bad = [k for k in order if not is_storable_folder(k)]
    if bad:
        raise SchemaError(
            f"{SHOW_DOC} lists set keys that cannot be stored: {', '.join(map(str, bad))}",
            hint="use set keys of letters, digits, _ and ., with no doubled .",
            context={"bad_set_keys": bad},
        )
    return order


def _set(reading: PartReading, key: str) -> Set:
    raw = read_set_object(reading.source, key)
    stored_key = raw.get("key")
    if isinstance(stored_key, str):
        check_id(stored_key, kind="set key")
    if "key" not in raw:
        raw = {**raw, "key": key}
    loaded = Set._validated(raw, context=reading.context)
    if loaded.key != key:
        raise SchemaError(
            f"{set_doc(key)} holds the set keyed {loaded.key}",
            hint=f"store each set under its key; drop key or set it to {key!r}",
            context={"path": set_doc(key), "key": loaded.key},
        )
    return loaded


def read_set(reading: PartReading, key: str) -> Set:
    """Decodes the set stored under a key from set.json and its role documents, once per digests.

    Args:
        reading: How the tree's documents are read.
        key: The set's storage key, its folder under `/sets/`.

    Returns:
        current: The set, whose key must be its folder.
    """
    return reading.cached(set_doc(key), partial(_set, reading, key), also=role_docs(key))


def _forms(reading: PartReading, key: str) -> tuple[Form, ...]:
    stored = load_array(reading.source, forms_doc(key), is_optional=True)
    return tuple(Form._validated(f, context=reading.context) for f in stored)


def read_forms(reading: PartReading, key: str, set_id: SetId) -> tuple[Form, ...]:
    """Decodes the forms stored with a set, refusing a form that names another set.

    Args:
        reading: How the tree's documents are read.
        key: The set's storage key.
        set_id: The set's ID, which every form in the document must name.

    Returns:
        forms: The forms in stored order; none when the set stores no forms document.
    """
    found = reading.cached(forms_doc(key), partial(_forms, reading, key))
    wrong = [f.id for f in found if f.set != set_id]
    if wrong:
        raise SchemaError(
            f"{forms_doc(key)} holds forms of another set: {', '.join(wrong)}",
            hint=f"give every form in this file set={set_id!r}",
            context={"set": set_id, "forms": wrong},
        )
    return found


def _paths(reading: PartReading, folder: tuple[str, str]) -> tuple[TransitionSpec, ...]:
    raw = load_object(reading.source, paths_doc(folder), is_optional=True)
    if not raw:
        return ()
    return (TransitionSpec._validated(raw, context=reading.context),)


def _require_paths_for(
    folder: tuple[str, str], tid: TransitionId, specs: tuple[TransitionSpec, ...]
) -> None:
    for spec in specs:
        if spec.id != tid:
            raise SchemaError(
                f"{paths_doc(folder)} holds paths for {spec.id}",
                hint=f"set id to {list(tid)}",
                context={"tid": tid, "found": spec.id},
            )


def is_same_float(a: float, b: float) -> bool:
    """Tells whether two floats are one value, signed zeros told apart.

    >>> is_same_float(0.0, -0.0), is_same_float(2.0, 2.0)
    (False, True)
    """
    return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)


def _with_counts(
    spec: TransitionSpec, counts: float, context: Mapping[str, bool] | None
) -> TransitionSpec:
    """Gives paths the destination set's counts, validated in the decode's ID context."""
    fields = {name: getattr(spec, name) for name in type(spec)._field_names()}
    return TransitionSpec._validated(fields | {"counts": counts}, context=context)


def _counted(
    reading: PartReading, folder: tuple[str, str], spec: TransitionSpec, counts: float
) -> TransitionSpec:
    """Returns paths with the destination set's counts; paths already holding them are reused."""
    if is_same_float(spec.counts, counts):
        return spec
    rebuild = partial(_with_counts, spec, counts, reading.context)
    return reading.cached(paths_doc(folder), rebuild, variant=f"counts={counts!r}")


def read_paths(
    reading: PartReading, folder: tuple[str, str], tid: TransitionId, counts: float
) -> TransitionSpec | None:
    """Decodes the paths stored for a pair of consecutive sets, given its destination's counts.

    Args:
        reading: How the tree's documents are read.
        folder: The storage keys of the two sets, naming the transition folder.
        tid: The IDs of the two sets, which the stored paths must name.
        counts: The destination set's counts, which the paths take.

    Returns:
        spec: The stored paths with the destination's counts, or None when none are stored.
    """
    found = reading.cached(paths_doc(folder), partial(_paths, reading, folder))
    _require_paths_for(folder, tid, found)
    return _counted(reading, folder, found[0], counts) if found else None


def _validated_part[M: FrozenModel](reading: PartReading, path: str, model: type[M]) -> M:
    return model._validated(load_object(reading.source, path), context=reading.context)


def read_checked_part[M: FrozenModel](reading: PartReading, path: str, model: type[M]) -> M:
    """Decodes a show-level document once per digest; an invalid one raises SchemaError.

    Args:
        reading: How the tree's documents are read.
        path: The document's stored path.
        model: The model the document holds.

    Returns:
        part: The decoded model.
    """
    return reading.cached(path, partial(_validated_part, reading, path, model))


def read_part[M: FrozenModel](reading: PartReading, path: str, model: type[M]) -> M | JsonObject:
    """Returns a show-level document decoded once per digest, or its object when it is invalid.

    An invalid document goes into the Show as parsed, so the Show's own validation refuses it
    and names the failing fields under the Show's field, as a whole-show decode does.

    Args:
        reading: How the tree's documents are read.
        path: The document's stored path.
        model: The model the document holds.

    Returns:
        part: The decoded model, or the parsed object when the model refuses it.
    """
    try:
        return read_checked_part(reading, path, model)
    except SchemaError:
        return load_object(reading.source, path)
