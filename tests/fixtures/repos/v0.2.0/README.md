Read-only test data: a `FileRepository` written by ShowPy v0.2.0 (three revisions, one show).
Never edit these files; tests copy them before writing.

Generated from tag v0.2.0 by `scripts/make_legacy_fixture.py`. The script as committed with the
v0.1.1 and v0.3.0 fixtures reproduces `repo/` byte for byte; `legacy_content_hash` in
`expected.json` depends on the hash seed of the original run, because v0.2.0 hashed sets in
iteration order. Regenerate: in a clean checkout of v0.2.0, with that checkout's own environment,
run

    PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.2.0
