#!/usr/bin/env python3

"""
test_parity_core.py --- gates the compiled core's number text, unit helpers, ID parsers,
canonical JSON, content hashes and strict JSON reader against the v0.4.0 oracle

Contains:
    test_plain_decimal_text_matches_v040_on_a_million_floats: writes the same text for every
        float, with every decimals count
    test_unit_conversions_match_v040: gives the same angles, facings, rounded values and errors
    test_reference_and_transition_parsing_match_v040_on_the_fuzz_corpus: parses the same
        references, transitions and IDs, with the same errors
    test_content_hash_matches_v040_on_every_stored_fixture: hashes, names and parses every file
        under tests/fixtures/repos/ as v0.4.0 did, and hashes a 300 x 60 show the same way
    test_canonical_bytes_match_v040_on_generated_json: writes the same canonical bytes, values,
        hashes and errors for seeded JSON values, numbers in ECMAScript text included
    test_strict_json_refuses_what_v040_refuses: parses and refuses the same texts, with the same
        error codes, messages and contexts, and parses a 300 x 60 show the same way
    test_float_comparison_and_id_ranges_match_v040: compares floats, ranks noise, splits counts,
        expands and compacts ID ranges, sorts naturally, suggests close names and guards CSV
        cells as v0.4.0 did
    test_canonical_and_strict_json_match_v040_value_by_value: on this machine, gives the
        oracle's record for every generated value and hostile text, naming the first that differs
    test_value_by_value_check_finds_a_changed_float_text: the same check flags one record of
        the real outputs whose float text is changed
"""

from collections.abc import Callable, Sequence
from typing import cast

from e8s import (
    approx_equal,
    asset_uri,
    canonical_value,
    chunk_counts,
    close_matches,
    content_hash,
    format_ranges,
    jcs,
    natural_key,
    natural_sort,
    parse_range,
    split_id,
)
from e8s._core import core
from e8s.core import csv_cells, strict_json, util
from tests.parity.compare import first_difference
from tests.parity.run import AreaRun

PREFIX = "core."
# Per-value outputs: one record per generated value or hostile text, in input order.
PER_VALUE_KEYS = (
    ("core.canonical_bytes_on_generated_json", "values"),
    ("core.strict_json_on_hostile_text", "parsed"),
    ("core.strict_json_on_hostile_text", "nesting"),
    ("core.strict_json_on_hostile_text", "copied"),
)
CHANGED_RECORD = 7


def _passed(run: AreaRun, case: str) -> None:
    report = run.reports[f"{PREFIX}{case}"]
    assert report.discrete_mismatches == ()
    assert report.past_gate == ()


def test_plain_decimal_text_matches_v040_on_a_million_floats(
    parity_area: Callable[[str], AreaRun],
) -> None:
    run = parity_area("core")
    _passed(run, "plain_decimal_text_on_a_million_floats")
    _passed(run, "plain_decimal_text_on_20000_floats")


def test_unit_conversions_match_v040(parity_area: Callable[[str], AreaRun]) -> None:
    run = parity_area("core")
    _passed(run, "unit_conversions_on_seeded_values")
    _passed(run, "unit_conversions_timed")
    assert run.reports[f"{PREFIX}unit_conversions_on_seeded_values"].values > 0


def test_reference_and_transition_parsing_match_v040_on_the_fuzz_corpus(
    parity_area: Callable[[str], AreaRun],
) -> None:
    run = parity_area("core")
    _passed(run, "reference_and_transition_corpus")
    _passed(run, "reference_and_transition_corpus_timed")


def test_content_hash_matches_v040_on_every_stored_fixture(
    parity_area: Callable[[str], AreaRun],
) -> None:
    assert (content_hash, asset_uri) == (core.content_hash, core.asset_uri)
    run = parity_area("core")
    _passed(run, "content_hash_on_stored_fixtures")
    _passed(run, "content_hash_on_a_300_by_60_show")


def test_canonical_bytes_match_v040_on_generated_json(
    parity_area: Callable[[str], AreaRun],
) -> None:
    assert (canonical_value, jcs) == (core.canonical_value, core.jcs)
    run = parity_area("core")
    _passed(run, "canonical_bytes_on_generated_json")


def test_strict_json_refuses_what_v040_refuses(parity_area: Callable[[str], AreaRun]) -> None:
    assert strict_json.parse_strict_json is core.parse_strict_json
    assert strict_json.strict_json_copy is core.strict_json_copy
    run = parity_area("core")
    _passed(run, "strict_json_on_hostile_text")
    _passed(run, "strict_json_on_a_300_by_60_show")


def test_float_comparison_and_id_ranges_match_v040(
    parity_area: Callable[[str], AreaRun],
) -> None:
    served = (approx_equal, chunk_counts, close_matches, format_ranges, natural_key, natural_sort)
    assert served == (
        core.approx_equal,
        core.chunk_counts,
        core.close_matches,
        core.format_ranges,
        core.natural_key,
        core.natural_sort,
    )
    assert (parse_range, split_id, util.noise_rank) == (
        core.parse_range,
        core.split_id,
        core.noise_rank,
    )
    assert csv_cells.neutralize_cell is core.neutralize_cell
    run = parity_area("core")
    for case in (
        "float_comparison_on_seeded_values",
        "float_comparison_timed",
        "id_ranges_on_seeded_text",
        "id_ranges_timed",
        "close_matches_on_seeded_names",
        "close_matches_timed",
        "csv_cells_on_seeded_text",
    ):
        _passed(run, case)


def _records(outputs: object) -> Sequence[object]:
    return cast("Sequence[object]", outputs)


def test_canonical_and_strict_json_match_v040_value_by_value(
    parity_area: Callable[[str], AreaRun],
) -> None:
    run = parity_area("core")
    for case, key in PER_VALUE_KEYS:
        oracle, core_records = _records(run.oracle[case][key]), _records(run.core[case][key])
        assert len(oracle) > CHANGED_RECORD
        difference = first_difference(f"{case}/{key}", oracle, core_records)
        assert difference is None, difference


def test_value_by_value_check_finds_a_changed_float_text(
    parity_area: Callable[[str], AreaRun],
) -> None:
    case, key = PER_VALUE_KEYS[0]
    oracle = _records(parity_area("core").oracle[case][key])
    changed = [*oracle]
    changed[CHANGED_RECORD] = {"jcs": {"ok": "[0.10000000000000001]"}}
    difference = first_difference(f"{case}/{key}", oracle, changed)
    assert difference is not None
    assert difference.startswith(f"{case}/{key}[{CHANGED_RECORD}]: oracle ")
    assert difference.endswith("core {'jcs': {'ok': '[0.10000000000000001]'}}")
