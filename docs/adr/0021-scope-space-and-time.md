# ADR 0021: The library's scope narrows to space and time

- Status: Accepted (2026-11-22)
- Date: 2026-11-22
- Deciders: the maintainer (abj360)

## Context

The library grew outward from its core. Besides positions, motion, the count timeline and the
show model, it renders sheets and video frames, prints coordinate sheets and PDFs, formats text
and CSV tables, publishes shows with performer claims and join links, and keeps designer notes.
Measured on `main` at `8b92d66` from `showpy/_api_table.py` (577 public names):

| Package or part          | Public names | What it does                                                           |
| ------------------------ | ------------ | ---------------------------------------------------------------------- |
| `showpy.export`          | 45           | interchange, publishing, print and PDF, tables, video                  |
| `showpy.sheet`           | 44           | templates, landmarks, zones, coordinate wording, rendering, fitting    |
| `showpy.notes`           | 8            | designer notes and their anchors                                       |
| pandas results elsewhere | 5            | `Set.table`, `paths_table`, `speeds`, `energy_curve`, `phrase_profile` |

Presentation and collaboration features cost the library in three ways. They pull in the four
optional dependencies of ADR 0010 (pandas, Pillow, segno, imageio-ffmpeg). They hold 63 of the
339 open ledger defects of the default suite (appendix). And each host already decides how a show
is drawn, printed, shared and discussed, as it decides permissions (ADR 0003): two hosts rarely
want the same print layout or the same publishing flow.

## Decision

1. **Scope.** The maintainer decided on 2026-11-22 that the library keeps space and time:
   positions, motion, timing and the show model, with the checks, storage and migrations that
   serve them. Apps render, print, publish and keep notes. Names outside that scope leave the
   library on the timeline below; nothing leaves without warning first.
2. **Timeline.** Every name below warns with `DeprecatedWarning` from the release in "Warns from"
   and is gone in the release in "Gone in". Counts are taken from `showpy/_api_table.py` and the
   code at `8b92d66`, except where the row says it is an estimate.

   | Names                                                                                                                         | Count    | Warns from | Gone in  | Use instead                                                                                                |
   | ----------------------------------------------------------------------------------------------------------------------------- | -------- | ---------- | -------- | ---------------------------------------------------------------------------------------------------------- |
   | Interchange at its old path: `export_json`, `import_json`, `import_fonts`, `import_positions`                                 | 4        | `v0.4.0`   | `v0.6.0` | The same top-level names, now defined in `showpy/show/`                                                    |
   | Publishing: 15 functions and `Claim`, `Performer`, `PerformerView`, `Publication`                                             | 19       | `v0.4.0`   | `v0.6.0` | Nothing: leaves the library                                                                                |
   | Print and PDF: `chart`, `coordinate_sheet`, `coordinate_sheets`, `cue_sheet`, `to_pdf`, `escape_text` and four models         | 10       | `v0.4.0`   | `v0.6.0` | Position and transition data the app lays out (`coordinate_table`, `describe_transition`, added by v0.6.0) |
   | Tables and the format registry: eight names and `TextTable`                                                                   | 9        | `v0.4.0`   | `v0.6.0` | Arrays from `frames` (added by v0.6.0)                                                                     |
   | Video: `export_video`, `render_frame`, `video_canvas`                                                                         | 3        | `v0.4.0`   | `v0.6.0` | Nothing: apps render from `frames`                                                                         |
   | pandas results: `speeds`, `energy_curve`, `phrase_profile`, `paths_table`, `Set.table`                                        | 5        | `v0.4.0`   | `v0.6.0` | Array results under the same names                                                                         |
   | Coordinate wording: `to_coords`, `from_coords`, `coordinate_presets`, `set_coordinates`, `CoordinateSystem`, `CoordinateKind` | 6        | `v0.4.0`   | `v0.6.0` | `locate`, `at_location`, `Location` (added by v0.6.0)                                                      |
   | Landmarks and zones: 11 functions and `Landmark`, `Zone`, `ZoneKind`                                                          | 14       | `v0.4.0`   | `v0.6.0` | Sheet markings and `markings_near` (added by v0.6.0)                                                       |
   | `proscenium_stage`; `SheetCanvas`, `draw_sheet`, `render_sheet`                                                               | 4        | `v0.4.0`   | `v0.6.0` | `floor` (added by v0.6.0); nothing for rendering                                                           |
   | Other sheet names that change shape (`sheet_templates`, `get_sheet`, `customize`, `GuideLine`, `on_surface`, ...)             | up to 16 | `v0.4.0`   | `v0.6.0` | `sheets`, `sheet`, `field`, `floor`, `Marking`, `on_sheet`; ADR 0022 lists each                            |
   | `Show.coordinates`, `Show.zones`, `Spot.claimable`                                                                            | 3        | `v0.4.0`   | `v0.6.0` | Dropped by the v3 format step (ADR 0023), listed in its report                                             |
   | Notes: `Note`, `add_note`, `edit_note`, `remove_note`, `notes_for`, `notes_near`, `accept_draft`                              | 7        | `v0.4.0`   | `1.0.0`  | The `notes.json` document, handled by the app; `Anchor` moves to `showpy/show/`                            |

   How each count was taken:
   - The five `showpy.export` rows add up to its 45 public names (`showpy.export.data` 11,
     `models` 9, `pdf` 2, `positions` 1, `print_sheets` 4, `publish` 15, `video` 3). The four
     print models are `Chart`, `ChartRow`, `CoordinateSheet` and `SheetRow`; the eight table
     names are `FormatRegistry`, `as_table`, `export_frames_csv`, `formats`,
     `register_exporter`, `register_importer`, `to_csv` and `to_text`.
   - The 11 landmark and zone functions are `landmark`, `landmark_anchor`, `landmark_keys`,
     `nearest_landmarks`, `zones_at`, `line_landmark`, `point_landmark`, `add_landmark`,
     `remove_landmark`, `add_zone` and `remove_zone`.
   - The sheet rows: 6 + 14 + 4 = 24 names leave outright. Of the other 20 public `showpy.sheet`
     names, up to 16 change shape; that row is an estimate until ADR 0022 lists each name.
   - The pandas row counts the public functions and methods annotated to return a pandas
     `DataFrame` or `Series` outside `showpy.export` (`git grep -E '"pd\.(DataFrame|Series)"'`).
   - The notes row counts 7 of the 8 public `showpy.notes` names; the eighth, `Anchor`, stays
     public and moves, because comments use it (`showpy/show/models.py:364`).
   - Names marked "added by v0.6.0" do not exist yet; the release that removes the old name adds
     its replacement first.

