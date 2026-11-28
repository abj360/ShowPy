#!/usr/bin/env python3

"""
compare.py --- gates the working tree's case outputs against the v0.4.0 oracle's

A key's prefix names its gate: `pos:` floats in metres at most 1e-9 apart, `deg:` floats in
degrees at most 1e-9 apart, `val:` other floats at most 4 ulp (or 1e-12) apart. Every other
key must be identical, byte for byte.

Contains:
    ulps(): returns the exact distance in units in the last place between two float arrays
    ParityReport: what one case's comparison found, and whether it passed
    compare(): compares one case's oracle outputs with its core outputs
    SpeedReport: the timed cases' minimum run times and the cases where the core is slower
    SPEED_NOISE_RATIO: the core/oracle time ratio past which a timed case is slower
    compare_speed(): finds the timed cases slower in the core by more than SPEED_NOISE_RATIO
    Flip: a case fixed past v0.4.0, compared against its ledger expectation instead
    load_flipped(): reads flipped.toml, refusing an entry that names no loaded case
    Intended: a case whose output differs from v0.4.0 by decision, compared against its expectation
    load_intended(): reads intended.toml, refusing unknown, repeated or already flipped cases
    compare_case(): compares a case against the oracle, or a flipped case against its fix
    first_difference(): names the first record where two per-value output lists differ
    digest_lines(): lists the SHA-256 of every core output
    format_table(): renders the per-case reports as a text table
    format_speed(): renders the speed report as a text table
"""

import hashlib
import math
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from tests.ledger_plugin import DEFECT_ID_PATTERN
from tests.parity.cases import Case, Output, encode_record

POSITION_PREFIX = "pos:"
ANGLE_PREFIX = "deg:"
VALUE_PREFIX = "val:"
GATED_PREFIXES = (POSITION_PREFIX, ANGLE_PREFIX, VALUE_PREFIX)
POSITION_TOL_M = 1e-9
ANGLE_TOL_DEG = 1e-9
VALUE_MAX_ULP = 4
VALUE_ABS_TOL = 1e-12
SIGN_BIT = np.uint64(1 << 63)
# "Not measurably slower" (2026-11-24, controller ruling): the same Python code timed on both
# sides measured core/oracle ratios of 1.037, 1.008, 1.009, 1.008 and 1.017 from single launches
# and 0.990, 0.982 and 1.007 as the best of 5 alternating launches, so a port fails only past
# this ratio.
SPEED_NOISE_RATIO = 1.05
FLIP_KEYS = frozenset({"name", "ledger", "expect"})
INTENDED_KEYS = frozenset({"name", "reason", "expect"})
INTENDED_RESULT = "intended"


def _ordered(values: npt.NDArray[np.float64]) -> npt.NDArray[np.uint64]:
    bits = np.ascontiguousarray(values, dtype=np.float64).view(np.uint64)
    return np.where(bits & SIGN_BIT, ~bits, bits | SIGN_BIT)


