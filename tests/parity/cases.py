#!/usr/bin/env python3

"""
cases.py --- the parity case record, its loader and the byte encoding of its outputs

Both sides import this module: the oracle interpreter (v0.4.0 with its locked numpy and
pydantic) and the working tree. It imports nothing from e8s, so each side's e8s is the one the
runner passes to a case.

Contains:
    Output: one case output, a float64 or int64 array or a JSON value
    Case: one named computation of an area, run against a given e8s module
    Case.qualified_name(): returns `<area>.<name>`, the name flipped.toml and timings use
    load_cases(): imports tests/parity/cases_<area>.py and returns its checked CASES
    load_all_cases(): loads the cases of every cases_<area>.py
    encode_record(): encodes one output as a JSON header line and its raw bytes
    encode_outputs(): encodes a case's outputs, in their order, as one byte string
    decode_outputs(): decodes what encode_outputs wrote, refusing a malformed or truncated record
    write_outputs(): writes a case's encoded outputs to <folder>/<case name>.parity
    read_outputs(): reads every case file of a folder back into outputs
"""

import importlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import numpy as np
import numpy.typing as npt
from pydantic import JsonValue

type Output = npt.NDArray[np.float64] | npt.NDArray[np.int64] | JsonValue

CASES_FOLDER = Path(__file__).resolve().parent
CASE_MODULE_PREFIX = "cases_"
CASES_ATTRIBUTE = "CASES"
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
CASE_FILE_SUFFIX = ".parity"
FLOAT_DTYPE = "<f8"
INT_DTYPE = "<i8"
JSON_DTYPE = "json"
ARRAY_DTYPES = {("f", 8): FLOAT_DTYPE, ("i", 8): INT_DTYPE}
HEADER_END = b"\n"
ITEM_BYTES = 8
JSON_HEADER_KEYS = frozenset({"dtype", "key", "nbytes"})
ARRAY_HEADER_KEYS = JSON_HEADER_KEYS | {"shape"}


@dataclass(frozen=True)
class Case:
    """Names one computation whose outputs the oracle and the working tree must agree on.

    Attributes:
        area: The area whose case module defines the case, such as "time".
        name: The case's name, unique in its area.
        run: Computes the outputs from the e8s module it is given; keys carry their gate.
        timed: Whether `--speed` compares the minimum run time of both sides.
    """

    area: str
    name: str
    run: Callable[[ModuleType], Mapping[str, Output]]
    timed: bool = False

    @property
    def qualified_name(self) -> str:
        """Returns `<area>.<name>`."""
        return f"{self.area}.{self.name}"


def _checked_name(kind: str, name: str) -> str:
    if not NAME_PATTERN.match(name):
        raise SystemExit(f"E: parity {kind} {name!r} must match {NAME_PATTERN.pattern}")
    return name


def load_cases(area: str) -> tuple[Case, ...]:
    """Imports tests/parity/cases_<area>.py and returns its CASES after checking them.

    Args:
        area: The area to load, such as "time".

    Returns:
        cases: The module's cases, in their order; each names this area and a unique name.
    """
    module_name = f"{CASE_MODULE_PREFIX}{_checked_name('area', area)}"
    if not (CASES_FOLDER / f"{module_name}.py").is_file():
        raise SystemExit(f"E: no tests/parity/{module_name}.py")
    module = importlib.import_module(f"{__package__}.{module_name}")
    cases = getattr(module, CASES_ATTRIBUTE, None)
    if not isinstance(cases, tuple) or not all(isinstance(case, Case) for case in cases):
        raise SystemExit(f"E: tests/parity/{module_name}.py must define CASES: tuple[Case, ...]")
    names = [_checked_name("case", case.name) for case in cases]
    if len(set(names)) != len(names) or any(case.area != area for case in cases):
        raise SystemExit(f"E: {module_name} cases must have unique names and area {area!r}")
    return cases


def load_all_cases() -> tuple[Case, ...]:
    """Loads the cases of every tests/parity/cases_<area>.py, area by area in name order."""
    modules = sorted(CASES_FOLDER.glob(f"{CASE_MODULE_PREFIX}*.py"))
    areas = [module.stem.removeprefix(CASE_MODULE_PREFIX) for module in modules]
    return tuple(case for area in areas for case in load_cases(area))


def _array_dtype(key: str, value: np.ndarray) -> str:
    dtype = ARRAY_DTYPES.get((value.dtype.kind, value.dtype.itemsize))
    if dtype is None:
        raise SystemExit(f"E: output {key!r} has dtype {value.dtype}; return float64 or int64")
    return dtype


def _canonical_json(key: str, value: JsonValue) -> bytes:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as error:
        raise SystemExit(f"E: output {key!r} is not finite JSON: {error}") from error
    return text.encode("utf-8")