3. **Notes leave `Show` at v0.4.0.** Today a repository stores notes as one of the show's own
   documents, `/show/notes.json` (`showpy/show/document_paths.py:42`), decoded into `Show.notes`
   (`showpy/show/models.py:103`). From format v2 (ADR 0023), `Show` has no `notes` field. The
   v1-to-v2 migration writes the notes to a `notes.json` document beside the show, with its own
   header:

   ```json
   { "format": "e8.notes", "version": 1, "show": "<id>", "notes": [] }
   ```

   Each note's anchor is written after the same migration's ID renames, so anchors name IDs that
   exist. In a repository the document sits beside the show's documents and is written by the
   first save, in the same revision as the v2 documents; a single-file export returns it as a
   second document beside the show. The deprecated `notes` functions keep it in step until
   1.0.0; after that the app owns it.

4. **Messages.** Every deprecation message says the name "leaves the library" or names its
   replacement, and names the release that removes it. Each name is a row in `DEPRECATED` in
   `showpy/_api_table.py`, and a test fails when the package version reaches a row's removal
   version (ADR 0009, decision 5).
5. **Superseded decisions.**
   - ADR 0018 (stored publications hold no join token) is superseded at v0.6.0, when the
     publishing code it governs leaves.
   - ADR 0010's extras leave with the last functions that need them: `qr` (`qr_code`), `video`
     (`export_video`), `render` (`render_sheet`, `draw_sheet`) and `tables` (the five pandas
     results, `cue_sheet`, and `to_csv`, `to_text` and `to_pdf`, which accept a pandas table).
     All four go at v0.6.0, and so does `all`, the fifth extra in `pyproject.toml`, which only
     combines the other four.
6. **Phase 2 changes.**
   - Dropped: E2 (video export failures and hangs), E3 (split `export/publish.py` and
     `export/data.py`, which leave) and F2 (hardening of sheet functions that leave).
   - Deferred to a later phase: the pure module splits A5 (`show/manage.py`), B3 (lane B's
     oversized modules, if any), C5 (`geo/curves.py`, `geo/spacing.py`), D4 (`cast/spots.py`) and
     G6 (`choreo/forms.py`, `choreo/form_text.py`), and the split part of S4 (`cast/select.py`).
     Each would add moved-name deprecation rows to code that a later phase partitions again.
   - A written exception to the project's 500-line module rule follows. At `8b92d66`, 11
     library modules are over 500 lines (`wc -l`, not counting the generated
     `showpy/__init__.py` and the `_api_table.py` list): `export/publish.py` 720, `geo/curves.py`
     597, `cast/spots.py` 589, `show/repositories.py` 585, `show/manage.py` 573,
     `export/data.py` 572, `choreo/forms.py` 571, `choreo/form_text.py` 569, `cast/select.py`
     546, `show/storage.py` 530 and `geo/spacing.py` 508. They stay over the limit until their
     code leaves (the two in `export/`) or a later phase splits them.
