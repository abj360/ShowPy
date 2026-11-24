Read-only test data: a `FileRepository` written by e8Show v0.4.0 (six revisions, one show, layout
1). Never edit these files; tests copy them before writing.

Content: revisions 1 and 2 are the v0.3.0 fixture's two revisions, copied byte for byte, so their
`show.json` has no header (format 1). Revision 3 is the first v0.4.0 save: it re-encodes the
stored documents in format 2 (`"format": "e8.show"`, IDs and forms migrated). Revision 4 gives
the first spot a curved row on transition 1 to 2. Revision 5 pins the third spot as `soloist`.
Revision 6 writes the notes document `/notes/notes.json` (header `"format": "e8.notes"`), holding
one designer note `note_1` anchored on set 1. v0.4.0 has no notes functions, so the script builds
the document from its private notes models and writes it through `Workspace.write`, the way a host
app stores it. Revisions 3 to 6 carry the show header.

`expected.json` holds `shows`, the show of each revision ID as `oracle_json` dumps it, plus the
head show and the revision list.

Generated from tag v0.4.0 by `scripts/make_legacy_fixture.py` as committed with this fixture, from
the committed v0.3.0 fixture. Regenerate: in a clean checkout of v0.4.0, with that checkout's own
environment, run

    PYTHONHASHSEED=0 PYTHONPATH=. uv run --frozen python <repo>/scripts/make_legacy_fixture.py \
        --out <repo>/tests/fixtures/repos/v0.4.0 --show v040
