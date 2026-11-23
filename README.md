# e8Show

e8Show (import name `e8s`) is a Python choreography library. With it you draw spots on a 2D sheet, build a show set by set from stored shapes, move every performer between sets with one exact motion engine, and publish the result so performers can claim their spot.

## Why

Drill design is slow because every dot, every move and every check is manual. e8Show makes the mechanics exact and repeatable, so the designer only makes the creative choices.

- **One source of motion.** The canvas, the checks, the exports and the video all read positions from one `PathEngine`, so they never disagree. Its collision segments stay within 0.02 m of the true path. A Hypothesis property test probes 9 points per segment across 150 random paths to check this.
- **Exact and deterministic.** Arc-length curves are exact for lines and arcs. Tempo ramps convert analytically, both ways. Randomness is seeded, and no library code reads the clock.
- **Verified.** About 1,500 tests (unit, property, golden replay and doctests) pass with warnings as errors, under `mypy --strict`, pyright and ruff. Hundreds more are strict-xfail tests that pin open audited defects; the plugin prints the exact count (`Ledger: 528 open` on 2026-11-13).
- **Light to import.** `import e8s` takes 2.5 ms; names load lazily.

## Features

- **Sheets:**
  - NFHS, NCAA and NFL fields, a stage and a gym floor, or a blank sheet.
  - Add zones, landmarks and guides.
  - Coordinate wording that never moves stored positions.
  - Replace the sheet with landmark-fitted re-mapping.
  - Render to SVG, PNG or PDF.
- **Cast:**
  - Groups, spots (circle or rectangle footprints) and labeling.
  - A selection language: `T1-T8 + group:guard - tag:soloist`.
- **Geometry:**
  - 11 curve kinds, including splines, Béziers, SVG paths and spirals, plus text in a stroke font.
  - Regions, even spacing, Poisson-disk scatter, exact affine transforms, snapping and measurement.
- **Forms:**
  - Lines, arcs, blocks, wedges, fills, scatters, custom forms, and generated shapes that stay editable.
  - Form fitting from loose dots.
  - Optimal matching: bottleneck-then-sum, order-preserving, with crossing and backward penalties.
- **Motion:**
  - Path styles: straight, polyline, curve, hold, rotate, follow-the-leader and custom motions.
  - Easing, staggers and ripples.
  - Facing modes and timed overrides.
  - Advisory gap finding with delay, waypoint and reassignment fixes.
- **Time:** count timelines with tempo ramps, holds, measures, markers, cues and audio sync.
- **Show:**
  - Set management and a show index.
  - Validation, versioned storage (in memory or files) and a workspace file view.
  - History with diffs and revert, pins, proposals and canvas events.
- **Export:**
  - Publishing with performer claims and QR codes.
  - Coordinate sheets, charts and cue sheets.
  - A dependency-free PDF writer, JSON and CSV, and 2D MP4 video with audio.

## Quickstart (one command, fully dockerized)

```bash
# No ports: e8s is a library. The container installs the wheel and runs the full test suite.
cp .env.example .env && docker compose -f docker/docker-compose.yml up --build
```

To run locally:

```bash
uv sync && uv run pytest
```

## Usage

```python
import e8s
from e8s.examples import two_set_show

show = two_set_show()  # 4 trumpets, 8 counts, 120 cpm
spec = e8s.stagger(show.transition(("1", "2")), ["T1", "T2", "T3", "T4"], step=1.0)
show = show.model_copy(update={"transitions": (spec,)})

engine = e8s.PathEngine(show)
engine.position("T4", 6.0)  # array([6. , 7.4]): T4 starts 3 counts late
engine.speed("T4", ("1", "2")).m_per_s  # 1.6
e8s.min_gaps(show, ("1", "2"), top=1)[0].gap  # 1.4 m between footprints
e8s.validate_show(show).ok  # True
```

`pip install e8s` (or `uv add e8s`) installs the core: numpy, scipy, shapely and pydantic. Four extras add the dependencies of a few output functions; without one, those functions raise `MissingExtraError` with the install command.

| Extra    | Installs               | Unlocks                                                                             |
| -------- | ---------------------- | ----------------------------------------------------------------------------------- |
| `tables` | pandas                 | `Set.table`, `paths_table`, `speeds`, `energy_curve`, `phrase_profile`, `cue_sheet` |
| `render` | Pillow                 | `render_sheet(fmt="png")` and `fmt="pdf"`, `draw_sheet`, `render_frame`             |
| `qr`     | segno, Pillow          | `qr_code`                                                                           |
| `video`  | imageio-ffmpeg, Pillow | `export_video`                                                                      |
| `all`    | every extra above      | everything                                                                          |

Every public function has a runnable `>>>` example in its docstring. The full API is in `docs/e8s-api-reference.md`.

## Architecture

