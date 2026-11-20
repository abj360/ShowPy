Read-only test data: a `FileRepository` written by ShowPy v0.1.1 (import name `stage`; three
revisions, one show). Never edit these files; tests copy them before writing.

Content: the example two-set show with tags on one spot, the selection `front-four`, a stored
`-0.0`, a build script on set 2, a curved path row and an entity pin, saved as three revisions.

Generated from tag v0.1.1 by `scripts/make_legacy_fixture.py` as committed with this fixture.
Regenerate: in a clean checkout of v0.1.1, with that checkout's own environment, run

    PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.1.1 --package stage --show example
