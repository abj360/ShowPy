#!/usr/bin/env python3

"""
test_compare_parity_digests.py --- checks the CI gate over the 18 parity digests

Contains:
    test_differing_keys_lists_changed_and_one_sided_keys: compares two digest mappings
    test_empty_ported_set_passes_and_reports_other_areas: 18 empty ported digests pass
    test_regression_fewer_than_18_digests_fail: a missing cell or seed fails either kind
    test_a_differing_ported_digest_fails_and_names_its_key: ported outputs must be identical
    test_main_exits_with_the_comparison_result: the command-line exit code
"""

from pathlib import Path

import pytest

from scripts.compare_parity_digests import (
    EXPECTED_DIGESTS,
    compare_folder,
    differing_keys,
    main,
    read_digests,
)

CELLS = [f"{os}-py{python}" for os in ("Linux", "macOS", "Windows") for python in ("3.13", "3.14")]
SEEDS = ("0", "1", "2")
SECONDS = "time.clock_on_200_seeded_timelines/val:seconds"
QUERIES = "time.clock_on_200_seeded_timelines/queries"


def _line(digest: str, key: str) -> str:
    return f"{digest * 64}  {key}\n"


def _write(folder: Path, ported: str, every: str, *, skip: int = 0) -> None:
    folder.mkdir(exist_ok=True)
    names = [f"{cell}-seed{seed}" for cell in CELLS for seed in SEEDS]
    for name in names[skip:]:
        (folder / f"{name}.txt").write_text(ported, encoding="utf-8")
        windows = name.startswith("Windows")
        text = every.replace(_line("a", SECONDS), _line("b", SECONDS)) if windows else every
        (folder / f"{name}.all").write_text(text, encoding="utf-8")


def test_differing_keys_lists_changed_and_one_sided_keys() -> None:
    first = read_digests(_line("a", SECONDS) + _line("c", QUERIES))
    other = read_digests(_line("b", SECONDS) + _line("c", QUERIES) + _line("d", "x/y"))
    assert differing_keys(first, other) == [SECONDS, "x/y"]
    assert differing_keys(first, dict(first)) == []


def test_empty_ported_set_passes_and_reports_other_areas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "", _line("a", SECONDS) + _line("c", QUERIES))
    assert len(CELLS) * len(SEEDS) == EXPECTED_DIGESTS
    assert compare_folder(tmp_path) == 0
    out = capsys.readouterr().out
    assert f"W: Windows-py3.13-seed0.all differs from Linux-py3.13-seed0.all on {SECONDS}" in out
    assert QUERIES not in out
    assert "Found 18 equal parity digests of the ported areas (0 outputs)" in out


@pytest.mark.parametrize("ported", ["", _line("a", SECONDS)])
def test_regression_fewer_than_18_digests_fail(
    ported: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, ported, _line("c", QUERIES), skip=1)
    assert compare_folder(tmp_path) == 1
    assert "E: expected 18 ported parity digests, found 17" in capsys.readouterr().out
    _write(tmp_path / "again", ported, "")
    for path in (tmp_path / "again").glob("Linux-py3.13-seed0.all"):
        path.unlink()
    assert compare_folder(tmp_path / "again") == 1


def test_a_differing_ported_digest_fails_and_names_its_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, _line("a", SECONDS) + _line("c", QUERIES), "")
    (tmp_path / "macOS-py3.14-seed2.txt").write_text(
        _line("a", SECONDS) + _line("d", QUERIES), encoding="utf-8"
    )
    assert compare_folder(tmp_path) == 1
    assert f"E: macOS-py3.14-seed2.txt differs from Linux-py3.13-seed0.txt on {QUERIES}" in (
        capsys.readouterr().out
    )


def test_main_exits_with_the_comparison_result(tmp_path: Path) -> None:
    _write(tmp_path, "", "")
    with pytest.raises(SystemExit) as passed:
        main([str(tmp_path)])
    assert passed.value.code == 0
    with pytest.raises(SystemExit) as failed:
        main([str(tmp_path / "missing")])
    assert failed.value.code == 1