7. **Retired ledger IDs.** The 63 ledger IDs in the appendix test only code that leaves. Each
   stays a strict xfail until its code is deleted at v0.6.0, and is deleted with that code, never
   earlier.

## Alternatives

- **Keep every module.** Rejected: the maintainer narrowed the scope. Keeping them means fixing
  63 defects in code that every host replaces with its own presentation anyway.
- **Remove without warning at v0.4.0.** Rejected: ADR 0009 gives a renamed or removed name two
  minor releases of warnings, and v0.4.0 is the first release that can carry them.
- **Remove at v0.5.0.** Rejected: that counts two releases from a v0.3.0 deprecation that never
  happened. v0.3.0 warns about none of these names.

## Consequences

- Consumers that print, publish, render or keep notes through the library move that work into
  the app before v0.6.0 (notes before 1.0.0). v0.4.0 and v0.5.0 warn on every such call.
- The v0.4.0 release notes carry the timeline table above as a "leaving the library" section.
- Interchange (`export_json`, `import_json`, `import_fonts`, `import_positions`) stays, defined
  in `showpy/show/`; its top-level names do not change.
- At v0.6.0 the library drops all five optional extras (`qr`, `video`, `render`, `tables` and
  `all`), and the default ledger line falls by the 63 retired IDs.

## Numbers

| Measure (at `8b92d66` unless stated)                    | Value                                                                      |
| ------------------------------------------------------- | -------------------------------------------------------------------------- |
| Public names                                            | 577                                                                        |
| Names that warn from v0.4.0 and go at v0.6.0            | 77, plus up to 16 sheet names                                              |
| Of these, names that move and keep their top-level name | 4 (interchange)                                                            |
| Notes names that warn from v0.4.0 and go at 1.0.0       | 7 (`Anchor` moves)                                                         |
| Stored fields dropped by the v3 step                    | 3                                                                          |
| Optional extras removed at v0.6.0                       | 5 of 5 (`qr`, `video`, `render`, `tables`, and `all`, which combines them) |
| Phase 2 tasks dropped / deferred                        | 3 / 5, and the split part of a sixth                                       |
| Library modules over 500 lines left by the exception    | 11                                                                         |
| Open ledger IDs retired                                 | 63 of 339 in the default suite                                             |

The 77 are 45 `showpy.export` names (the 4 interchange names, which go only from their old
path), 24 sheet names, 5 pandas results and 3 stored fields.

## What would change our mind

- Two or more hosts that need the same print layout or publishing flow: then bringing that part
  back would be a separate decision.
- A sheet feature that cannot be expressed without landmarks or zones: then ADR 0022 keeps it,
  and this timeline loses that row before v0.6.0.

## References

- ADR 0003 (permissions belong to the host), ADR 0009 (deprecation policy), ADR 0010 (optional
  extras), ADR 0018 (tokenless publication links), ADR 0020 (rename), ADR 0022 (sheets, to be
  written), ADR 0023 (format versions).
- Phase 2 plan, `docs/superpowers/plans/2026-11-18-phase-2-foundations.md`: tasks A5, B3, C5,
  D4, E2, E3, F2, G6 and S4, and Appendix B (ledger IDs assigned to later phases).
- Code: `showpy/_api_table.py`, `showpy/export/`, `showpy/sheet/`, `showpy/notes/`,
  `showpy/show/models.py`, `showpy/show/document_paths.py`, `showpy/cast/models.py`.

## Appendix: retired ledger IDs

Collected at `8b92d66` with `uv run pytest -m defect --co -q` and a collection hook that reads
each `defect` marker, then a scan of every test function for zone, landmark, coordinate,
render, template and publish names. Each ID maps to one test. On 2026-11-22 every ID in the
three tables below was checked again on this branch: all 83 (63 retired, 13 fixed by a kept
task, 7 kept) are collected with `-m defect`, under the same test node and the same ID.

The "phase it was assigned to" column is the phase the Phase 2 plan assigned the ID to
(its Appendix B), before this ADR.

### Retired

#### Export (33)

