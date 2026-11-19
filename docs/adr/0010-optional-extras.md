# ADR 0010: pandas, Pillow, segno and imageio-ffmpeg become optional extras

- Status: Accepted (2026-11-19)
- Date: 2026-11-18
- Deciders: the maintainer (abj360)

## Context

`pyproject.toml` lists nine runtime dependencies. Four serve only a few output functions:

| Dependency     | Used by (library modules)                                                                                                    | Public functions that need it                                                                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| pandas         | `choreo/models.py`, `choreo/paths.py`, `choreo/motion_stats.py`, `export/data.py`, `export/pdf.py`, `export/print_sheets.py` | 6 return a `DataFrame` or `Series` (`Set.table`, `paths_table`, `speeds`, `energy_curve`, `phrase_profile`, `cue_sheet`); `to_csv`, `to_text` and `to_pdf` also accept one |
| Pillow         | `sheet/render.py`, `export/publish.py`, `export/video.py`                                                                    | `render_sheet` PNG and PDF output, `draw_sheet`, `qr_code`, `export_video`                                                                                                 |
| segno          | `export/publish.py`                                                                                                          | `qr_code`                                                                                                                                                                  |
| imageio-ffmpeg | `export/video.py`                                                                                                            | `export_video`                                                                                                                                                             |

Every one of them is imported at module top level. Measured on 2026-11-18, WSL2, Python 3.14.7,
median of 9 fresh interpreters:

| Import                                              | Time   |
| --------------------------------------------------- | ------ |
| `import showpy` (lazy table only)                   | 36 ms  |
| `import showpy.choreo.models`                       | 466 ms |
| numpy, pydantic, shapely and `scipy.optimize` alone | 339 ms |
| pandas, after numpy                                 | 127 ms |
| `from PIL import Image, ImageDraw, ImageFont`       | 13 ms  |
| `import segno`                                      | 19 ms  |
| `import imageio_ffmpeg`                             | 25 ms  |

So every program that touches a `Set` pays 127 ms for pandas, about 27% of the cost of loading
the data model, though only six functions return pandas objects. The audit measured 235 ms of
368 ms on v0.1.1 for the same import.

Installed size, from the Linux x86_64 wheels in this repository's `uv.lock` (`du` of
site-packages):

| Group                                                            | Installed     |
| ---------------------------------------------------------------- | ------------- |
| Core: numpy, scipy, shapely, pydantic, pydantic-core             | 192.6 MiB     |
| imageio-ffmpeg (bundles an ffmpeg binary)                        | 164.8 MiB     |
| pandas, with python-dateutil and tzdata (only pandas needs them) | 58.1 MiB      |
| Pillow                                                           | 20.0 MiB      |
| segno                                                            | 0.5 MiB       |
| **Total today**                                                  | **436.1 MiB** |

The ShowPy wheel itself is 415 KiB and does not change. A consumer that only edits and stores
shows, such as an agent sandbox, downloads and imports 243 MiB it never uses.

Audit decision 8 asks whether these become extras.

## Decision

1. **Core dependencies**: numpy, scipy, shapely, pydantic, pydantic-core. Nothing else is
   installed by `pip install showpy` or `uv add showpy`.
2. **Extras**, each a range like the core dependencies:

   | Extra    | Installs               | Unlocks                                 |
   | -------- | ---------------------- | --------------------------------------- |
   | `tables` | pandas                 | the six DataFrame and Series functions  |
   | `render` | Pillow                 | `render_sheet(fmt="png")`, `draw_sheet` |
   | `qr`     | segno, Pillow          | `qr_code`                               |
   | `video`  | imageio-ffmpeg, Pillow | `export_video`                          |
   | `all`    | every extra above      | everything                              |

   `render_sheet(fmt="svg")`, `to_pdf`, `to_csv`, `to_text`, `export_json` and every editing,
   storage and engine function need no extra. `render_sheet(fmt="pdf")` moves to the library's own
   PDF writer (`export/pdf.py`), which also removes the clock Pillow writes into PDFs (UP-9.4-38).

