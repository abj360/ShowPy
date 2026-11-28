#!/usr/bin/env python3

"""
test_parity_compare.py --- checks the parity harness's gates, speed check, flips and encoding

Contains:
    test_compare_counts_bit_identical_values_and_ulps: counts identical values and ulp distances
    test_ulps_are_exact_across_zero_and_the_whole_range: no int64 overflow, -0.0 is one ulp away
    test_compare_flags_a_position_past_the_gate: 1.1e-9 m fails `pos:`, 0.9e-9 m passes
    test_compare_flags_an_angle_past_the_gate: 1.1e-9 degrees fails `deg:`
    test_compare_holds_values_to_four_ulp_or_an_absolute_floor: 5 ulp fails `val:` unless tiny
    test_compare_flags_a_non_finite_change: NaN against a number fails every gate
    test_compare_flags_a_discrete_mismatch: orders, ints, JSON types and keys must be identical
    test_compare_flags_a_gated_key_that_is_not_a_float_array: a `pos:` list is a mismatch
    test_compare_flags_a_slower_case: a core minimum over 1.05x the oracle's fails `--speed`
    test_speed_margin_is_the_measured_noise_ratio: 1.05 passes, a hair above fails
    test_regression_speed_fails_a_missing_timing: a case timed on one side only fails
    test_flipped_case_is_compared_against_its_ledger_expectation: the oracle is ignored
    test_load_flipped_rejects_an_unknown_case: an entry must name a loaded case
    test_load_flipped_rejects_a_malformed_ledger_id: an entry must carry a UP- ledger ID
    test_repository_flipped_file_names_only_loaded_cases: the committed flipped.toml loads
    test_intended_case_is_compared_against_its_expectation: the oracle is ignored
    test_load_intended_rejects_an_unknown_case: an entry must name a loaded case
    test_load_intended_rejects_a_repeated_case: a case is listed once
    test_load_intended_rejects_a_flipped_case: a case is flipped or intended, never both
    test_load_intended_rejects_a_missing_reason: an entry needs a one-line reason
    test_intended_cases_are_printed_in_the_table: a passing intended case shows its reason
    test_repository_intended_file_names_only_loaded_cases: the committed intended.toml loads
    test_outputs_round_trip_through_their_encoding: arrays and JSON decode to equal values
    test_encoding_rejects_an_unsupported_dtype: a float32 array fails closed
    test_regression_decode_refuses_a_malformed_or_truncated_record: bounds every header field
    test_load_cases_reads_the_time_self_check: cases_time.py holds the clock self-check
    test_load_cases_rejects_an_unknown_area: an area without a case module fails closed
    test_first_difference_names_the_first_differing_record: a changed float text is found at
        its index, with both records
    test_first_difference_flags_a_missing_record: lists of different lengths differ
"""

import math
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from tests.parity.cases import (
    Case,
    Output,
    decode_outputs,
    encode_outputs,
    load_all_cases,
    load_cases,
)
from tests.parity.compare import (
    SPEED_NOISE_RATIO,
    Flip,
    Intended,
    compare,
    compare_case,
    compare_speed,
    first_difference,
    format_table,
    load_flipped,
    load_intended,
    ulps,
)

FLIPPED_FILE = Path(__file__).resolve().parents[2] / "parity" / "flipped.toml"
INTENDED_FILE = Path(__file__).resolve().parents[2] / "parity" / "intended.toml"
INTENDED_ENTRY = '[[case]]\nname = "geo.scale_check"\nreason = "x"\nexpect = {}\n'
TINY = 5e-324
CASE = "geo.scale_check"