| ID        | test node                                                                                                   | leaving name or module                                                             | reason                                               | phase it was assigned to |
| --------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------- | ------------------------ |
| UP-9.5-10 | `tests/unit/export/test_publish.py::test_regression_pause_cannot_reopen_a_closed_publication`               | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-11 | `tests/unit/export/test_publish.py::test_regression_republish_from_closed_does_not_go_live`                 | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-13 | `tests/unit/export/test_publish.py::test_regression_claim_spot_rejects_a_time_before_publication`           | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-15 | `tests/unit/export/test_publish.py::test_regression_reassign_rejects_a_time_before_publication`             | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-16 | `tests/unit/export/test_publish.py::test_regression_reassign_rejects_an_unknown_spot`                       | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-17 | `tests/unit/export/test_publish.py::test_regression_reassign_rejects_crew_on_a_performer_spot`              | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-18 | `tests/unit/export/test_publish.py::test_regression_reassign_rejects_an_inactive_spot`                      | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-19 | `tests/unit/export/test_publish.py::test_regression_republish_after_a_spot_rename_keeps_the_claim`          | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-20 | `tests/unit/export/test_publish.py::test_regression_notify_changes_reports_a_deleted_set`                   | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-21 | `tests/unit/export/test_publish.py::test_regression_notify_changes_reports_a_new_set`                       | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-22 | `tests/unit/export/test_publish.py::test_regression_notify_changes_reports_a_coordinate_system_change`      | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-23 | `tests/unit/export/test_publish.py::test_regression_performer_view_needs_a_live_publication`                | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-24 | `tests/unit/export/test_publish.py::test_regression_performer_view_checks_the_show_matches_the_publication` | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-25 | `tests/unit/export/test_publish.py::test_regression_performer_view_after_deletion_gives_a_performer_hint`   | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-26 | `tests/unit/export/test_publish.py::test_regression_publication_version_detects_concurrent_claims`          | `showpy.export.publish` (publish, claims, pause, reassign, performer view, notify) | tests publishing and claims, which leave the library | Phase 3                  |
| UP-9.5-31 | `tests/unit/export/test_data.py::test_regression_coordinate_sheet_csv_names_the_entity_and_performer`       | `showpy.export.data` (`to_text`, `coordinate_sheet`, CSV tables)                   | tests table formatting, which leaves the library     | Phase 5                  |
| UP-9.5-32 | `tests/unit/export/test_data.py::test_regression_to_text_drops_tabs_and_carriage_returns`                   | `showpy.export.data` (`to_text`, `coordinate_sheet`, CSV tables)                   | tests table formatting, which leaves the library     | Phase 5                  |
| UP-9.5-33 | `tests/unit/export/test_data.py::test_regression_to_text_aligns_wide_cjk_characters`                        | `showpy.export.data` (`to_text`, `coordinate_sheet`, CSV tables)                   | tests table formatting, which leaves the library     | Phase 5                  |
| UP-9.5-36 | `tests/unit/export/test_pdf.py::test_regression_wide_table_stays_on_the_page`                               | `showpy.export.pdf`                                                                | tests PDF export, which leaves the library           | Phase 5                  |
| UP-9.5-37 | `tests/unit/export/test_pdf.py::test_regression_long_title_is_clipped_to_the_page`                          | `showpy.export.pdf`                                                                | tests PDF export, which leaves the library           | Phase 5                  |
| UP-9.5-38 | `tests/unit/export/test_pdf.py::test_regression_non_latin_names_are_not_replaced_by_question_marks`         | `showpy.export.pdf`                                                                | tests PDF export, which leaves the library           | Phase 5                  |
| UP-9.5-39 | `tests/unit/export/test_print_sheets.py::test_regression_crossing_labels_tell_both_sides_apart`             | `showpy.export.print_sheets`                                                       | tests print sheets, which leave the library          | Phase 5                  |
| UP-9.5-40 | `tests/unit/export/test_print_sheets.py::test_regression_out_and_back_crossing_is_reported_twice`           | `showpy.export.print_sheets`                                                       | tests print sheets, which leave the library          | Phase 5                  |
| UP-9.5-45 | `tests/unit/export/test_video.py::test_regression_short_audio_does_not_truncate_the_video`                  | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-46 | `tests/unit/export/test_video.py::test_regression_negative_sync_offset_delays_the_audio`                    | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-47 | `tests/unit/export/test_video.py::test_regression_sync_offset_past_the_audio_raises`                        | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-48 | `tests/unit/export/test_video.py::test_regression_track_gain_is_applied`                                    | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-49 | `tests/unit/export/test_video.py::test_regression_frames_cover_the_half_open_range`                         | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-50 | `tests/unit/export/test_video.py::test_regression_adjacent_ranges_do_not_duplicate_the_boundary_frame`      | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-51 | `tests/unit/export/test_video.py::test_regression_mp4_is_faststart`                                         | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-52 | `tests/unit/export/test_video.py::test_regression_mp4_carries_bt709_colour_tags`                            | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-54 | `tests/unit/export/test_video.py::test_regression_frames_do_not_rebuild_the_cast_ids_per_spot`              | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |
| UP-9.5-57 | `tests/unit/export/test_video.py::test_regression_audio_section_limits_playback`                            | `showpy.export.video`                                                              | tests video export, which leaves the library         | Phase 5                  |

#### Sheet (17)