3. **A missing extra raises `MissingExtraError`**, a new `ShowPyError` with code `MISSING_EXTRA`,
   when the function is called, never at import. Its message names the function and the extra,
   and its hint is the install command for both installers:
   `install the extra: pip install "showpy[tables]" or uv add "showpy[tables]"`.
4. **Imports are lazy.** One helper, `showpy.core.extras.require_extra(module, *, extra)`, imports
   the module on first call and caches it. Type annotations that name pandas or Pillow types sit
   under `TYPE_CHECKING`. `to_csv`, `to_text` and `to_pdf` recognize a DataFrame by checking
   `sys.modules` for pandas first, so they never import it to reject one.
5. **CI proves it.** A `core` job installs the wheel without extras and runs: the README usage
   example, an import of every module under `showpy/` with an assertion that none of pandas,
   PIL, segno or imageio_ffmpeg is in `sys.modules`, and one call per extra-gated function
   expecting `MissingExtraError`. The full test matrix installs `all`.
6. **Import budget**: `import showpy.choreo.models` must not import pandas, checked by a
   subprocess test, and `nox -s bench` records its import time against the baseline.

## Alternatives

- **Keep everything core.** Rejected: 243 MiB and 127 ms per process for six functions, and
  imageio-ffmpeg ships a native binary that security reviews of agent sandboxes must vet.
- **Make pandas lazy but keep it a core dependency.** Rejected as the end state: it fixes import
  time but not install size. It is what a consumer gets by installing `showpy[tables]`.
- **Drop pandas and return plain records.** Rejected for now: DataFrames are the documented
  return type of six functions and of guide chapters. A later phase may add record-returning
  twins; this ADR does not remove a return type.
- **A single `showpy[export]` extra for all four.** Rejected: the video extra alone is 165 MiB;
  a consumer that prints QR codes should not install ffmpeg.
- **Default extras (PEP 771).** Rejected: installer support is not universal, and a default
  extra brings back the size this ADR removes for anyone who does not opt out.

## Consequences

- Breaking for installers: a consumer upgrading to v0.4.0 that calls an extra-gated function
  without declaring the extra gets `MissingExtraError` at the first call. Release notes put the
  four extras and the function table first.
- The Docker image and the CI matrix install `showpy[all]`; the new `core` job keeps the
  boundary honest.
- `deptry` keeps passing: optional dependencies are declared under
  `[project.optional-dependencies]`.

## Numbers

| Measure                                     | Today     | After (core install)                                                      |
| ------------------------------------------- | --------- | ------------------------------------------------------------------------- |
| Runtime dependencies installed by default   | 9         | 5                                                                         |
| Installed size, Linux x86_64                | 436.1 MiB | 192.6 MiB (44%)                                                           |
| `import showpy.choreo.models` (median of 9) | 466 ms    | about 339 ms (pandas removed)                                             |
| Public functions that need an extra         | —         | 10 (6 tables, 2 render, 1 qr, 1 video), plus PNG output of `render_sheet` |

The "after" import time is an estimate: today's time minus the measured pandas import. The task
that lands this ADR measures it.

## What would change our mind

- Most consumers turn out to call the DataFrame functions: then `tables` joins core again and the
  lazy import stays.
- pandas import time drops sharply upstream: then the import half of this ADR loses its point,
  and the size half stands alone.

## References

- Audit `docs/upgrade-plan.md`: section 4.6, section 6 ("Extras"), section 10 decision 8.
- Code: `pyproject.toml` (`dependencies`), the six pandas modules and three Pillow modules above.
- Ledger: UP-9.4-38 (clock in Pillow PDFs), UP-9.5-55 (raw ffmpeg failures).
- Related: ADR 0009 (deprecation), ADR 0011 (Python floor).