def encode_record(key: str, value: Output) -> bytes:
    """Encodes one output as a canonical JSON header line followed by its payload.

    Args:
        key: The output's key, prefixed with its gate.
        value: A float64 or int64 array (raw little-endian bytes) or a JSON value.

    Returns:
        record: The header line, which holds the key, dtype, shape and payload size, then the
            payload; the same output gives the same bytes on every platform.
    """
    if isinstance(value, np.ndarray):
        dtype = _array_dtype(key, value)
        payload = np.ascontiguousarray(value, dtype=dtype).tobytes()
        header: dict[str, JsonValue] = {"dtype": dtype, "shape": list(value.shape)}
    else:
        payload = _canonical_json(key, value)
        header = {"dtype": JSON_DTYPE}
    header |= {"key": key, "nbytes": len(payload)}
    return _canonical_json(key, header) + HEADER_END + payload


def encode_outputs(outputs: Mapping[str, Output]) -> bytes:
    """Encodes a case's outputs, in their order, as one byte string."""
    return b"".join(encode_record(key, value) for key, value in outputs.items())


def _json_payload(key: str, payload: bytes) -> JsonValue:
    try:
        decoded: JsonValue = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"E: parity record {key!r} holds no JSON: {error}") from error
    return decoded


def _array_payload(key: str, dtype: str, shape: JsonValue, payload: bytes) -> Output:
    sizes = shape if isinstance(shape, list) else [None]
    if not all(type(size) is int and size >= 0 for size in sizes):
        raise SystemExit(f"E: parity record {key!r} has shape {shape!r}")
    counts = [int(str(size)) for size in sizes]
    if math.prod(counts) * ITEM_BYTES != len(payload):
        raise SystemExit(f"E: parity record {key!r} holds {len(payload)} bytes, not {counts}")
    return np.frombuffer(payload, dtype=dtype).copy().reshape(counts)


def _header(encoded: bytes, at: int) -> tuple[dict[str, JsonValue], int]:
    end = encoded.find(HEADER_END, at)
    if end < 0:
        raise SystemExit(f"E: parity record at byte {at} is truncated before its header ends")
    try:
        header = json.loads(encoded[at:end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"E: parity record header at byte {at} is not JSON: {error}") from error
    expected = JSON_HEADER_KEYS if _get(header, "dtype") == JSON_DTYPE else ARRAY_HEADER_KEYS
    if not isinstance(header, dict) or set(header) != expected:
        raise SystemExit(f"E: parity record header at byte {at} is {header!r}")
    nbytes, key = header["nbytes"], header["key"]
    start = end + len(HEADER_END)
    if (
        not isinstance(key, str)
        or type(nbytes) is not int
        or not 0 <= nbytes <= len(encoded) - start
    ):
        raise SystemExit(f"E: parity record header at byte {at} is {header!r} (truncated?)")
    return header, start


def _get(header: object, name: str) -> object:
    return header.get(name) if isinstance(header, dict) else None


def decode_outputs(encoded: bytes) -> dict[str, Output]:
    """Decodes the outputs encode_outputs wrote, in their order, refusing a malformed record.

    Args:
        encoded: The bytes of one case's records.

    Returns:
        outputs: Key to output; arrays come back as native float64 or int64 arrays. A header
            that is not a JSON object with exactly the expected fields, a size past the end of
            the data, or a payload that does not fill its shape exits with an E: line.
    """
    outputs: dict[str, Output] = {}
    at = 0
    while at < len(encoded):
        header, start = _header(encoded, at)
        key, dtype = str(header["key"]), header["dtype"]
        at = start + int(str(header["nbytes"]))
        payload = encoded[start:at]
        if dtype == JSON_DTYPE:
            outputs[key] = _json_payload(key, payload)
        elif dtype in (FLOAT_DTYPE, INT_DTYPE):
            outputs[key] = _array_payload(key, str(dtype), header["shape"], payload)
        else:
            raise SystemExit(f"E: parity record {key!r} has dtype {dtype!r}")
    return outputs


def write_outputs(folder: Path, name: str, outputs: Mapping[str, Output]) -> None:
    """Writes a case's encoded outputs to <folder>/<name>.parity."""
    (folder / f"{name}{CASE_FILE_SUFFIX}").write_bytes(encode_outputs(outputs))


def read_outputs(folder: Path) -> dict[str, dict[str, Output]]:
    """Reads every <case>.parity file of a folder.

    Args:
        folder: A folder oracle_runner.py wrote.

    Returns:
        outputs: Case name to its outputs, in case-name order.
    """
    return {
        path.stem: decode_outputs(path.read_bytes())
        for path in sorted(folder.glob(f"*{CASE_FILE_SUFFIX}"))
    }