| ID        | test node                                                                                                    | leaving name or module                                               | reason                                                               | phase it was assigned to |
| --------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------ |
| UP-9.4-19 | `tests/unit/sheet/test_coords.py::test_regression_short_label_with_digit_round_trips_or_is_rejected`         | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-20 | `tests/unit/sheet/test_coords.py::test_regression_colliding_direction_labels_round_trip_or_are_rejected`     | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-21 | `tests/unit/sheet/test_coords.py::test_regression_from_coords_reads_negative_long_number`                    | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-22 | `tests/unit/sheet/test_coords.py::test_regression_from_coords_reads_leading_dot_number`                      | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-23 | `tests/unit/sheet/test_coords.py::test_regression_from_coords_reads_exponent_number`                         | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-24 | `tests/unit/sheet/test_coords.py::test_regression_from_coords_reads_number_glued_to_unit`                    | `sheet/coords.py` (`to_coords`, `from_coords`, `coordinate_presets`) | tests coordinate wording, which leaves the library                   | Phase 5                  |
| UP-9.4-25 | `tests/unit/sheet/test_bounds.py::test_regression_landmark_anchor_ignores_repeated_closing_vertex`           | landmarks (`landmark_anchor`)                                        | tests landmark anchors, which leave the library                      | Phase 4                  |
| UP-9.4-26 | `tests/unit/sheet/test_bounds.py::test_regression_landmark_anchor_is_the_area_centroid`                      | landmarks (`landmark_anchor`)                                        | tests landmark anchors, which leave the library                      | Phase 4                  |
| UP-9.4-30 | `tests/unit/sheet/test_templates.py::test_regression_blank_sheets_with_different_content_get_different_refs` | sheet templates (`blank_sheet`, `customize`, `get_sheet`)            | tests the old template functions, which the new sheet model replaces | Phase 5                  |
| UP-9.4-34 | `tests/unit/sheet/test_templates.py::test_regression_customize_resize_updates_ref_and_name`                  | sheet templates (`blank_sheet`, `customize`, `get_sheet`)            | tests the old template functions, which the new sheet model replaces | Phase 5                  |
| UP-9.4-35 | `tests/unit/sheet/test_templates.py::test_regression_customize_resize_does_not_keep_items_off_the_sheet`     | zones kept by `customize` after a resize                             | tests zones, which leave the library                                 | Phase 5                  |
| UP-9.4-36 | `tests/unit/sheet/test_templates.py::test_regression_get_sheet_without_version_returns_latest_saved`         | sheet templates (`blank_sheet`, `customize`, `get_sheet`)            | tests the old template functions, which the new sheet model replaces | Phase 5                  |
| UP-9.4-39 | `tests/unit/sheet/test_render.py::test_regression_svg_is_well_formed_with_control_characters_in_names`       | `sheet/render.py` (`render_sheet`, `SheetCanvas`)                    | tests sheet rendering, which leaves the library                      | Phase 5                  |
| UP-9.4-40 | `tests/unit/sheet/test_render.py::test_regression_svg_draws_zone_holes`                                      | `sheet/render.py` (`render_sheet`, `SheetCanvas`)                    | tests sheet rendering, which leaves the library                      | Phase 5                  |
| UP-9.4-41 | `tests/unit/sheet/test_render.py::test_regression_png_draws_zone_holes`                                      | `sheet/render.py` (`render_sheet`, `SheetCanvas`)                    | tests sheet rendering, which leaves the library                      | Phase 5                  |
| UP-9.4-42 | `tests/unit/sheet/test_render.py::test_regression_canvas_shows_a_front_ensemble_below_the_sheet`             | `sheet/render.py` (`render_sheet`, `SheetCanvas`)                    | tests sheet rendering, which leaves the library                      | Phase 5                  |
| UP-9.4-46 | `tests/unit/sheet/test_templates.py::test_regression_customize_resize_does_not_keep_landmarks_off_the_sheet` | landmarks kept by `customize` after a resize                         | tests landmarks, which leave the library                             | Phase 5                  |

#### Zones in the show layer (3)

| ID        | test node                                                                                                     | leaving name or module              | reason                                          | phase it was assigned to |
| --------- | ------------------------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------- | ------------------------ |
| UP-9.7-47 | `tests/unit/show/test_sections.py::test_regression_join_after_split_keeps_a_zone_window_across_the_split`     | zone windows in `join_sections`     | tests zone windows, which leave the library     | Phase 3                  |
| UP-9.7-73 | `tests/unit/show/test_validate.py::test_regression_validate_show_reports_zone_selectors_naming_unknown_spots` | zone selectors in `validate_show`   | tests zone validation, which leaves the library | Phase 3                  |
| UP-9.7-74 | `tests/unit/show/test_validate.py::test_regression_validate_show_reports_degenerate_zones`                    | degenerate zones in `validate_show` | tests zone validation, which leaves the library | Phase 3                  |

#### Dropped tasks (E2, F2) (9)