def ulps(a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> npt.NDArray[np.uint64]:
    """Returns |a - b| in units in the last place, exactly, over the whole float64 range.

    Bit patterns are mapped to unsigned integers in float order, so no subtraction overflows;
    -0.0 and 0.0 are one ulp apart.

    Args:
        a: One float array.
        b: Another float array of the same shape.

    Returns:
        distances: The number of float64 values between each pair, as uint64.
    """
    ordered_a, ordered_b = _ordered(a), _ordered(b)
    return np.where(ordered_a >= ordered_b, ordered_a - ordered_b, ordered_b - ordered_a)


@dataclass(frozen=True)
class ParityReport:
    """Holds what comparing one case's outputs found.

    Attributes:
        values: The float values compared under `pos:`, `deg:` and `val:` keys.
        bit_identical: How many of them have the same bit pattern on both sides.
        max_abs_m: The largest difference under `pos:` keys, in metres.
        max_abs_deg: The largest difference under `deg:` keys, in degrees.
        max_ulp: The largest ulp distance of any compared float.
        ulp_histogram: Ulp distance to the number of values that far apart, identical ones out.
        discrete_mismatches: Keys that differ in presence, type, shape or (ungated) content.
        past_gate: Gated keys holding at least one value past the key's gate.
    """

    values: int
    bit_identical: int
    max_abs_m: float
    max_abs_deg: float
    max_ulp: int
    ulp_histogram: Mapping[int, int]
    discrete_mismatches: tuple[str, ...]
    past_gate: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Tells whether every key is identical or within its gate."""
        return not self.discrete_mismatches and not self.past_gate


def _is_float_array(value: Output) -> bool:
    return isinstance(value, np.ndarray) and value.dtype == np.float64


def _within_gate(
    key: str, absolute: npt.NDArray[np.float64], distances: npt.NDArray[np.uint64]
) -> bool:
    finite = np.isfinite(absolute)
    if key.startswith(VALUE_PREFIX):
        close = (distances <= VALUE_MAX_ULP) | (absolute <= VALUE_ABS_TOL)
    else:
        tolerance = POSITION_TOL_M if key.startswith(POSITION_PREFIX) else ANGLE_TOL_DEG
        close = absolute <= tolerance
    return bool(np.all(finite & close))


@dataclass
class _Tally:
    values: int = 0
    bit_identical: int = 0
    max_abs_m: float = 0.0
    max_abs_deg: float = 0.0
    histogram: Counter[int] | None = None

    def add_floats(
        self, key: str, oracle: npt.NDArray[np.float64], core: npt.NDArray[np.float64]
    ) -> bool:
        """Counts one gated key's values; tells whether all of them are within its gate."""
        a, b = oracle.ravel(), core.ravel()
        same = a.view(np.uint64) == b.view(np.uint64)
        distances = np.where(same, np.uint64(0), ulps(a, b))
        with np.errstate(invalid="ignore", over="ignore"):
            absolute = np.where(same, 0.0, np.abs(a - b))
        self.values += a.size
        self.bit_identical += int(same.sum())
        self.histogram = (self.histogram or Counter()) + Counter(distances[~same].tolist())
        largest = float(np.max(np.nan_to_num(absolute, nan=math.inf), initial=0.0))
        if key.startswith(POSITION_PREFIX):
            self.max_abs_m = max(self.max_abs_m, largest)
        elif key.startswith(ANGLE_PREFIX):
            self.max_abs_deg = max(self.max_abs_deg, largest)
        return _within_gate(key, absolute, distances)


def _is_discrete_match(key: str, oracle: Output, core: Output) -> bool:
    if key.startswith(GATED_PREFIXES):
        return _is_float_array(oracle) and _is_float_array(core) and _shape(oracle) == _shape(core)
    return encode_record(key, oracle) == encode_record(key, core)


def _shape(value: Output) -> tuple[int, ...]:
    return value.shape if isinstance(value, np.ndarray) else ()


def compare(oracle: Mapping[str, Output], core: Mapping[str, Output]) -> ParityReport:
    """Compares one case's outputs key by key under the gate each key's prefix names.

    Args:
        oracle: The outputs the reference produced (the v0.4.0 oracle or a flip's expectation).
        core: The outputs the working tree produced.

    Returns:
        report: Counts, largest differences, the ulp histogram and every failing key.
    """
    tally = _Tally()
    mismatches: list[str] = []
    past_gate: list[str] = []
    for key in sorted(set(oracle) | set(core)):
        if (
            key not in oracle
            or key not in core
            or not _is_discrete_match(key, oracle[key], core[key])
        ):
            mismatches.append(key)
        elif key.startswith(GATED_PREFIXES) and not tally.add_floats(
            key, np.asarray(oracle[key]), np.asarray(core[key])
        ):
            past_gate.append(key)
    histogram = dict(sorted((tally.histogram or Counter()).items()))
    return ParityReport(
        values=tally.values,
        bit_identical=tally.bit_identical,
        max_abs_m=tally.max_abs_m,
        max_abs_deg=tally.max_abs_deg,
        max_ulp=max(histogram, default=0),
        ulp_histogram=histogram,
        discrete_mismatches=tuple(mismatches),
        past_gate=tuple(past_gate),
    )


@dataclass(frozen=True)
class SpeedReport:
    """Holds the timed cases' minimum run times on both sides.

    Attributes:
        rows: Case name, oracle minimum and core minimum in seconds (NaN when missing).
        slower: Cases whose core minimum exceeds SPEED_NOISE_RATIO times the oracle minimum,
            or that lack a timing on one side.
    """

    rows: tuple[tuple[str, float, float], ...]
    slower: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """Tells whether no timed case is slower in the core."""
        return not self.slower


def compare_speed(oracle: Mapping[str, float], core: Mapping[str, float]) -> SpeedReport:
    """Finds the timed cases whose core minimum exceeds the oracle's by more than the noise.

    Args:
        oracle: Qualified case name to the oracle's minimum of 7 runs, in seconds.
        core: Qualified case name to the core's minimum of 7 runs, in seconds.

    Returns:
        report: One row per case either side timed. A case is slower when its core minimum
            exceeds SPEED_NOISE_RATIO times its oracle minimum, or when one side lacks it.
    """
    rows = tuple(
        (name, oracle.get(name, math.nan), core.get(name, math.nan))
        for name in sorted(set(oracle) | set(core))
    )
    slower = tuple(name for name, before, after in rows if not after <= before * SPEED_NOISE_RATIO)
    return SpeedReport(rows=rows, slower=slower)


@dataclass(frozen=True)
class Flip:
    """Names a case whose v0.4.0 output was a defect the working tree fixed.

    Attributes:
        name: The qualified case name, `<area>.<name>`.
        ledger: The ledger ID whose test asserts the fixed behaviour.
        expect: The fixed outputs; gated keys hold float lists, other keys JSON values.
    """

    name: str
    ledger: str
    expect: Mapping[str, Output]


def _flip(entry: object, known: set[str]) -> Flip:
    if not isinstance(entry, dict) or set(entry) != FLIP_KEYS:
        raise SystemExit(f"E: flipped.toml entry {entry!r} needs exactly {sorted(FLIP_KEYS)}")
    name, ledger, expect = entry["name"], entry["ledger"], entry["expect"]
    if name not in known:
        raise SystemExit(f"E: flipped.toml names case {name!r}, which no cases_<area>.py defines")
    if not isinstance(ledger, str) or not DEFECT_ID_PATTERN.match(ledger):
        raise SystemExit(f"E: flipped.toml case {name!r} has ledger {ledger!r}; use UP-<s>-<nn>")
    if not isinstance(expect, dict):
        raise SystemExit(f"E: flipped.toml case {name!r} needs an expect table")
    return Flip(name=name, ledger=ledger, expect=expect)


def load_flipped(path: Path, cases: Sequence[Case]) -> dict[str, Flip]:
    """Reads the flipped cases, refusing any entry that names no loaded case.

    Args:
        path: The flipped.toml file.
        cases: Every loaded case; an entry's name must be one's qualified name.

    Returns:
        flips: Qualified case name to its flip.
    """
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(document) - {"case"}:
        raise SystemExit(f"E: {path} holds keys other than [[case]]: {sorted(document)}")
    known = {case.qualified_name for case in cases}
    flips = [_flip(entry, known) for entry in document.get("case", [])]
    by_name = {flip.name: flip for flip in flips}
    if len(by_name) != len(flips):
        raise SystemExit(f"E: {path} lists a case more than once")
    return by_name


@dataclass(frozen=True)
class Intended:
    """Names a case whose output differs from v0.4.0 by the maintainer's decision.

    Attributes:
        name: The qualified case name, `<area>.<name>`.
        reason: One line saying why the difference is intended.
        expect: The intended outputs; gated keys hold float lists, other keys JSON values.
    """

    name: str
    reason: str
    expect: Mapping[str, Output]


def _intended(entry: object, known: set[str]) -> Intended:
    if not isinstance(entry, dict) or set(entry) != INTENDED_KEYS:
        raise SystemExit(f"E: intended.toml entry {entry!r} needs exactly {sorted(INTENDED_KEYS)}")
    name, reason, expect = entry["name"], entry["reason"], entry["expect"]
    if name not in known:
        raise SystemExit(f"E: intended.toml names case {name!r}, which no cases_<area>.py defines")
    if not isinstance(reason, str) or not reason.strip():
        raise SystemExit(f"E: intended.toml case {name!r} needs a one-line reason")
    if not isinstance(expect, dict):
        raise SystemExit(f"E: intended.toml case {name!r} needs an expect table")
    return Intended(name=name, reason=reason, expect=expect)


def load_intended(
    path: Path, cases: Sequence[Case], flips: Mapping[str, Flip]
) -> dict[str, Intended]:
    """Reads the intended cases, refusing unknown, repeated and already flipped ones.

    Args:
        path: The intended.toml file.
        cases: Every loaded case; an entry's name must be one's qualified name.
        flips: The flipped cases; a case is flipped or intended, never both.

    Returns:
        intended: Qualified case name to its entry.
    """
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(document) - {"case"}:
        raise SystemExit(f"E: {path} holds keys other than [[case]]: {sorted(document)}")
    known = {case.qualified_name for case in cases}
    entries = [_intended(entry, known) for entry in document.get("case", [])]
    by_name = {entry.name: entry for entry in entries}
    if len(by_name) != len(entries):
        raise SystemExit(f"E: {path} lists a case more than once")
    both = sorted(set(by_name) & set(flips))
    if both:
        raise SystemExit(f"E: {both} are in both flipped.toml and intended.toml")
    return by_name


def _expectation(entry: Flip | Intended) -> dict[str, Output]:
    return {
        key: np.asarray(value, dtype=np.float64) if key.startswith(GATED_PREFIXES) else value
        for key, value in entry.expect.items()
    }


def compare_case(
    name: str,
    oracle: Mapping[str, Output],
    core: Mapping[str, Output],
    flips: Mapping[str, Flip],
    intended: Mapping[str, Intended] | None = None,
) -> ParityReport:
    """Compares a case against the oracle, or a flipped or intended case against its expectation.

    Args:
        name: The qualified case name.
        oracle: The oracle's outputs, ignored for a flipped or intended case.
        core: The working tree's outputs.
        flips: The flipped cases by qualified name.
        intended: The intended cases by qualified name.

    Returns:
        report: The comparison against the reference that applies to the case.
    """
    entry = flips.get(name) or (intended or {}).get(name)
    return compare(oracle if entry is None else _expectation(entry), core)


def first_difference(name: str, oracle: Sequence[object], core: Sequence[object]) -> str | None:
    """Names the first record where two per-value output lists differ.

    Args:
        name: The `<case>/<key>` the lists come from, used in the result.
        oracle: The oracle's records, one per input value.
        core: The core's records for the same inputs.

    Returns:
        difference: `name[index]` with both records, a length mismatch, or None when equal.
    """
    if len(oracle) != len(core):
        return f"{name}: oracle has {len(oracle)} records, core {len(core)}"
    for index, (expected, actual) in enumerate(zip(oracle, core, strict=True)):
        if expected != actual:
            return f"{name}[{index}]: oracle {expected!r}, core {actual!r}"
    return None


def digest_lines(name: str, outputs: Mapping[str, Output]) -> list[str]:
    """Lists `<sha256>  <case>/<key>` for every output of one case, in key order."""
    return [
        f"{hashlib.sha256(encode_record(key, outputs[key])).hexdigest()}  {name}/{key}"
        for key in sorted(outputs)
    ]


def format_table(
    reports: Mapping[str, ParityReport], intended: Mapping[str, Intended] | None = None
) -> str:
    """Renders one row per case, then each case's ulp histogram when it has one.

    An intended case that passes shows `intended` and its reason, so it is never skipped.
    """
    header = (
        f"{'case':<44} {'values':>8} {'identical':>9} {'max|d| m':>9} {'max|d| deg':>10} "
        f"{'max ulp':>7} {'result':>6}"
    )
    lines = [header]
    by_name = intended or {}
    for name, report in reports.items():
        passed = INTENDED_RESULT if name in by_name else "ok"
        result = passed if report.passed else "FAIL"
        lines.append(
            f"{name:<44} {report.values:>8} {report.bit_identical:>9} {report.max_abs_m:>9.2e} "
            f"{report.max_abs_deg:>10.2e} {report.max_ulp:>7} {result:>6}"
        )
        if name in by_name:
            lines.append(f"  intended: {by_name[name].reason}")
        if report.ulp_histogram:
            lines.append(f"  ulp histogram: {dict(report.ulp_histogram)}")
        lines.extend(
            f"  E: {name} {key} differs" for key in (*report.discrete_mismatches, *report.past_gate)
        )
    return "\n".join(lines)


def format_speed(report: SpeedReport) -> str:
    """Renders one row per timed case: both minima, their ratio and the result against the limit."""
    limit = f"<= {SPEED_NOISE_RATIO:.2f}"
    lines = [f"{'case':<44} {'oracle min s':>12} {'core min s':>12} {'ratio':>6} {limit:>8}"]
    for name, before, after in report.rows:
        result = "FAIL" if name in report.slower else "ok"
        lines.append(
            f"{name:<44} {before:>12.6f} {after:>12.6f} {after / before:>6.3f} {result:>8}"
        )
    return "\n".join(lines)
