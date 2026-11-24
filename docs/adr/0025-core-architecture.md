# ADR 0025: Core architecture

- Status: Accepted (2026-11-24)
- Date: 2026-11-24
- Deciders: the maintainer (abj360)

## Context

Phase 4 moves the computation of the six packages below `show` (`core`, `geo`, `time`,
`sheet`, `cast`, `choreo`) into one Rust core behind unchanged Python names. A wheel ships the
core to Python; a WebAssembly build ships the same core to the browser later (Phase 6). `Show`
stays a pydantic model until Phase 6, and Phase 3 owns `e8s/show/` while Phase 4 runs, so the
engine must read a pydantic `Show` and must not change how `e8s/show/` reaches the packages
below it.

The transition plan's decisions D12, D13, D17 and D36 left these open: the crates and the
bindings, the float rules, how the engine reads a `Show`, and how far the core may sit from
today's numpy results. A local trial answered each with numbers (Apple M4, 10 cores, macOS
26.6.2; rustc 1.100.0; PyO3 0.30.1 with `abi3-py313`; the `numpy` crate 0.30.0; `libm` 0.2.16;
maturin 1.16.0; base `be672db`, `v0.4.0-24`, where the clock, the engine and the ported curves
are unchanged since `v0.4.0`). The trial ported `count_to_seconds` line for line, and a
positions engine for straight, polyline, arc and hold paths. Its numbers are under "Numbers".

The maintainer settled the trial's open questions on 2026-11-24: crate names, source layout,
the `numpy` crate, which numpy behaviours to copy, explicit FMA, panics, versioning, when the
wheel matrix runs, the toolchain pin, the show hand-off, and no deprecation shims. This record
states those decisions and the ones the trial left to this ADR: how parity is measured when the
golden fixtures are rounded, licence notices, the wasm bindings, and compiled-value caching.

## Decision

1. **Workspace.** A Cargo workspace at the repository root, `resolver = "3"`, edition 2024:
   - `crates/e8s-core`: the engine. Pure Rust, no Python or JavaScript dependency,
     `#![forbid(unsafe_code)]`. One Rust module per package, in the same layers: `base` (the
     `core` package; Rust reserves `core` for its own crate), `geo`, `time`, `sheet`, `cast`,
     `choreo`, plus `assign` (assignment solvers) and `rng` (the seeded generator).
   - `crates/e8s-py`: PyO3 bindings built into the extension module `e8s._core`, with one
     submodule per area: `e8s._core.core`, `.geo`, `.time`, `.sheet`, `.cast`, `.engine` and
     `.forms`. Typing stubs live in `e8s/_core/*.pyi`, one file per submodule, checked against
     the extension by `mypy.stubtest`.
   - `crates/e8s-wasm`: wasm-bindgen bindings. No hand-written exports and no `unsafe` in our
     code; the generated glue holds it. Phase 4 exports only what the parity checks need.
   - Crate names are never published (`publish = false`).
2. **Python source layout.** `e8s/` stays at the repository root. maturin is the PEP 517 build
   backend (`python-source = "."`, `module-name = "e8s._core"`) and replaces hatchling and
   hatch-vcs. The sdist still builds from source and needs a Rust toolchain.
3. **Toolchain.** `rust-toolchain.toml` pins `1.100.0` with the `wasm32-unknown-unknown`
   target. `rust-version` equals the pin. A toolchain bump is its own commit and reruns the
   parity harness on every CI cell.
4. **Version.** `[workspace.package] version` in `Cargo.toml` is the single source. maturin
   writes it into the wheel metadata in its PEP 440 form; `e8s.__version__` keeps reading
   `importlib.metadata`. The release commit bumps it, and the tag job refuses a tag `vX.Y.Z`
   that differs from it. The Docker build no longer needs a pretended version.
5. **Arrays across the boundary.** One call per frame or per batch, never one per spot. Arrays
   go out as numpy arrays that own the Rust buffer (zero-copy) and come in as read-only numpy
   borrows, through the `numpy` crate (BSD-2-Clause). Python lists are not part of any hot
   path. `pythonize` and `serde_json` are not dependencies of `e8s-py`.