| ID        | test node                                                                                          | leaving name or module        | reason                                                                     | phase it was assigned to |
| --------- | -------------------------------------------------------------------------------------------------- | ----------------------------- | -------------------------------------------------------------------------- | ------------------------ |
| UP-9.4-18 | `tests/unit/sheet/test_coords.py::test_regression_to_coords_rejects_unknown_style`                 | `sheet/coords.py` `to_coords` | F2: tests `to_coords`, which leaves the library                            | Phase 2 (dropped)        |
| UP-9.4-27 | `tests/unit/sheet/test_bounds.py::test_regression_nearest_landmarks_rejects_fractional_k`          | `nearest_landmarks`           | F2: tests `nearest_landmarks`, which leaves the library                    | Phase 2 (dropped)        |
| UP-9.4-28 | `tests/unit/sheet/test_bounds.py::test_regression_nearest_landmarks_rejects_nan_point`             | `nearest_landmarks`           | F2: tests `nearest_landmarks`, which leaves the library                    | Phase 2 (dropped)        |
| UP-9.4-29 | `tests/unit/sheet/test_bounds.py::test_regression_nearest_landmarks_rejects_unknown_kind`          | `nearest_landmarks`           | F2: tests `nearest_landmarks`, which leaves the library                    | Phase 2 (dropped)        |
| UP-9.4-32 | `tests/unit/sheet/test_templates.py::test_regression_customize_rejects_non_numeric_size`           | `customize`                   | F2: tests `customize`, which the new sheet model replaces                  | Phase 2 (dropped)        |
| UP-9.4-37 | `tests/unit/sheet/test_templates.py::test_regression_sheet_templates_rejects_unknown_surface_type` | `sheet_templates`             | F2: tests `sheet_templates`, which the new sheet model replaces            | Phase 2 (dropped)        |
| UP-9.4-38 | `tests/unit/sheet/test_render.py::test_regression_pdf_embeds_no_wall_clock_dates`                  | `sheet/render.py` PDF         | F2: tests sheet PDF rendering, which leaves the library                    | Phase 2 (dropped)        |
| UP-9.5-55 | `tests/unit/export/test_video.py::test_regression_ffmpeg_start_failure_is_wrapped`                 | `showpy.export.video`         | E2: tests ffmpeg start failure in video export, which leaves the library   | Phase 2 (dropped)        |
| UP-9.5-56 | `tests/unit/export/test_video.py::test_regression_audio_mux_has_a_timeout`                         | `showpy.export.video`         | E2: tests the ffmpeg mux timeout in video export, which leaves the library | Phase 2 (dropped)        |

#### UP-9.4-45 (1)

| ID        | test node                                                                                 | leaving name or module       | reason                                             | phase it was assigned to      |
| --------- | ----------------------------------------------------------------------------------------- | ---------------------------- | -------------------------------------------------- | ----------------------------- |
| UP-9.4-45 | `tests/unit/sheet/test_models.py::test_regression_show_rejects_zone_id_shared_with_sheet` | zones (`Show.zones`, `Zone`) | tests zone ID validation, which leaves the library | Phase 2 (F1 does not flip it) |

#### Counts

| Group                   | IDs |
| ----------------------- | --- |
| Export                  | 33  |
| Sheet                   | 17  |
| Zones in the show layer | 3   |
| Dropped tasks (E2, F2)  | 9   |
| UP-9.4-45               | 1   |
| Total                   | 63  |

### Fixed by a kept Phase 2 task (context, not retired)

These IDs test code that is kept or survives the move, and flip when their lane merges (E1 for
export, F1 for sheet). Source: `git diff 14eef50 <lane branch> -- tests`.

| ID        | test node                                                                                           | task | branch             |
| --------- | --------------------------------------------------------------------------------------------------- | ---- | ------------------ |
| UP-9.4-08 | `tests/unit/sheet/test_models.py::test_regression_sheet_rejects_negative_size`                      | F1   | `feat/p2-f-sheets` |
| UP-9.4-09 | `tests/unit/sheet/test_models.py::test_regression_sheet_rejects_one_point_outline`                  | F1   | `feat/p2-f-sheets` |
| UP-9.4-10 | `tests/unit/sheet/test_models.py::test_regression_sheet_rejects_malformed_ref`                      | F1   | `feat/p2-f-sheets` |
| UP-9.4-11 | `tests/unit/sheet/test_models.py::test_regression_sheet_rejects_duplicate_landmark_ids`             | F1   | `feat/p2-f-sheets` |
| UP-9.4-14 | `tests/unit/sheet/test_models.py::test_regression_guide_line_rejects_no_points`                     | F1   | `feat/p2-f-sheets` |
| UP-9.4-44 | `tests/unit/sheet/test_models.py::test_regression_sheet_rejects_landmark_alias_colliding_with_name` | F1   | `feat/p2-f-sheets` |
| UP-9.5-12 | `tests/unit/export/test_publish.py::test_regression_claim_spot_rejects_a_naive_time`                | E1   | `feat/p2-e-export` |
| UP-9.5-14 | `tests/unit/export/test_publish.py::test_regression_reassign_rejects_a_naive_time`                  | E1   | `feat/p2-e-export` |
| UP-9.5-29 | `tests/unit/export/test_data.py::test_regression_csv_writes_large_floats_without_exponent`          | E1   | `feat/p2-e-export` |
| UP-9.5-30 | `tests/unit/export/test_data.py::test_regression_csv_keeps_cue_seconds_above_one_thousand_exact`    | E1   | `feat/p2-e-export` |
| UP-9.5-34 | `tests/unit/export/test_data.py::test_regression_export_json_include_accepts_a_single_field_name`   | E1   | `feat/p2-e-export` |
| UP-9.5-43 | `tests/unit/export/test_positions.py::test_regression_import_positions_normalizes_facing`           | E1   | `feat/p2-e-export` |
| UP-9.5-44 | `tests/unit/export/test_positions.py::test_regression_import_positions_rejects_ids_with_spaces`     | E1   | `feat/p2-e-export` |

