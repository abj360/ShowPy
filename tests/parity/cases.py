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
    decode_outputs(): decodes what encode_outputs wrote
    write_outputs(): writes a case's encoded outputs to <folder>/<case name>.parity
    read_outputs(): reads every case file of a folder back into outputs
"""

import importlib
import json
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


def _decode_payload(header: Mapping[str, JsonValue], payload: bytes) -> Output:
    dtype = header["dtype"]
    if dtype == JSON_DTYPE:
        decoded: JsonValue = json.loads(payload.decode("utf-8"))
        return decoded
    shape = header["shape"]
    if dtype not in (FLOAT_DTYPE, INT_DTYPE) or not isinstance(shape, list):
        raise SystemExit(f"E: unknown parity record header {header!r}")
    array = np.frombuffer(payload, dtype=str(dtype)).copy()
    return array.reshape([int(str(size)) for size in shape])


def decode_outputs(encoded: bytes) -> dict[str, Output]:
    """Decodes the outputs encode_outputs wrote, in their order.

    Args:
        encoded: The bytes of one case's records.

    Returns:
        outputs: Key to output; arrays come back as native float64 or int64 arrays.
    """
    outputs: dict[str, Output] = {}
    at = 0
    while at < len(encoded):
        end = encoded.index(HEADER_END, at)
        header = json.loads(encoded[at:end].decode("utf-8"))
        start = end + len(HEADER_END)
        at = start + int(header["nbytes"])
        outputs[str(header["key"])] = _decode_payload(header, encoded[start:at])
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