```
            core  (units, ids, errors, selection, validation, types)
              |
      +-------+--------+
      |                |
     geo              time        curves, spacing, affine | tempo, holds, measures
      |                |
  +---+-----+-----+----+
  |         |     |
 cast     sheet  notes            spots, selection | templates, coords | notes
  |         |     |
  +----+----+-----+
       |
     choreo                        forms, match, paths, PathEngine, facing, avoid
       |
      show                         manage, storage, history, collab, validate
       |
     export                        publish, print, PDF, data, video
```

Lower layers never import higher ones. A consumer such as a checker reads shows through storage and the same `PathEngine`.

## Config

| Setting               | Where                      | Default        | Meaning                                           |
| --------------------- | -------------------------- | -------------- | ------------------------------------------------- |
| `E8S_UPDATE_GOLDEN`   | env, tests                 | unset          | `1` rewrites golden fixtures for human review     |
| `E8S_UPDATE_SCHEMA`   | env, tests                 | unset          | `1` regenerates `e8s/schemas/showpy.show.v1.json` |
| `curve_tol`           | `PathEngine(...)`          | 0.01 m         | Chord error for drawn paths                       |
| `tol`                 | `PathEngine.segments(...)` | 0.02 m         | Segment error for collision checks                |
| `/config/limits.yaml` | workspace                  | designer-owned | Checker limits; agents cannot write it            |

## Structure

```
e8s/
  core/     units, ids, errors, types, selection, validation, util
  geo/      curves, shapes, svg, text, regions, spacing, affine, transforms, measure, analysis
  time/     models, timeline, clock, measures, validate
  cast/     models, spots, select, rewrite
  sheet/    models, templates, surfaces, coords, bounds, replace, render
  notes/    models, notes
  choreo/   models, forms*, match*, paths, routes, motion, engine*, facing, avoid, motion_stats
  show/     models, manage, sections, storage, repositories, history, collab, validate
  export/   models, publish, print_sheets, pdf, data, positions, video
  examples/ sheets, shows, publishing (deterministic example shows)
e8s/schemas/ showpy.show.v1.json
tests/      unit/ property/ golden/ fixtures/
docs/       API reference, ADRs, handoff notes
docker/     Dockerfile, docker-compose.yml
```

## Development

```bash
export PATH=~/.nvm/versions/node/v22.23.5/bin:$PATH   # Node's bin/ first; no system node
npm ci                      # pinned Prettier, used by the lint gate
uv sync
uv run pre-commit install
uv run ruff format . && uv run ruff check .
uv run nox -s types
uv run pytest
```

Read `CLAUDE.md`, `CONTRIBUTING.md` and the ADRs before changing anything. The PathEngine, units and the locked checker have rules of their own.

## Testing / CI

| Gate            | Command                                                                                            | CI  |
| --------------- | -------------------------------------------------------------------------------------------------- | --- |
| Format, lint    | `uv run ruff format --check . && uv run ruff check .`, then Prettier for Markdown, YAML and JSON   | yes |
| Types           | `uv run nox -s types` (mypy `--strict`, then pyright basic)                                        | yes |
| Tests           | `uv run pytest` (unit, property, golden, doctests; warnings are errors)                            | yes |
| Golden          | `uv run pytest -m golden`                                                                          | yes |
| Dependency scan | `pip-audit --strict --require-hashes` on the exported lockfile                                     | yes |
| Hash seeds      | `uv run nox -s hashseeds` (full suite under seeds 1 and 2)                                         | yes |
| Slow            | `uv run nox -s slow` (generated-show properties, large golden, two seeds)                          | yes |
| Wheel           | `uv run nox -s wheel` (build, install with `[all]` in a clean venv, run the Usage example)         | yes |
| Core install    | `uv run nox -s core` (wheel without extras: imports, Usage example, every gated call fails closed) | yes |
| Lowest          | `uv sync --resolution lowest-direct`, then `uv run --no-sync nox -s tests`                         | yes |
| Docker          | `docker compose -f docker/docker-compose.yml up --build --exit-code-from e8s`                      | yes |

## Docs

- `docs/upgrade-plan.md`: the upgrade program, phase by phase
- `docs/e8s-api-reference.md`: the full API reference
- `docs/adr/`: architecture decisions (layout and reference fixes; the PathEngine)
- `docs/HANDOFF.md`: current state and next steps
- The study guide lives in the public repository <https://github.com/abj360/ShowPy-guide>
- `CONTRIBUTING.md`: workflow and standards

## License

- From the licence change on, e8Show is licensed under the PolyForm Noncommercial License 1.0.0
  (`LICENSE`).
- Versions v0.1.0 to v0.3.0, and every commit before the licence change, were released under the
  MIT License and stay under it.
- The bundled fonts keep their own licences: the SIL Open Font License 1.1, the Hershey font
  notice and, for `simplex`, MIT. Their texts are in `e8s/fonts/licenses/`. The non-commercial
  terms do not cover the font files.
- The JSON schemas in `e8s/schemas/` stay under the MIT License
  (`e8s/schemas/LICENSE-MIT.txt`), so any tool may read and validate show files.
- For a commercial licence, contact the maintainer through <https://github.com/abj360>.

Maintained by abj360.