### Kept (context, not retired)

These IDs sit in folders that lose code, but test behaviour that stays.

| ID        | test node                                                                                                     | surviving name                         | why                                                                  |
| --------- | ------------------------------------------------------------------------------------------------------------- | -------------------------------------- | -------------------------------------------------------------------- |
| UP-9.5-41 | `tests/unit/export/test_positions.py::test_regression_import_positions_reads_a_utf8_bom`                      | `import_positions`                     | survives in `showpy/show/`; BOM handling stays                       |
| UP-9.5-42 | `tests/unit/export/test_positions.py::test_regression_import_positions_reads_semicolon_delimited_csv`         | `import_positions`                     | survives in `showpy/show/`; delimiter handling stays                 |
| UP-9.7-60 | `tests/unit/show/test_manage.py::test_regression_set_counts_rescale_shifts_facing_overrides_and_zone_windows` | `set_counts` rescale                   | facing-override half survives; split the test when zones are removed |
| UP-9.3-82 | `tests/unit/cast/test_rewrite.py::test_regression_stale_selector_does_not_block_unrelated_delete`             | selector rewrite in `cast/rewrite.py`  | selector-rewrite logic survives                                      |
| UP-9.3-83 | `tests/unit/cast/test_rewrite.py::test_regression_old_glued_selector_does_not_block_relabel`                  | selector rewrite in `cast/rewrite.py`  | selector-rewrite logic survives                                      |
| UP-9.7-79 | `tests/unit/show/test_validate.py::test_regression_validate_show_warns_about_placements_far_off_the_sheet`    | `validate_show` off-sheet warning      | an off-sheet warning is needed with any sheet model                  |
| UP-9.7-80 | `tests/unit/show/test_validate.py::test_regression_validate_show_reports_unpinned_sheet_without_a_store`      | `validate_show` sheet dependency check | stays until the new sheet model replaces it                          |

### Ledger line

| Moment                                                                     | Default suite line |
| -------------------------------------------------------------------------- | ------------------ |
| (a) `main` at `8b92d66`                                                    | `Ledger: 339 open` |
| (b) After tasks E1 and F1 merge (the 13 IDs above flip)                    | `Ledger: 326 open` |
| (c) After v0.6.0 deletes the 63 retired tests with their code (only these) | `Ledger: 263 open` |

Lines (b) and (c) count only the changes named; other Phase 2 tasks flip more IDs in between.
`uv run pytest -m defect --co -q` prints `Ledger: 340 open` at `8b92d66`: the 339 of the default
suite plus the one slow ID, UP-9.1-72, which `nox -s slow` reports as `Ledger: 1 open`.

A retired ID stays a strict xfail until its code is deleted at v0.6.0. It is then deleted with
that code, in the same change, never earlier.

## Addendum 2026-11-22: no removal before the replacement exists

The maintainer accepted a rule on 2026-11-22: no public name is removed before its replacement
exists in the library. The timeline above removes several names at v0.6.0 whose replacements it
marks "added by v0.6.0". Those replacements now arrive in v0.7.0, so each such name keeps its
`DeprecatedWarning` from v0.4.0 and is removed in v0.7.0, in the release that adds the
replacement. A name with no replacement ("Nothing: leaves the library") is unaffected, and so are
the interchange names at their old path. Earlier sections stay as written; where they say
"added by v0.6.0", read v0.7.0.

Why: a consumer that follows the warning needs something to move to. Removing a name one release
before its replacement exists leaves a release with neither. At the time of this addendum none of
`frames`, `locate`, `at_location`, `Location`, `markings_near`, `floor`, `sheets`, `sheet`,
`field`, `Marking`, `on_sheet`, `coordinate_table` or `describe_transition` is in the library.

Each row was checked against the replacement it names. The rows not listed here are unchanged.