6. **How the engine reads a `Show` before Phase 6 (D12).**
   - The binding walks pydantic objects by attribute reads; never JSON.
   - It caches one core value per frozen part (`Set`, `TransitionSpec`, `Timeline`, `Cast`,
     `Sheet`, `Form` and each curve), keyed by `id()` and checked through a weak reference that
     evicts the entry when the part is collected. A show's core value is assembled from
     `Arc`-shared parts, so an edit that keeps 59 of 60 sets converts one.
   - **Compiled values are cached beside their part:** the clock beside its `Timeline`, plans
     beside their `TransitionSpec`, arc-length tables beside their curve. A ported function is
     never slower than the Python it replaces; the parity harness times both and fails on a
     slower port. (The trial's `count_to_seconds` compiled the timeline on every call and took
     1.98 µs against Python's cached 0.78 µs.)
   - The data models stay pydantic, in their packages, until Phase 6 moves `Show` and its
     models into the core. Nothing in `e8s` mutates a frozen part (ADR 0026 already bans
     `model_copy(update=)` inside `e8s`); a library rule test also bans `object.__setattr__`.
7. **No deprecation shims.** There are no users, so a Python module whose work moved to the
   core is deleted once its package passes its parity run on `main`, with no alias and no
   warning. The public names keep working because `e8s/_api_table.py` points them at
   `e8s._core`. A public name that cannot survive the port (a shapely conversion, for one) is
   removed outright, or kept behind an optional extra, and named in the commit and the
   release notes.
8. **Float rules (D17).**
   - Every transcendental comes from `libm`, pinned exactly (`=0.2.16`).
   - No fused multiply-add from contraction. Rust does not contract; CI disassembles the
     extension on the arm64 cells and fails on any FMA instruction outside `libm::fma`.
   - An explicit `libm::fma` is allowed only where its result is identical on every platform:
     a correctly rounded error-free transform, as in CPython's `math.dist`.
   - Operations run in a fixed order. No parallel reductions in results. No quantization.
   - The core copies numpy's deterministic choices that tests pin: an exponent of exactly 2 is
     an exact square (`u ** 2.0` is `u * u`), and `np.interp` keeps its formula and edge rules.
     It does not copy platform quirks: numpy's fused `np.interp` on arm64 is not reproduced.
   - NaN is rejected at the boundary with `SchemaError`; the core never canonicalizes it.
9. **Errors and panics.** Core functions return `Result` with an error code from the catalogue
   in `e8s/core/error_codes.py`; a generator writes the Rust enum from it. The binding raises
   the e8s exception class for that code, with the same `code`, `message`, `context` and
   `hint`. `panic = "unwind"`: every binding entry point catches an unwinding panic and raises
   `E8sError` with code `CORE_PANIC`, never `PanicException`, and never aborts the process.
   Clippy denies `unwrap_used`, `expect_used`, `panic`, `todo` and `unimplemented` in our
   crates. Every parser has a `cargo-fuzz` target; the nightly run must find no panic and no
   exception outside the `E8sError` family.
10. **Parity and the golden fixtures (D36).**
    - **The oracle is v0.4.0 itself.** The parity harness builds the `v0.4.0` wheel from its tag
      into its own virtual environment, runs a fixed case list there with numpy, scipy and
      shapely, and writes every float as raw float64 bytes. The core runs the same cases in the
      working tree. The comparison is direct: no rounded fixture stands between the two.
    - Gates: positions within 1e-9 m; every count, order, ID and error code identical;
      `content_hash` values and encoded documents byte-identical. The report also counts
      bit-identical values and the ulp histogram of the rest.
    - **Fixtures do not need exact floats.** The committed goldens round to 9 decimals, so they
      cannot measure the 1e-9 m gate (the large golden stores `t0` and `t1` rounded, which
      alone moves a point by 1.38e-8 m). They stay as they are and keep their own tolerances;
      the harness measures the gate. A golden is refreshed only if it fails against the core,
      once, human-reviewed, with the harness report for the same case attached.
    - **Cross-platform identity.** The core's outputs (sampled arrays, documents, hashes) are
      byte-identical across the 6 CI cells (Linux, macOS and Windows on Python 3.13 and 3.14)
      and 3 hash seeds, and between the native and the wasm builds on the goldens.
11. **Crates and licences.**
    - Keep list (confirmed on crates.io on 2026-11-23): `geo` and `i_overlay` for regions,
      `rstar`, `libm`, `sha2`, `serde_json_canonicalizer`, `spade`; `kurbo` as a reference only.
      Avoid: `clipper2`, `geo-clipper` (C++ under BSL-1.0, no wasm build), `cp_sat` (C++),
      `minicbor` and `poisson-diskus` (BlueOak-1.0.0), `serde_cbor` (unmaintained).
    - We write our own Jonker-Volgenant assignment solver (Crouse's rectangular variant), with
      scipy's choice among equal-cost optima, and our own sparse solver for mirror pairs. If
      either is translated from scipy's source rather than from the papers, scipy's BSD-3-Clause
      notice ships with it.
    - `cargo deny` allows MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Zlib, Unicode-3.0 and
      Apache-2.0 WITH LLVM-exception, bans the avoid list and unmaintained crates, and exempts
      our own unpublished crates.
    - **Notices ship in the wheel.** `cargo about` generates
      `e8s/licenses/THIRD_PARTY_RUST.txt` from the crates linked into `e8s._core`;
      `license-files` lists it, and `License-Expression` adds the licences it names. A test
      regenerates it and fails when it is stale.
12. **CI.**
    - A `rust` job on every pull request that touches Rust: `cargo fmt`, clippy, `cargo test`,
      `cargo deny`, the FMA check and the wasm build.
    - A wheel matrix: manylinux_2_28 and musllinux_1_2 for x86_64 and aarch64, Windows x64 and
      arm64, macOS arm64 and x86_64, and the sdist, built by `maturin-action` on native runners.
      It runs on pull requests that touch `crates/`, `Cargo.*`, `rust-toolchain.toml`,
      `pyproject.toml` or its own workflow, and on every release tag. Each wheel runs the ported
      packages' tests on 3.13 and 3.14. The Intel macOS job is dropped when GitHub retires its
      runners.
    - A nightly core workflow: the fuzz targets, and mutation testing for Rust (`cargo-mutants`)
      and Python (`mutmut`), whose scores are recorded, not gated, in Phase 4.
13. **Custom callables wait for ADR 0027.** Custom motions, form functions and `cost_fn` are
    Python functions today (D13). ADR 0027 decides how a compiled core receives them. It is a
    separate gate: it must be accepted before the engine lane starts, and before any forms or
    matching task that touches a callable.

## Alternatives

- **Hand a `Show` over as JSON on every call** (`model_dump_json` then serde, or `to_json` then
  `pythonize`). Rejected: 108 to 125 ms per call and a 45 MB transient peak.
- **Convert the whole show once, keyed by the `Show` object.** Rejected: every edit makes a new
  `Show`, so each engine call after an edit costs 23.7 ms. The per-part cache costs 0.17 ms.
- **Move the `Show` data type into the core now.** Rejected for Phase 4: it saves 41 MB per show
  and all conversion, but pulls the model, its edits and its validation forward from Phase 6,
  into code Phase 3 owns, and Python reads get 1.4 times slower (1.10 against 0.79 ms over 18,000
  points).
- **Compile per call, cache nothing.** Rejected: a small ported function becomes slower than the
  Python it replaces (`count_to_seconds`, 1.98 against 0.78 µs).
- **Quantize to 1e-9 for cross-platform agreement.** Rejected by D17: identical arithmetic gives
  identical bits. Native and wasm agree on 8,439,540 of 8,439,540 values.
- **Mirror numpy's fused `np.interp` on arm64.** Rejected: platform-specific. x86 numpy does not
  fuse, so copying it would tie the core to numpy's build.
- **Ban FMA entirely.** Rejected: CPython's `math.dist` uses a correctly rounded `fma`, which
  gives the same bits everywhere and in wasm; banning it would make `math.dist` parity
  impossible for no determinism gain.
- **`panic = "abort"`.** Rejected: a bug in the core would kill the host interpreter, and with
  it a server holding other users' work.
- **Store `repr` floats in the golden fixtures.** Rejected: it doubles the fixtures for a gate
  the live comparison already measures exactly, and a fixture refresh would then hide a moved
  float behind a reviewed diff of thousands of lines.
- **Compare against today's Python in the same process.** Rejected: the Python modules are
  deleted as each package passes, and two `e8s` packages cannot share one interpreter.
- **Keep tag-driven versions (hatch-vcs) through a release script that writes `Cargo.toml`.**
  Rejected: two sources of one number; the maintainer chose `Cargo.toml`.
- **Move `e8s/` to `python/e8s/`** as the spec draws. Rejected: every tool path changes, and the
  trial built the wheel with no file moved.
- **Our own buffer-protocol wrapper instead of the `numpy` crate.** Rejected: more code we own;
  the crate is BSD-2-Clause and zero-copy.
- **Hand-written wasm exports.** Rejected: they need `unsafe` around raw pointers.
- **Return Python lists.** Rejected: 31 times slower than a numpy array at 300 spots, and 94
  times once the caller wants an array.
- **mypyc for the interim.** Already rejected by D11.

## Consequences

- Building from source needs a Rust toolchain. Every developer machine and CI job that runs
  `uv sync` compiles the extension; `tool.uv.cache-keys` lists the Rust sources so a changed
  crate rebuilds it.
- Between releases, builds of `main` report the version of the last release; only the release
  commit moves it.
- Each live `Show` version costs about 16.5 MB of core memory on top of its pydantic objects,
  shared part by part with other versions.
- Results move from v0.4.0 in the last bits: in the trial, 1.9% of positions and 0.9% of clock
  values with ramps, never by more than 8.9e-14 m or 3 ulp. The committed goldens are expected
  to pass unchanged; the harness proves the gate.
- `sample` stops being a budget problem: 69,900 frames/s from Python, against 77.8 today and a
  budget of 300.
- Public names whose values are third-party objects (`Region.to_shapely`, `Region.from_shapely`,
  `CurveBase.to_shapely`) cannot survive the removal of shapely: they leave with it, or move
  behind an optional extra.
- The wheel matrix costs about 29 billed minutes per run (20 for Linux and Windows): free on the
  public repository, about $0.65 at private rates.
- The `License-Expression` of the wheel grows by the licences of the linked Rust crates.

## Numbers

From the trial (`phase-4-trial-report.md`, 2026-11-23). Show under test: `seeded_show(11, 300,
60)`, 60 sets, 300 spots, 16,199 path rows, 652 counts.

| Measure                                               | Value                                                                                             |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Wheel, macOS arm64, `cp313-abi3`                      | 977,727 bytes (`_core.abi3.so` 939,312); imports on 3.13.16 and 3.14.8                            |
| Build, cold / warm after one core file                | 12.8 s / 8.8 s; sdist 459,514 bytes, wheel from it in 12.9 s                                      |
| Wheel matrix, estimated billed minutes, cold / cached | 29 / 22 (Linux and Windows 20 / 14); about 5 minutes wall                                         |
| `count_to_seconds`, native vs wasm, 10,000 timelines  | 240,060 of 240,060 bit-identical                                                                  |
| `count_to_seconds`, core vs CPython                   | 2,185 differ (1 ulp 1,858; 2 ulp 319; 3 ulp 8), all with ramps; 0 with the platform libm          |
| Engine positions, native vs wasm                      | 8,199,480 of 8,199,480 bit-identical (large golden and 300 x 60)                                  |
| Engine positions, core vs numpy, opener               | 5,136 of 5,136 bit-identical                                                                      |
| Engine positions, core vs numpy, large golden         | 1,285,192 of 1,309,770 bit-identical (98.12%); max 8.9e-14 m                                      |
| Causes of the differences                             | `libm` `sin`, `cos`, `atan2` about 20,200; arm64 fused `np.interp` about 4,100; `pow(u, 2.0)` 270 |
| Core vs the 9-decimal fixtures                        | opener 4.97e-10 m; large `segments.json` 1.38e-8 m (Python itself 1.38e-8 m)                      |
| Show hand-off, per call                               | attribute walk 25.3 ms; dump and serde 107.9 ms; `to_json` and `pythonize` 124.5 ms               |
| Show hand-off, cached                                 | hit 85 ns; one-spot edit 0.17 ms per part, 23.7 ms whole show                                     |
| Memory                                                | pydantic `Show` 57.9 MB; core value 16.5 MB                                                       |
| Boundary                                              | 10.1 ns a call; (300, 2) numpy out 0.144 µs zero-copy, 0.190 µs copied, list 4.49 µs              |
| Boundary inbound                                      | numpy borrow of 600 floats 0.384 µs; list 2.80 µs                                                 |
| `count_to_seconds` per call                           | Python (clock cached) 0.78 µs; trial binding (compiles every call) 1.98 µs                        |
| `sample`, 300 x 60                                    | core from Python 69,900 frames/s; inside Rust 98,900; wasm about 100,000; today 77.8; budget 300  |
| Compile everything, 300 x 60                          | core 4.9 ms plus 25 ms first conversion; today 1.49 s                                             |

The engine port covered straight, polyline, arc and hold paths (69% of the show's plans) and
no facing; the rest ran a straight stand-in. Even ten times slower, `sample` would clear its
budget twenty times over.

## What would change our mind

- A part-cache miss over 5 ms on a common edit path: then move `Show` into the core early.
- A `libm` release, a toolchain bump or a target where native and wasm disagree on any golden
  value: then vendor the functions we use.
- A parity run that shows a count, order or ID change rather than a sub-nanometre shift: then
  fix the discrete decision in the library first; never loosen the gate.
- abi3 wheels failing on a supported platform, or the `numpy` crate failing to follow a numpy
  ABI change: then per-version wheels, or our own buffer-protocol wrapper.
- A v0.4.0 test that pins a third-party artifact (a GEOS vertex order, a scipy tie choice) the
  core cannot reproduce: then the maintainer decides between reproducing it and a reviewed
  change to that test; the port never edits it silently.

## References

- `phase-4-trial-report.md` and `phase-4-trial/` (sources, scripts, results), in the
  maintainer's agent notes.
- Transition plan, section 4.3, and decisions D11, D12, D13, D17 and D36.
- `algorithms-and-evaluation.md`, sections 1.8, 1.11, 1.15, 1.23 and 3.1.
- e8Show 1.0 spec, sections 6.1 and 19.3.
- ADR 0014 (canonical hashing), ADR 0015 (repository layout v2, Phase 3), ADR 0020 (rename),
  ADR 0026 (public model interface); ADR 0027 (custom callables, to be written).
- Plan: `docs/superpowers/plans/2026-11-24-phase-4-engine-port.md`.
