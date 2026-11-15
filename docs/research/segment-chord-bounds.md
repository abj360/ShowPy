# Segment chord bounds for PathEngine motion

Audience: whoever changes `PathEngine.segments` or adds a curve kind or easing. Scope: how far a
time-linear segment can stray from the motion it stands for, and which bounds have a closed form.

Last updated: 2026-11-15

## Question

`PathEngine.segments(tid, tol)` promises (ADR 0002, decision 5) that each segment, linear in
space and in time, stays within `tol` of the true position at every instant. Before this note the
promise rested on probes at fixed eighths of each piece (`showpy/choreo/engine.py`,
`PROBE_SHARES`). Which bound makes the promise hold for every motion, custom periodic ones
included?

## Findings

### 1. The probe grid aliased periodic motions

A 4-cycle `weave` over a full 8-count window has side offset `A·sin(2π·4·u)`. At the probe shares
`u = k/8` the offset is `sin(πk) = 0` for every k (checked numerically, all seven values are 0 to
1e-12). The single segment from start to end passed with a measured error of 0, while the true
error is the full amplitude, 1.0 m (0.689 m at the 25 probes per segment the ledger test uses).
This is ordinary aliasing: a signal sampled at an integer multiple of its own rate looks
constant (Shannon, "Communication in the presence of noise", Proc. IRE 37, 1949). No fixed probe
grid can be safe for every period; either the breaks of the motion are known, or the probes
must not lock to a period.

`speed().distance` summed segment chords, so it reported 4.0 m for a path the engine traces at
16.75 m (`tests/unit/choreo/test_engine.py`, UP-9.2-02). It inherits the segment fix.

### 2. Linear interpolation error is h²/8 · max|p''|

For `p ∈ C²([a, b])` with values in ℝ², the linear interpolant `L` through `p(a)` and `p(b)`
satisfies

    p(t) − L(t) = −∫ G(t, ξ) p''(ξ) dξ,   G ≥ 0,   ∫ G(t, ξ) dξ = (t − a)(b − t) / 2

(the Peano kernel of linear interpolation; de Boor, _A Practical Guide to Splines_, rev. ed.
2001, chapter II, gives the scalar form `‖f − I f‖ ≤ ¼·½·h²·‖f''‖`). Because the kernel is
non-negative, the norm passes inside the integral and the bound holds for vectors too:

    |p(t) − L(t)| ≤ (t − a)(b − t)/2 · max|p''| ≤ h²/8 · max|p''|,   h = b − a.

The bound needs `p'` continuous on the piece. A kink (a polyline corner, an easing break) puts a
delta in `p''`, so every kink must be a segment end. That is why segment ends start from every
break of the curve and the easing.

### 3. Split p'' into a curvature term and an easing term

The engine evaluates `p(τ) = c(s(τ))` (`engine_compile.py`, `plan_positions`). `c` is
parameterized by arc length, so `|c'(s)| = 1` and `|c''(s)| = κ(s)`, the curvature (do Carmo,
_Differential Geometry of Curves and Surfaces_, §1-5). `s(τ)` is the time map: inside one
knot interval it is `s₀ + g·(E(τ) − t₀)` with constant `g`, and `E(τ) = start + W·e(u)`,
`u = (τ − start)/W` (`plan_progress`). By the chain rule

    p'' = c''(s)·s'² + c'(s)·s'',   so   |p''| ≤ κ·s'² + |s''|,
    s' = g·e'(u),   s'' = g·e''(u)/W.

The easing term is unbounded for `ease_in` with strength in (1, 2): `e'' = k(k−1)u^(k−2)` blows
up at `u = 0`. A plain `h²/8·max|p''|` bound then never certifies the first piece. A sharper
split avoids that. Let `s_lin` be the linear interpolant of `s` on the piece. Then

    |p − L| ≤ |c(s) − c(s_lin)| + |c(s_lin) − L| ≤ |s − s_lin| + κ_max·Δs²/8.

The first term uses `|c'| = 1` (c is 1-Lipschitz in arc length). The second applies finding 2 to
`c ∘ s_lin`, which moves at constant speed `Δs/h`, so its second derivative is at most
`κ·(Δs/h)²`; its chord is `L`, since both end on `p(a)` and `p(b)`. The first term is a 1-D
chord error of the easing, scaled by `|g|·W`, and has a closed form for every built-in easing.

### 4. Closed forms per curve kind

Curvature bound `κ_max` on a piece with no break inside:

| Curve kind (`showpy/geo/curves.py`)                                     | `κ_max`                                       | Geometric term `κ_max·Δs²/8`                 |
| ----------------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------- |
| `Segment`, `Polyline` (and sampled custom)                              | 0                                             | 0, exact                                     |
| `ArcCurve`, `CircleCurve`                                               | `1/r`                                         | `Δs²/(8r)`; true sagitta `r(1 − cos(Δs/2r))` |
| `TrimmedCurve`                                                          | that of its base                              | as the base                                  |
| `CompositeCurve`                                                        | max over parts, if all known                  | as the parts                                 |
| `SplineCurve`, `EllipseCurve`, `BezierCurve`, `SvgCurve`, `SpiralCurve` | no cheap closed form (table-based arc length) | probed, see finding 6                        |

`1 − cos x ≤ x²/2` gives `r(1 − cos(Δs/2r)) ≤ Δs²/(8r)`, so the arc bound is never below the
true error. A numerical check over 2,000 random sweeps in (0, π] found a worst ratio of true
chord error to bound of 0.99999991.