| Names                                       | Count    | Warns from | Gone in (was)     | Why                                                                                         |
| ------------------------------------------- | -------- | ---------- | ----------------- | ------------------------------------------------------------------------------------------- |
| Interchange at its old path                 | 4        | `v0.4.0`   | `v0.6.0` (same)   | The replacement is the same name, defined in `showpy/show/`, which exists at v0.4.0         |
| Publishing                                  | 19       | `v0.4.0`   | `v0.6.0` (same)   | No replacement                                                                              |
| Print and PDF                               | 10       | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement `coordinate_table` and `describe_transition` is not yet in the library          |
| Tables and the format registry              | 9        | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement is arrays from `frames`                                                         |
| Video                                       | 3        | `v0.4.0`   | `v0.6.0` (same)   | No replacement                                                                              |
| pandas results                              | 5        | `v0.4.0`   | `v0.6.0` (same)   | The names stay and return arrays; no new name is needed                                     |
| Coordinate wording                          | 6        | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement is `locate`, `at_location`, `Location`                                          |
| Landmarks and zones                         | 14       | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement is sheet markings and `markings_near`                                           |
| `proscenium_stage`                          | 1        | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement is `floor`                                                                      |
| `SheetCanvas`, `draw_sheet`, `render_sheet` | 3        | `v0.4.0`   | `v0.6.0` (same)   | No replacement: rendering leaves the library                                                |
| Other sheet names that change shape         | up to 16 | `v0.4.0`   | `v0.7.0` (v0.6.0) | Replacement is `sheets`, `sheet`, `field`, `floor`, `Marking`, `on_sheet`                   |
| `Show.coordinates`, `Show.zones`            | 2        | `v0.4.0`   | `v0.7.0` (v0.6.0) | `to_coords`, `set_coordinates`, `zones_at` and `add_zone` read and write them until they go |
| `Spot.claimable`                            | 1        | `v0.4.0`   | `v0.6.0` (same)   | Belongs to publishing, which has no replacement                                             |
| Notes                                       | 7        | `v0.4.0`   | `1.0.0` (same)    | The `notes.json` document exists from v0.4.0                                                |

Rows that follow from the table:

- `Show.coordinates` and `Show.zones` are stored fields. ADR 0023 drops them at its v3 step at
  v0.6.0. That step cannot drop them before v0.7.0 without breaking the names above that use
  them. ADR 0023 needs its own addendum to say which format step drops them; this addendum does
  not decide it. `Spot.claimable` stays in the v3 step.
- The extras of ADR 0010 follow the last function that needs them: `qr`, `video` and `render`
  still go at v0.6.0. `tables` stays until v0.7.0, because `cue_sheet`, `to_pdf`, `to_csv` and
  `to_text` still accept a pandas table. `all` stays until v0.7.0 with it.
- The retired ledger IDs of the appendix are deleted with their code: IDs that test publishing,
  video, rendering and the stored `Spot.claimable` go at v0.6.0; IDs that test print, tables,
  coordinates, landmarks, zones and sheet templates go at v0.7.0. Until then each stays a strict
  xfail. The ledger line "(c)" of the appendix is reached only after v0.7.0; the split of the 63
  IDs by release is recomputed from this table when v0.6.0 is cut.
- ADR 0018 is still superseded at v0.6.0, because the publishing code it governs still leaves
  then.

Effect on the "Numbers":

| Measure                                    | Before            | After                         |
| ------------------------------------------ | ----------------- | ----------------------------- |
| Names gone at v0.6.0                       | 77, plus up to 16 | 35                            |
| Names gone at v0.7.0                       | 0                 | 42, plus up to 16 sheet names |
| Optional extras removed at v0.6.0 / v0.7.0 | 5 / 0             | 3 / 2 (`tables`, `all`)       |

The 35 at v0.6.0 are 4 interchange aliases, 19 publishing, 3 video, 5 pandas, 3 render and 1
stored field. The 42 at v0.7.0 are 10 print, 9 tables, 6 coordinate wording, 14 landmarks and
zones, 1 `proscenium_stage` and 2 stored fields. The total stays 77, plus up to 16 sheet names.
Every name still warns from v0.4.0; the warning period for the v0.7.0 names is three releases
instead of two.

### Correction 2026-11-22: `Spot.claimable` goes at v0.7.0

Format v3 ships at v0.7.0 (ADR 0023, addendum of the same date), and v0.6.0 keeps writing v2.
`Spot.claimable` is a stored field, and the library has no way to drop it before the v3 step, so
its removal moves from v0.6.0 to v0.7.0. It warns from v0.4.0 as before. In the addendum above,
the counts therefore change: 34 names go at v0.6.0 (4 interchange aliases, 19 publishing, 3
video, 5 pandas and 3 render) and 43 at v0.7.0, plus up to 16 sheet names. The v3 step drops
`Show.coordinates`, `Show.zones` and `Spot.claimable` together, so the open item about ADR 0023
is closed.
