#!/usr/bin/env python3

"""
test_migrate.py --- benchmarks decoding and migrating a large show written by v0.3.0

The fixture holds the stored documents of a seeded 300-spot, 60-set show exactly as v0.3.0
wrote them (format version 1). Decoding them parses every document strictly, runs the v1 to
v2 sub-steps, validates the show with legacy IDs accepted, runs the ids step and validates
again. The budget is 1 s for that, as the fastest of three timed rounds; unlike the targets in
test_budgets.py, it fails the benchmark run when missed. The full `migrate_documents`, which
also encodes the v2 documents, is timed beside it.

Contains:
    test_decode_and_migrate_a_v030_show: decodes the v1 tree within the budget
    test_migrate_documents_of_a_v030_show: times the migration to v2 documents
"""

import gzip
import json
import timeit
from collections.abc import Callable
from pathlib import Path

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from e8s.show.documents import decode_show, migrate_documents
from e8s.show.migrate import CURRENT_VERSION, read_version
from e8s.show.models import Show
from tests.conftest import BenchTier

pytestmark = pytest.mark.bench

FIXTURE = Path(__file__).parent / "fixtures" / "v0.3.0-300x60.json.gz"
BUDGET_S = 1.0
GATE_ROUNDS = 3
SPOTS = 300
SETS = 60


@pytest.fixture(scope="module")
def v1_docs() -> dict[str, bytes]:
    """Reads the stored documents v0.3.0 wrote for the seeded 300 x 60 show."""
    stored: dict[str, str] = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    docs = {path: text.encode() for path, text in stored.items()}
    assert read_version(docs) == 1
    return docs


def test_decode_and_migrate_a_v030_show(
    benchmark: BenchmarkFixture, tier: BenchTier, v1_docs: dict[str, bytes]
) -> None:
    def decode() -> Show:
        return decode_show(v1_docs)

    benchmark.extra_info.update({"tier": tier.name, "budget_s": BUDGET_S})
    show = benchmark(decode)
    assert (len(show.cast.spots), len(show.sets)) == (SPOTS, SETS)
    assert min(timeit.repeat(decode, number=1, repeat=GATE_ROUNDS)) < BUDGET_S


def test_migrate_documents_of_a_v030_show(
    benchmark: BenchmarkFixture, tier: BenchTier, v1_docs: dict[str, bytes]
) -> None:
    benchmark.extra_info.update({"tier": tier.name})
    # pytest-benchmark leaves pedantic() untyped; binding it to a typed name keeps mypy strict.
    pedantic: Callable[..., tuple[dict[str, bytes], object]] = benchmark.pedantic
    migrated, _ = pedantic(migrate_documents, args=(v1_docs,), rounds=GATE_ROUNDS, iterations=1)
    assert read_version(migrated) == CURRENT_VERSION
