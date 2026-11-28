#!/usr/bin/env python3

"""
core_json.py --- parity cases for canonical JSON, content hashes and the strict JSON reader

Inputs are built on the side under test from `e8s.seeded_rng`, the stored repository fixtures
and hand-picked texts, so both sides see the same values. Bytes and long texts are compared as
SHA-256 digests; every error is compared field by field through its `to_dict`.

Contains:
    outcome(): runs one call and records its result, digest or error
    big_show(): builds a valid show with 300 spots over 60 sets
    content_hash_on_stored_fixtures(): hashes, names and parses every stored fixture file
    canonical_bytes_on_generated_json(): writes canonical bytes, values and hashes of seeded values
    strict_json_on_hostile_text(): parses hand-picked and mutated JSON texts and copies values
    content_hash_on_a_300_by_60_show(): the timed hash of a large show
    strict_json_on_a_300_by_60_show(): the timed strict parse of a large show's canonical JSON
    JSON_CASES: the cases above
"""

import hashlib
import importlib
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from types import ModuleType

import numpy as np
from pydantic import JsonValue

from tests.parity.cases import Case, Output
from tests.parity.json_inputs import (
    HAND_PICKED,
    Count,
    Label,
    generated_value,
    mutants,
    nested,
)

SEED = 20261127
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "repos"
SKIPPED_PARTS = frozenset({"__pycache__"})
GENERATED_VALUES = 3_000
MUTANTS = 3_000
SHORT_TEXT = 400
SPOTS = 300
SETS = 60
SHOW_ID = "show_1"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _printable(value: object) -> JsonValue:
    """Escapes lone surrogates in an error record's text, which UTF-8 output cannot hold."""
    if isinstance(value, str):
        return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, dict):
        return {str(_printable(key)): _printable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_printable(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return repr(value)


def outcome(function: Callable[..., object], *args: object, **kwargs: object) -> JsonValue:
    """Runs one call and records its result, digest or error.

    Args:
        function: The function to call.
        args: Positional arguments of the call.
        kwargs: Keyword arguments of the call.

    Returns:
        record: `{"ok": text}` for a str or short bytes, `{"sha256": digest}` for long bytes
            and other values (of their `repr`), the error's `to_dict` for an e8s error, or the
            class name of a foreign exception.
    """
    try:
        result = function(*args, **kwargs)
    except Exception as error:
        to_dict = getattr(error, "to_dict", None)
        if callable(to_dict):
            return {"error": _printable(to_dict())}
        return {"raised": type(error).__name__}
    if isinstance(result, str):
        return {"ok": result}
    if isinstance(result, bytes):
        if len(result) <= SHORT_TEXT:
            return {"ok": result.decode("utf-8")}
        return {"sha256": _digest(result)}
    return {"sha256": _digest(repr(result).encode("utf-8", "backslashreplace"))}


def _odd_values(e8s: ModuleType) -> list[object]:
    readonly = importlib.import_module(f"{e8s.__name__}.core.readonly")
    examples = importlib.import_module(f"{e8s.__name__}.examples")
    cyclic: list[object] = []
    cyclic.append(cyclic)
    return [
        *(nested(depth, lambda inner: [inner]) for depth in (99, 100, 101, 102)),
        *(nested(depth, lambda inner: {1: inner}) for depth in (33, 34, 100, 101)),
        *(nested(depth, lambda inner: {"k": inner}) for depth in (100, 101)),
        cyclic,
        readonly.ReadOnlyMap({"b": 1, "a": (2, 3)}),
        {Label("k"): Label("v"), "j": Count(3)},
        {"a": 1, Label("a"): 2},
        object(),
        1j,
        b"bytes",
        bytearray(b"x"),
        np.datetime64("2026-01-01"),
        np.arange(6, dtype=np.int64).reshape(2, 3),
        {"a": np.float64("nan")},
        examples.two_set_show(),
        examples.crossing_show(),
    ]


def content_hash_on_stored_fixtures(e8s: ModuleType) -> Mapping[str, Output]:
    """Hashes, names and strictly parses every file under tests/fixtures/repos/.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Per file, its byte and text hashes, its asset URI, its parse outcome and the
            canonical bytes, value and hash of what it parsed to.
    """
    strict = importlib.import_module(f"{e8s.__name__}.core.strict_json")
    records: dict[str, JsonValue] = {}
    for path in sorted(FIXTURE_ROOT.rglob("*")):
        if not path.is_file() or SKIPPED_PARTS & set(path.parts):
            continue
        raw = path.read_bytes()
        name = path.relative_to(FIXTURE_ROOT).as_posix()
        record: dict[str, JsonValue] = {
            "bytes": outcome(e8s.content_hash, raw),
            "revision": outcome(e8s.content_hash, raw, domain="showpy.revision"),
            "asset": outcome(e8s.asset_uri, raw),
            "text": outcome(lambda data: e8s.content_hash(data.decode("utf-8")), raw),
            "parsed": outcome(strict.parse_strict_json, raw, document=name),
            "nesting": strict.json_nesting(raw),
        }
        try:
            value = strict.parse_strict_json(raw, document=name)
        except e8s.SchemaError:
            records[name] = record
            continue
        record["jcs"] = outcome(e8s.jcs, value)
        record["canonical"] = outcome(e8s.canonical_value, value)
        record["hash"] = outcome(e8s.content_hash, value)
        records[name] = record
    return {"files": records}


def canonical_bytes_on_generated_json(e8s: ModuleType) -> Mapping[str, Output]:
    """Writes the canonical bytes, value and hash of seeded values and of odd ones.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Per value, the outcome of jcs, canonical_value and content_hash.
    """
    rng = e8s.seeded_rng(SEED)
    values = [generated_value(rng) for _ in range(GENERATED_VALUES)] + _odd_values(e8s)
    records: list[JsonValue] = [
        {
            "jcs": outcome(e8s.jcs, value),
            "canonical": outcome(e8s.canonical_value, value),
            "hash": outcome(e8s.content_hash, value),
        }
        for value in values
    ]
    domains = ("showpy.content", "", "Show", "a b", "caf\u00e9", "x" * 100, "it's\n")
    records += [outcome(e8s.content_hash, "text", domain=domain) for domain in domains]
    empty: dict[str, int] = {}
    records += [outcome(e8s.content_hash, data) for data in (b"", "", "\ud800", "{}", empty)]
    return {"values": records}


def _seed_texts(e8s: ModuleType) -> list[bytes]:
    rng = e8s.seeded_rng(SEED + 2)
    seeds: list[bytes] = []
    for _ in range(400):
        value = generated_value(rng)
        try:
            seeds.append(e8s.jcs(value))
        except e8s.SchemaError:
            continue
    return [*seeds, *HAND_PICKED[:40]]


def strict_json_on_hostile_text(e8s: ModuleType) -> Mapping[str, Output]:
    """Parses hand-picked and seeded mutated JSON texts, and copies seeded Python values.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: Per text, the parse outcome and the nesting count; per value, the outcome of
            strict_json_copy.
    """
    strict = importlib.import_module(f"{e8s.__name__}.core.strict_json")
    seeds = _seed_texts(e8s)
    texts = [*HAND_PICKED, *seeds, *mutants(e8s.seeded_rng(SEED + 1), seeds, MUTANTS)]
    parsed = [outcome(strict.parse_strict_json, raw, document="doc.json") for raw in texts]
    nesting = [strict.json_nesting(raw) for raw in texts]
    rng = e8s.seeded_rng(SEED + 3)
    copies = [generated_value(rng) for _ in range(600)] + [
        {1: "x", "1": "y"},
        {True: 1, "true": 2},
        {None: 1, 2.5: 2},
        {(1, 2): 3},
        {"a": float("nan")},
        {"a": "\ud800"},
        {"a": {1, 2}},
        [nested(2_000, lambda inner: [inner])],
        [nested(101, lambda inner: [inner])],
    ]
    copied = [outcome(strict.strict_json_copy, value, document="import") for value in copies]
    return {"parsed": parsed, "nesting": nesting, "copied": copied}


@cache
def big_show(e8s: ModuleType) -> object:
    """Builds a valid show with 300 spots over 60 sets from the example show's JSON form.

    Args:
        e8s: The e8s module under test.

    Returns:
        show: The validated Show, built once per process.
    """
    examples = importlib.import_module(f"{e8s.__name__}.examples")
    doc = examples.two_set_show().model_dump(mode="json")
    rng = e8s.seeded_rng(SEED + 4)
    ids = [f"T{index}" for index in range(1, SPOTS + 1)]
    spot = doc["cast"]["spots"][0]
    placement = doc["sets"][0]["placements"][0]
    header = {key: value for key, value in doc["sets"][0].items() if key != "placements"}
    xs = rng.uniform(-18.0, 18.0, size=(SETS, SPOTS))
    ys = rng.uniform(1.0, 18.0, size=(SETS, SPOTS))
    facings = rng.uniform(0.0, 360.0, size=(SETS, SPOTS))
    doc["id"] = SHOW_ID
    doc["cast"]["spots"] = [{**spot, "id": spot_id} for spot_id in ids]
    doc["transitions"] = []
    doc["sets"] = [
        {
            **header,
            "id": str(index + 1),
            "counts": 0.0 if index == 0 else 8.0,
            "placements": [
                {
                    **placement,
                    "entity": spot_id,
                    "x": float(xs[index, column]),
                    "y": float(ys[index, column]),
                    "facing": float(facings[index, column]),
                }
                for column, spot_id in enumerate(ids)
            ],
        }
        for index in range(SETS)
    ]
    return e8s.Show.model_validate(doc)


def content_hash_on_a_300_by_60_show(e8s: ModuleType) -> Mapping[str, Output]:
    """Hashes a show with 300 spots over 60 sets; the timed share of content_hash.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The show's content hash.
    """
    return {"hash": e8s.content_hash(big_show(e8s))}


@cache
def _big_show_text(e8s: ModuleType) -> bytes:
    return bytes(e8s.jcs(big_show(e8s)))


def strict_json_on_a_300_by_60_show(e8s: ModuleType) -> Mapping[str, Output]:
    """Parses the canonical JSON of a show with 300 spots over 60 sets; the timed share of
    parse_strict_json.

    Args:
        e8s: The e8s module under test.

    Returns:
        outputs: The number of sets and the sum of every placement's x; the untimed cases
            compare whole parsed values.
    """
    strict = importlib.import_module(f"{e8s.__name__}.core.strict_json")
    value = strict.parse_strict_json(_big_show_text(e8s), document="show.json")
    sets = value["sets"]
    total = sum(placement["x"] for item in sets for placement in item["placements"])
    return {"sets": len(sets), "val:x": np.array([total])}


JSON_CASES = (
    Case(area="core", name="content_hash_on_stored_fixtures", run=content_hash_on_stored_fixtures),
    Case(
        area="core",
        name="canonical_bytes_on_generated_json",
        run=canonical_bytes_on_generated_json,
    ),
    Case(area="core", name="strict_json_on_hostile_text", run=strict_json_on_hostile_text),
    Case(
        area="core",
        name="content_hash_on_a_300_by_60_show",
        run=content_hash_on_a_300_by_60_show,
        timed=True,
    ),
    Case(
        area="core",
        name="strict_json_on_a_300_by_60_show",
        run=strict_json_on_a_300_by_60_show,
        timed=True,
    ),
)
