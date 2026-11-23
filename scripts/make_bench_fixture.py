#!/usr/bin/env python3

"""
make_bench_fixture.py --- writes the stored documents of a large seeded show as one gzip file

Run it from a checkout of a released tag with that checkout's own environment, so the bytes
are the ones that release wrote:

    PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_bench_fixture.py \
        --out <repo>/tests/bench/fixtures/v0.3.0-300x60.json.gz

The file holds one JSON object mapping each stored path to its document text, gzipped with a
zero timestamp so equal documents give equal bytes.

Contains:
    stored_documents(): encodes the seeded show as stored path to document text
    main(): parses the command line and writes the gzip file
"""

import argparse
import gzip
import json
from pathlib import Path

from e8s.show.documents import encode_show
from tests.fixtures.generators import seeded_show

SEED = 11
SPOTS = 300
SETS = 60
GZIP_LEVEL = 9


def stored_documents() -> dict[str, str]:
    """Encodes the seeded show of SPOTS spots and SETS sets as stored path to document text."""
    show = seeded_show(SEED, SPOTS, SETS)
    return {path: raw.decode() for path, raw in sorted(encode_show(show).items())}


def main() -> int:
    """Parses the command line and writes the gzip file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out: Path = parser.parse_args().out
    text = json.dumps(stored_documents(), sort_keys=True, ensure_ascii=False)
    out.write_bytes(gzip.compress(text.encode(), compresslevel=GZIP_LEVEL, mtime=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
