#!/usr/bin/env python3

"""
compare_parity_digests.py --- gates the 18 parity digests of the CI cells, failing closed

Each `parity` CI leg writes, per hash seed, `<cell>-seed<n>.txt` (the digests of the areas
already ported to the core) and `<cell>-seed<n>.all` (the digests of every area). The ported
digests must be byte-identical across the 6 cells and 3 seeds. The other areas still run
today's Python, whose last bits differ between C math libraries (2026-11-24, PR #92: Windows
and Linux differ on `time.clock_on_200_seeded_timelines/val:seconds` and `val:counts_back`), so
their differences are printed and never fail the job. Runs on the runner's system Python, so
it uses the standard library only.

Usage: `python3 scripts/compare_parity_digests.py <folder>`

Contains:
    EXPECTED_DIGESTS: the digest files each kind must have, one per cell and seed
    read_digests(): reads `<sha256>  <case>/<key>` lines into a key-to-digest mapping
    differing_keys(): lists the keys whose digests differ between two digest files
    compare_folder(): checks the ported digests and reports the others; returns the exit code
    main(): compares the folder named on the command line
"""

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

EXPECTED_DIGESTS = 18
PORTED_SUFFIX = ".txt"
EVERY_SUFFIX = ".all"
SEED_MARK = "-seed"


def read_digests(text: str) -> dict[str, str]:
    """Reads digest lines into key-to-digest pairs.

    Args:
        text: The text of one digest file, one `<sha256>  <case>/<key>` line per output.

    Returns:
        digests: Output key to its SHA-256; a line without two fields maps to an empty key.
    """
    digests: dict[str, str] = {}
    for line in text.splitlines():
        digest, _, key = line.partition("  ")
        digests[key] = digest
    return digests


def differing_keys(first: Mapping[str, str], other: Mapping[str, str]) -> list[str]:
    """Lists the keys present in only one of two digest mappings, or with different digests."""
    return sorted(key for key in set(first) | set(other) if first.get(key) != other.get(key))


def _files(folder: Path, suffix: str) -> list[Path]:
    return sorted(path for path in folder.rglob(f"*{SEED_MARK}*{suffix}") if path.is_file())


def _differences(files: Sequence[Path]) -> list[tuple[Path, list[str]]]:
    first = read_digests(files[0].read_text(encoding="utf-8"))
    found = []
    for path in files[1:]:
        keys = differing_keys(first, read_digests(path.read_text(encoding="utf-8")))
        if keys:
            found.append((path, keys))
    return found


def compare_folder(folder: Path) -> int:
    """Checks the ported digests and reports cross-cell differences of the other areas.

    Args:
        folder: The folder the CI job downloaded every digest file into.

    Returns:
        code: 0 when both kinds number EXPECTED_DIGESTS and the ported digests are equal, else
            1; differences in areas not yet ported are printed as W: lines only.
    """
    ported, every = _files(folder, PORTED_SUFFIX), _files(folder, EVERY_SUFFIX)
    for kind, files in (("ported", ported), ("all-area", every)):
        if len(files) != EXPECTED_DIGESTS:
            print(f"E: expected {EXPECTED_DIGESTS} {kind} parity digests, found {len(files)}")
            return 1
    for path, keys in _differences(every):
        print(f"W: {path.name} differs from {every[0].name} on {', '.join(keys)}")
    failures = _differences(ported)
    for path, keys in failures:
        print(f"E: {path.name} differs from {ported[0].name} on {', '.join(keys)}")
    if failures:
        return 1
    lines = len(read_digests(ported[0].read_text(encoding="utf-8")))
    print(f"Found {EXPECTED_DIGESTS} equal parity digests of the ported areas ({lines} outputs)")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    """Compares the digest folder named on the command line and exits with the result."""
    arguments = sys.argv[1:] if argv is None else list(argv)
    if len(arguments) != 1:
        raise SystemExit("E: usage: compare_parity_digests.py <folder>")
    raise SystemExit(compare_folder(Path(arguments[0])))


if __name__ == "__main__":
    main()
