Read-only benchmark data. Never edit these files.

`v0.3.0-300x60.json.gz` holds the stored documents of the seeded show (seed 11, 300 spots, 60
sets) exactly as ShowPy v0.3.0 encoded them: one JSON object mapping each stored path to its
document text, gzipped with a zero timestamp (977 KB; 13.7 MiB of documents unpacked).
`tests/bench/test_migrate.py` decodes and migrates it.

Generated from tag v0.3.0 by `scripts/make_bench_fixture.py` as committed with this file.
Regenerate: in a clean checkout of v0.3.0, with that checkout's own environment, run

```sh
PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_bench_fixture.py \
    --out <repo>/tests/bench/fixtures/v0.3.0-300x60.json.gz
```
