#!/usr/bin/env python3

"""
test_parity_core.py --- gates the compiled core's number text, unit helpers and ID parsers
against the v0.4.0 oracle

Contains:
    test_plain_decimal_text_matches_v040_on_a_million_floats: writes the same text for every
        float, with every decimals count
    test_unit_conversions_match_v040: gives the same angles, facings, rounded values and errors
    test_reference_and_transition_parsing_match_v040_on_the_fuzz_corpus: parses the same
        references, transitions and IDs, with the same errors
"""

from collections.abc import Callable

from tests.parity.run import AreaRun

PREFIX = "core."


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
