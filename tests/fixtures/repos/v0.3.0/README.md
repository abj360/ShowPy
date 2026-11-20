Read-only test data: a `FileRepository` written by ShowPy v0.3.0 (two revisions, one show).
Never edit these files; tests copy them before writing.

Content: revision 1 is the seeded show (24 spots, 6 sets) with set `2A` inserted after set 2 and
renumbered, so sets 3 to 7 carry explicit keys; two trumpets `T1` and `T2`; a zone whose
`applies_to` is `T1 + T2 & T2`; and a last set 8 holding the text form `title`. Revision 2 adds
sets `1a`, `1A` and `con` by hand as v0.2 stored them (folders named by ID, no `key` field),
committed through the backend so the v0.3.0 commit check, which refuses them, never runs.

Generated from tag v0.3.0 by `scripts/make_legacy_fixture.py` as committed with this fixture.
Regenerate: in a clean checkout of v0.3.0, with that checkout's own environment, run

    PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.3.0 --show v030