The table-based kinds interpolate their native parameter linearly in arc length between 2,048
or more table samples (`curve_base.py`, `_table`), so `|c'|` is 1 only to within the table's
chord error. They also have no curvature formula in the code. The engine treats them as
opaque.

### 5. Closed forms per easing

The easing's 1-D chord error on `[u₀, u₁]` is `C = max |e(u) − ℓ(u)|`, with `ℓ` the chord of `e`.
On a piece where `e` is convex or concave and smooth, the maximum sits where `e'(u*) = m`, the
chord slope (mean value theorem).

| Easing (`showpy/choreo/motion.py`, `ease`) | Pieces (breaks)                               | `C` on one piece                                                          |
| ------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------- |
| `linear`                                   | none                                          | 0                                                                         |
| `hold_then_burst`, `burst_then_settle`     | `1 − 1/k` or `1/k`                            | 0 (affine pieces)                                                         |
| `custom`                                   | every point's time                            | 0 (piecewise linear)                                                      |
| `ease_in` `u^k`                            | none; convex                                  | `ℓ(u*) − u*^k`, `u* = (m/k)^(1/(k−1))` clipped to the piece; 0 when k = 1 |
| `ease_out` `1 − (1−u)^k`                   | none; concave                                 | as `ease_in` on the mirrored piece `[1 − u₁, 1 − u₀]`                     |
| `ease_in_out`                              | `0.5`                                         | half the `ease_in` error on `[2u₀, 2u₁]`, mirrored on the second half     |
| `steps` (n smoothsteps)                    | `i/n`; inflection at `(i + ½)/n`, split there | `abs(S(x*) − ℓ(x*))/n`, `S = 3x² − 2x³`, `x* = (1 ± √(1 − 2m/3))/2`       |

`k = max(strength, 1)` and `n = max(round(k), 1)`, as in `ease`. The smoothstep inflection is
not an easing break (`easing_breaks` does not return it), so the engine adds it to the segment
ends itself. A numerical check against a 200,001-point brute force found a worst difference of
1.1e-11 for `u^k` (3,000 random pieces, k in [1, 4]) and 2.5e-12 for the smoothstep halves.

### 6. Opaque pieces need probes that cannot lock to a period

For an opaque curve there is no proof, only evidence. Two things keep the evidence honest:

- **Breaks first.** Spline knots, SVG piece ends and composite joins are curve breaks
  (`_breaks()`), so they become segment ends. Inside a piece the curve is one cubic or one
  native piece, which cannot hide a period between probes the way a whole sampled motion can.
- **Jittered probes.** Nine stratified shares, `(i + U_i)/9` with `U_i` uniform in [0, 1), drawn
  from `seeded_rng` keyed on the spot and the transition. A periodic error cannot vanish at
  every jittered probe the way it vanished at every fixed eighth, and the same show still
  yields the same segments (determinism rule 3). The `tol / 2` acceptance margin of ADR 0002's
  first addendum stays for these pieces.

## What the engine does with this

1. Segment ends start from every window edge, time knot, easing break, smoothstep inflection and
   curve break mapped through the inverse time map (arc length to eased time by the knots, then
   eased time to count by bisecting the monotone easing). A custom motion's sample vertices
   are not breaks: they sample a function, they are not corners a designer drew.
2. A piece with a known `κ_max` is accepted when `|g|·W·C + κ_max·Δs²/8 + corner gap ≤ tol`.
   This is a proof, not a probe. STRAIGHT with linear easing gives one piece and two stations.
3. On a polyline the corner gap is exact. The constant-speed walk `c ∘ s_lin` and the chord
   `L` are both piecewise linear in time, so their difference peaks on a corner strictly
   inside the piece. The corner at arc length `σ` sits at chord share `(σ − s₀)/(s₁ − s₀)`.
   That lets a custom motion merge sample vertices into long segments without losing the proof.
4. An opaque piece is accepted when its jittered-probe error is within `tol / 2`.
5. A piece is halved until it passes. At depth 20 the engine raises `PathError` code
   `SEGMENT_BOUND` instead of accepting a segment it cannot certify.

## Limitations

- Custom motions are already a 257-point polyline when the engine sees them (`CUSTOM_SAMPLES`).
  The bound is exact against that polyline, not against the motion function. A motion with
  more than about 64 cycles is aliased at compile time, before segments exist.
- Opaque curves get evidence, not proof. A feature narrower than one probe spacing that also
  sits between curve breaks can still slip through; the `tol / 2` margin is the only guard.
- Measured on the tier M bench show (100 spots, 20 sets): 34,031 segments where the probe grid
  made 45,280, because sampled custom motions no longer need one segment per sample.

## Sources

- `showpy/choreo/engine.py`, `showpy/choreo/engine_compile.py`, `showpy/choreo/motion.py`,
  `showpy/geo/curves.py`, `showpy/geo/curve_base.py` at commit `10a8b06`.
- C. de Boor, _A Practical Guide to Splines_, revised edition, Springer, 2001, chapter II
  (error of broken-line interpolation).
- M. P. do Carmo, _Differential Geometry of Curves and Surfaces_, Prentice-Hall, 1976, §1-5
  (curvature of an arc-length parameterized curve).
- C. E. Shannon, "Communication in the presence of noise", Proc. IRE 37(1), 1949 (sampling).
- Numerical checks: the brute-force comparisons above, run 2026-11-15 with numpy on this
  repository's locked environment.