def _floats(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


def _ulp_steps(value: float, steps: int) -> float:
    for _ in range(steps):
        value = float(np.nextafter(value, math.inf))
    return value


def _case(area: str, name: str) -> Case:
    return Case(area=area, name=name, run=lambda _e8s: {})


def test_compare_counts_bit_identical_values_and_ulps() -> None:
    oracle: dict[str, Output] = {"val:x": _floats(1.0, 2.0, 3.0), "pos:p": _floats(0.5)}
    core: dict[str, Output] = {
        "val:x": _floats(1.0, _ulp_steps(2.0, 1), _ulp_steps(3.0, 2)),
        "pos:p": _floats(0.5),
    }
    report = compare(oracle, core)
    assert report.values == 4
    assert report.bit_identical == 2
    assert dict(report.ulp_histogram) == {1: 1, 2: 1}
    assert report.max_ulp == 2
    assert report.max_abs_m == 0.0
    assert report.passed


def test_ulps_are_exact_across_zero_and_the_whole_range() -> None:
    biggest = float(np.finfo(np.float64).max)
    assert ulps(_floats(-0.0, -TINY, -biggest), _floats(0.0, TINY, biggest)).tolist() == [
        1,
        3,
        0xFFDFFFFFFFFFFFFF,
    ]
    assert ulps(_floats(1.0), _floats(_ulp_steps(1.0, 7))).tolist() == [7]


@pytest.mark.parametrize(("shift", "passed"), [(1.1e-9, False), (0.9e-9, True)])
def test_compare_flags_a_position_past_the_gate(shift: float, *, passed: bool) -> None:
    report = compare({"pos:p": _floats(0.0, 1.0)}, {"pos:p": _floats(shift, 1.0)})
    assert report.max_abs_m == shift
    assert report.passed is passed
    assert report.past_gate == (() if passed else ("pos:p",))


def test_compare_flags_an_angle_past_the_gate() -> None:
    report = compare({"deg:a": _floats(90.0)}, {"deg:a": _floats(90.0 + 1.1e-9)})
    assert report.max_abs_deg > 1e-9
    assert not report.passed


def test_compare_holds_values_to_four_ulp_or_an_absolute_floor() -> None:
    four = compare({"val:v": _floats(1e6)}, {"val:v": _floats(_ulp_steps(1e6, 4))})
    five = compare({"val:v": _floats(1e6)}, {"val:v": _floats(_ulp_steps(1e6, 5))})
    tiny = compare({"val:v": _floats(1e-300)}, {"val:v": _floats(_ulp_steps(1e-300, 99))})
    assert four.passed
    assert not five.passed
    assert five.past_gate == ("val:v",)
    assert tiny.passed


def test_compare_flags_a_non_finite_change() -> None:
    for key in ("pos:p", "deg:a", "val:v"):
        assert not compare({key: _floats(1.0)}, {key: _floats(math.nan)}).passed
        assert compare({key: _floats(math.inf)}, {key: _floats(math.inf)}).passed


def test_compare_flags_a_discrete_mismatch() -> None:
    oracle: dict[str, Output] = {
        "order": ["a", "b"],
        "count": np.array([1, 2], dtype=np.int64),
        "code": 1,
        "hash": _floats(0.1),
    }
    core: dict[str, Output] = {
        "order": ["b", "a"],
        "count": np.array([1, 3], dtype=np.int64),
        "code": 1.0,
        "hash": _floats(_ulp_steps(0.1, 1)),
        "extra": None,
    }
    report = compare(oracle, core)
    assert report.discrete_mismatches == ("code", "count", "extra", "hash", "order")
    assert not report.passed
    assert compare(oracle, dict(oracle)).passed


def test_compare_flags_a_gated_key_that_is_not_a_float_array() -> None:
    report = compare({"pos:p": [0.0]}, {"pos:p": [0.0]})
    assert report.discrete_mismatches == ("pos:p",)
    shapes = compare({"pos:p": _floats(0.0, 1.0)}, {"pos:p": _floats(0.0, 1.0).reshape(1, 2)})
    assert shapes.discrete_mismatches == ("pos:p",)


@pytest.mark.parametrize(
    ("ratio", "passed"), [(0.5, True), (1.0, True), (1.04, True), (1.06, False)]
)
def test_compare_flags_a_slower_case(ratio: float, *, passed: bool) -> None:
    report = compare_speed({"time.c": 1.0e-3}, {"time.c": ratio * 1.0e-3})
    assert report.passed is passed
    assert report.slower == (() if passed else ("time.c",))


def test_speed_margin_is_the_measured_noise_ratio() -> None:
    assert SPEED_NOISE_RATIO == 1.05
    at_margin = compare_speed({"time.c": 1.0}, {"time.c": SPEED_NOISE_RATIO})
    past_margin = compare_speed({"time.c": 1.0}, {"time.c": SPEED_NOISE_RATIO * (1 + 1e-9)})
    assert at_margin.passed
    assert not past_margin.passed


def test_regression_speed_fails_a_missing_timing() -> None:
    assert not compare_speed({"time.c": 1.0e-3}, {}).passed
    assert not compare_speed({}, {"time.c": 1.0e-3}).passed


def test_flipped_case_is_compared_against_its_ledger_expectation() -> None:
    flips = {CASE: Flip(name=CASE, ledger="UP-9.1-17", expect={"flag": True, "val:s": [2.0]})}
    oracle: dict[str, Output] = {"flag": False, "val:s": _floats(2.0)}
    fixed: dict[str, Output] = {"flag": True, "val:s": _floats(2.0)}
    assert compare_case(CASE, oracle, fixed, flips).passed
    assert not compare_case(CASE, oracle, oracle, flips).passed
    assert not compare_case("geo.other", oracle, fixed, flips).passed


def test_load_flipped_rejects_an_unknown_case(tmp_path: Path) -> None:
    flipped = tmp_path / "flipped.toml"
    flipped.write_text(
        '[[case]]\nname = "geo.missing"\nledger = "UP-9.1-17"\nexpect = {}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match=r"geo\.missing"):
        load_flipped(flipped, (_case("geo", "scale_check"),))
    known = load_flipped(flipped, (_case("geo", "missing"),))
    assert known["geo.missing"].ledger == "UP-9.1-17"


def test_load_flipped_rejects_a_malformed_ledger_id(tmp_path: Path) -> None:
    flipped = tmp_path / "flipped.toml"
    flipped.write_text(
        '[[case]]\nname = "geo.scale_check"\nledger = "9.1-17"\nexpect = {}\n', encoding="utf-8"
    )
    with pytest.raises(SystemExit, match=r"9\.1-17"):
        load_flipped(flipped, (_case("geo", "scale_check"),))


def test_repository_flipped_file_names_only_loaded_cases() -> None:
    text = FLIPPED_FILE.read_text(encoding="utf-8")
    assert "UP-9.1-17" in text
    assert "UP-9.1-18" in text
    assert load_flipped(FLIPPED_FILE, load_all_cases()) == {}


def test_intended_case_is_compared_against_its_expectation() -> None:
    intended = {CASE: Intended(name=CASE, reason="decided", expect={"flag": True})}
    oracle: dict[str, Output] = {"flag": False}
    chosen: dict[str, Output] = {"flag": True}
    assert compare_case(CASE, oracle, chosen, {}, intended).passed
    assert not compare_case(CASE, oracle, oracle, {}, intended).passed
    assert compare_case(CASE, oracle, oracle, {}).passed


def _intended_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "intended.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_intended_rejects_an_unknown_case(tmp_path: Path) -> None:
    path = _intended_file(tmp_path, INTENDED_ENTRY.replace("scale_check", "missing"))
    with pytest.raises(SystemExit, match=r"geo\.missing"):
        load_intended(path, (_case("geo", "scale_check"),), {})
    assert load_intended(path, (_case("geo", "missing"),), {})["geo.missing"].reason == "x"


def test_load_intended_rejects_a_repeated_case(tmp_path: Path) -> None:
    path = _intended_file(tmp_path, INTENDED_ENTRY * 2)
    with pytest.raises(SystemExit, match="more than once"):
        load_intended(path, (_case("geo", "scale_check"),), {})


def test_load_intended_rejects_a_flipped_case(tmp_path: Path) -> None:
    path = _intended_file(tmp_path, INTENDED_ENTRY)
    flips = {CASE: Flip(name=CASE, ledger="UP-9.1-17", expect={})}
    with pytest.raises(SystemExit, match="both"):
        load_intended(path, (_case("geo", "scale_check"),), flips)


def test_load_intended_rejects_a_missing_reason(tmp_path: Path) -> None:
    path = _intended_file(tmp_path, INTENDED_ENTRY.replace('reason = "x"', 'reason = " "'))
    with pytest.raises(SystemExit, match="reason"):
        load_intended(path, (_case("geo", "scale_check"),), {})


def test_intended_cases_are_printed_in_the_table() -> None:
    intended = {CASE: Intended(name=CASE, reason="decided", expect={"flag": True})}
    report = compare({"flag": True}, {"flag": True})
    shown = format_table({CASE: report, "geo.other": report}, intended)
    assert "intended" in shown
    assert "intended: decided" in shown
    assert "intended" not in format_table({CASE: report})


def test_repository_intended_file_names_only_loaded_cases() -> None:
    cases = load_all_cases()
    loaded = load_intended(INTENDED_FILE, cases, load_flipped(FLIPPED_FILE, cases))
    assert "core.non_finite_inputs_raise_e8s_errors" in loaded


def test_outputs_round_trip_through_their_encoding() -> None:
    outputs: dict[str, Output] = {
        "pos:p": np.arange(6, dtype=np.float64).reshape(3, 2),
        "ids": np.array([3, -1], dtype=np.int64),
        "doc": {"b": [1, 2.5, None], "a": "é"},
    }
    encoded = encode_outputs(outputs)
    decoded = decode_outputs(encoded)
    assert list(decoded) == ["pos:p", "ids", "doc"]
    assert compare(outputs, decoded).passed
    positions = decoded["pos:p"]
    assert isinstance(positions, np.ndarray)
    assert positions.shape == (3, 2)
    assert encode_outputs(decoded) == encoded


def test_encoding_rejects_an_unsupported_dtype() -> None:
    with pytest.raises(SystemExit, match="float32"):
        # cast: the encoder must refuse an array the Output type does not allow.
        encode_outputs({"val:v": cast(Output, np.zeros(2, dtype=np.float32))})


@pytest.mark.parametrize(
    "record",
    [
        b'{"dtype":"json","key":"k","nbytes":2}',
        b'{"dtype":"json","key":"k","nbytes":-1}\n',
        b'{"dtype":"json","key":"k","nbytes":3}\n[]',
        b'{"dtype":"json","key":"k","nbytes":99999999999}\n[]',
        b'{"dtype":"json","key":"k","nbytes":true}\n[]',
        b'{"dtype":"json","key":"k","nbytes":2.0}\n[]',
        b'{"dtype":"json","key":1,"nbytes":2}\n[]',
        b'{"dtype":"json","key":"k","nbytes":2,"extra":0}\n[]',
        b'{"dtype":"json","nbytes":2}\n[]',
        b'["dtype","json"]\n[]',
        b"not json\n[]",
        b'{"dtype":"json","key":"k","nbytes":2}\n[!',
        b'{"dtype":"<f8","key":"k","nbytes":8}\n12345678',
        b'{"dtype":"<f8","key":"k","nbytes":8,"shape":[2]}\n12345678',
        b'{"dtype":"<f8","key":"k","nbytes":8,"shape":[-1]}\n12345678',
        b'{"dtype":"<f4","key":"k","nbytes":8,"shape":[2]}\n12345678',
    ],
)
def test_regression_decode_refuses_a_malformed_or_truncated_record(record: bytes) -> None:
    with pytest.raises(SystemExit, match=r"^E: "):
        decode_outputs(record)


def test_load_cases_reads_the_time_self_check() -> None:
    cases = load_cases("time")
    assert [case.name for case in cases] == ["clock_on_200_seeded_timelines"]
    assert all(case.area == "time" for case in cases)


def test_load_cases_rejects_an_unknown_area() -> None:
    with pytest.raises(SystemExit, match="cases_nowhere"):
        load_cases("nowhere")


def test_first_difference_names_the_first_differing_record() -> None:
    oracle = [{"jcs": {"ok": "[1]"}}, {"jcs": {"ok": "[0.1]"}}, {"jcs": {"ok": "[2e-7]"}}]
    core = [
        {"jcs": {"ok": "[1]"}},
        {"jcs": {"ok": "[0.10000000000000001]"}},
        {"jcs": {"ok": "[x]"}},
    ]
    assert first_difference("c/values", oracle, oracle) is None
    assert first_difference("c/values", oracle, core) == (
        "c/values[1]: oracle {'jcs': {'ok': '[0.1]'}}, "
        "core {'jcs': {'ok': '[0.10000000000000001]'}}"
    )


def test_first_difference_flags_a_missing_record() -> None:
    assert first_difference("c/nesting", [1, 2, 3], [1, 2]) == (
        "c/nesting: oracle has 3 records, core 2"
    )
