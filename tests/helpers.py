#!/usr/bin/env python3

"""
helpers.py --- small builders shared by many test modules

Contains:
    make_set(): builds set "1" from (entity, x, y) rows, with some spots off the field
    make_facing_set(): builds set "1" from (entity, x, y, facing) rows, with off and pinned spots
    pin_spot(): pins one spot's placement in one set of a show
    replaced_form_show(): places form a on set "2", then re-places the set with form b only
    with_curve_rows(): gives the spots curved path rows through (1, 7) in transition 1 -> 2
    open_session(): saves a show to a fresh repository and opens two independent workspaces on it
    bisector_rotation(): returns a pivot and sweep whose arc carries one point onto another
    defect(): marks a test as an open audited defect, a strict xfail keyed on its ID
    capped_address_space(): caps the address space so a runaway allocation fails fast
    fails_after(): fails a test still running after a deadline, never inside coverage or a hook
    only_e8s_errors(): runs a fuzz target; only an E8sError may escape, within 5 s
    context_entries(): returns the list of JSON objects under one key of an error's context
    json_doc(): returns a model's to_json() dict, typed for indexing into nested values
    HOSTILE_JSON: names JSON bytes that make json.loads raise RecursionError or ValueError
    EXPECTED_FAILURES: exception names a hash-seed subprocess may end with
    outputs_under_hash_seeds(): runs one script under each hash seed and collects its output
    TAGGED_SHOW_SCRIPT: script head building two_set_show() with spot tags and a selection
    digests_under_hash_seeds(): hashes one output of the tagged show twice per hash seed
"""

import functools
import gc
import inspect
import math
import signal
import subprocess
import sys
import traceback
import warnings
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any, ParamSpec, TypeVar, cast

import pytest

from e8s.choreo.form_place import place
from e8s.choreo.forms import line_form
from e8s.choreo.match_ops import with_set
from e8s.choreo.models import Placement, PlacementPatch, Set
from e8s.choreo.path_models import PathRow, PathStyle, TransitionSpec, Waypoint
from e8s.core.errors import E8sError, bounded_repr
from e8s.core.ids import SetId, SpotId, make_tid
from e8s.core.json_types import JsonValue
from e8s.core.types import FrozenModel, Point
from e8s.examples import two_set_show
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.show_io import save_show
from e8s.show.workspace import Workspace, open_workspace

SET_ID = "1"
FIRST_TRANSITION = ("1", "2")
CURVE_COUNTS = 8.0
CURVE_WAYPOINT = Waypoint(x=1, y=7)
SHOW_ID = "s"
STILL_TOL_M = 1e-6
FULL_TURN = 360.0
HALF_TURN = 180.0
ALLOCATION_HEADROOM_BYTES = 256 * 2**20
FUZZ_BUDGET_S = 5.0
JSON_NESTING_DEPTH = 200_000
OVERLONG_INT_DIGITS = 5_000
# json.loads overflows the C stack (RecursionError) and refuses ints past
# sys.int_max_str_digits, 4300 by default (ValueError).
HOSTILE_JSON = {"deep": b"[" * JSON_NESTING_DEPTH, "huge-int": b"9" * OVERLONG_INT_DIGITS}
# Code running here is a tracer, finalizer or error hook: an exception raised in it is lost
# or corrupts the hook, so a hang alarm waits for the next tick instead.
NO_ALARM_MODULES = frozenset(
    {"coverage", "weakref", "_weakrefset", "_pytest.unraisableexception", "_pytest.threadexception"}
)

FINALIZER_NAME = "__del__"
HANG_RETRY_S = 0.05
EXPECTED_FAILURES = ("ShowValidationError", "SchemaError", "E8sError")
HASH_SEEDS = ("1", "2", "3")
SUBPROCESS_TIMEOUT_S = 20
STDERR_CHECK_TAIL = 300
STDERR_KEPT_TAIL = 200
TWO_HASH_SEEDS = ("1", "2")
TAGGED_SHOW_SCRIPT = (
    "import hashlib, json\n"
    "from e8s.cast.models import Cast, Spot\n"
    "from e8s.cast.spots import define_selection\n"
    "from e8s.examples import two_set_show\n"
    "from e8s.show.models import Show\n"
    "tags = ['alpha', 'beta', 'gamma', 'delta', 'epsilon']\n"
    "base = two_set_show()\n"
    "spots = [s.replace(tags=tags) for s in base.cast.spots]\n"
    "cast = base.cast.replace(spots=spots)\n"
    "show = base.replace(cast=cast)\n"
    "show = define_selection(show, 'pair', ['T1', 'T4'])\n"
    "def digest(data):\n"
    "    return hashlib.sha256(data).hexdigest()\n"
)

MarkedTest = TypeVar("MarkedTest", bound=Callable[..., object])
TestParams = ParamSpec("TestParams")


def make_set(*rows: tuple[str, float, float], off: tuple[str, ...] = ()) -> Set:
    """Builds set "1" at count 0 from (entity, x, y) rows.

    Args:
        rows: One (entity, x, y) tuple per spot.
        off: Spots placed off the field.
    """
    placements = tuple(
        Placement(entity=SpotId(e), x=x, y=y, on_field=e not in off) for e, x, y in rows
    )
    return Set(id=SetId(SET_ID), counts=0.0, placements=placements)


def make_facing_set(
    *rows: tuple[str, float, float, float],
    off: tuple[str, ...] = (),
    pinned: tuple[str, ...] = (),
) -> Set:
    """Builds set "1" at count 0 from (entity, x, y, facing) rows.

    Args:
        rows: One (entity, x, y, facing) tuple per spot.
        off: Spots placed off the field.
        pinned: Spots whose placement is pinned.
    """
    placements = tuple(
        Placement(entity=SpotId(e), x=x, y=y, facing=f, on_field=e not in off, pinned=e in pinned)
        for e, x, y, f in rows
    )
    return Set(id=SetId(SET_ID), counts=0.0, placements=placements)


def pin_spot(show: Show, set_id: str, entity: str) -> Show:
    """Pins the placement of `entity` in set `set_id`."""
    return with_set(show, show.set(set_id).with_placements({entity: PlacementPatch(pinned=True)}))


def replaced_form_show() -> Show:
    """Stores form a (T1..T4) on set "2", then re-places the set with form b (T2, T3) only.

    Both forms stay in `Show.forms`, but the set lists only b, so a is stale.
    """
    show = two_set_show()
    first, (stale,) = place(show, "2", [line_form("a", ["T1", "T2", "T3", "T4"], (0, 9), (6, 9))])
    show = with_set(show, first).replace(forms={"a": stale})
    second, (current,) = place(show, "2", [line_form("b", ["T2", "T3"], (0, 12), (2, 12))])
    return with_set(show, second).replace(forms={"a": stale, "b": current})


def with_curve_rows(show: Show, entities: Iterable[str], locked: bool = False) -> Show:
    """Replaces the transitions with one 8-count 1 -> 2 transition of curved rows.

    Args:
        show: Show whose sets "1" and "2" the transition joins.
        entities: Spots that get a curve row through (1, 7).
        locked: Whether the rows are locked.
    """
    rows = {
        SpotId(e): PathRow(
            entity=SpotId(e), style=PathStyle.CURVE, waypoints=(CURVE_WAYPOINT,), locked=locked
        )
        for e in entities
    }
    spec = TransitionSpec(id=make_tid(*FIRST_TRANSITION), counts=CURVE_COUNTS, rows=rows)
    return show.replace(transitions=(spec,))


def open_session(show: Show, at: datetime) -> tuple[InMemoryRepository, Workspace, Workspace]:
    """Saves `show` through one workspace, then opens a second one on the same repository.

    Two working copies let a test write through one while the other holds a stale view.

    Returns:
        session: The repository, the second workspace and the workspace that saved the show.
    """
    repo = InMemoryRepository()
    saver = open_workspace(repo, SHOW_ID)
    save_show(saver, show, at)
    return repo, open_workspace(repo, SHOW_ID), saver


def _angle(origin: Point, p: Point) -> float:
    return math.degrees(math.atan2(p[1] - origin[1], p[0] - origin[0]))


def bisector_rotation(a: Point, b: Point, reach: float, long_way: bool) -> tuple[Point, float]:
    """Returns a pivot and sweep whose arc carries `a` onto `b`, so a ROTATE path ends on target.

    The pivot sits on the perpendicular bisector of a-b, `reach` meters from its midpoint
    (negative reach picks the other side). When a and b coincide the pivot sits `reach`
    meters to the side and the sweep is a full turn.

    Args:
        a: Start point.
        b: Target point.
        reach: Signed distance of the pivot from the midpoint, nonzero.
        long_way: Sweeps the long way around instead of the short way.

    Returns:
        rotation: The pivot point and the signed sweep in degrees.
    """
    chord = math.dist(a, b)
    if chord <= STILL_TOL_M:
        return (a[0] + reach, a[1]), -FULL_TURN if long_way else FULL_TURN
    normal = (-(b[1] - a[1]) / chord, (b[0] - a[0]) / chord)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    pivot = (mid[0] + reach * normal[0], mid[1] + reach * normal[1])
    sweep = (_angle(pivot, b) - _angle(pivot, a) + HALF_TURN) % FULL_TURN - HALF_TURN
    if long_way:
        sweep -= math.copysign(FULL_TURN, sweep)
    return pivot, sweep


def defect(defect_id: str) -> Callable[[MarkedTest], MarkedTest]:
    """Marks a test as an open audited defect: a strict xfail keyed on its ID."""

    def decorate(test: MarkedTest) -> MarkedTest:
        marked = pytest.mark.defect(defect_id)(test)
        return cast(MarkedTest, pytest.mark.xfail(strict=True, reason=defect_id)(marked))

    return decorate


@contextmanager
def capped_address_space() -> Iterator[None]:
    """Lowers RLIMIT_AS to the current size plus headroom so a runaway allocation fails fast."""
    import resource  # POSIX only; imported here so Windows can still collect callers

    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[0])
    used = pages * resource.getpagesize()
    resource.setrlimit(resource.RLIMIT_AS, (used + ALLOCATION_HEADROOM_BYTES, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _is_hook_frame(frame: FrameType) -> bool:
    """Tells whether a frame runs a tracer, finalizer, GC callback or error hook."""
    module = str(frame.f_globals.get("__name__", ""))
    if module in NO_ALARM_MODULES or module.partition(".")[0] in NO_ALARM_MODULES:
        return True
    hook_codes = {getattr(callback, "__code__", None) for callback in gc.callbacks}
    return frame.f_code.co_name == FINALIZER_NAME or frame.f_code in hook_codes


def _may_interrupt(frame: FrameType | None, guard: FrameType | None) -> bool:
    """Tells whether a hang alarm may raise here: inside the guarded call, outside every hook.

    Args:
        frame: Frame the alarm interrupted.
        guard: Frame of the fails_after wrapper; the alarm raises only in frames it called.
    """
    if frame is None or frame is guard:
        return False
    while frame is not None:
        if frame is guard:
            return True
        if _is_hook_frame(frame):
            return False
        frame = frame.f_back
    return False


@contextmanager
def _resource_warnings_dropped_after(timed_out: list[bool]) -> Iterator[None]:
    """Drops unraisable ResourceWarnings once the hang alarm has fired, forwarding all others.

    An alarm landing inside io.open() makes CPython free the half-built file at once, and its
    ResourceWarning would fail the hang test's teardown. Before the alarm fires, every leak
    still reaches pytest.

    Args:
        timed_out: Non-empty once the alarm has raised its timeout.
    """
    forward = sys.unraisablehook

    # Quoted: sys.UnraisableHookArgs exists only for type checkers before Python 3.14.
    def hook(unraisable: "sys.UnraisableHookArgs") -> None:
        if timed_out and issubclass(unraisable.exc_type, ResourceWarning):
            return
        forward(unraisable)

    sys.unraisablehook = hook
    try:
        yield
    finally:
        sys.unraisablehook = forward


def _release_interrupted_frames(timeout: BaseException) -> None:
    """Frees what the hang alarm cut off mid-use, such as a file opened but not yet closed.

    Their ResourceWarnings belong to the interruption, not to the code under test, and would
    otherwise surface later as unraisable errors in a teardown.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        traceback.clear_frames(timeout.__traceback__)
        gc.collect()


def _run_under_hang_alarm(seconds: float, call: Callable[[], None]) -> None:
    """Runs `call`, failing the test when it is still running after `seconds`.

    Holds the whole alarm mechanism described on `fails_after`; this frame is the guard that
    ticks must land under.

    Args:
        seconds: Wall-clock budget before the call fails as a hang.
        call: Zero-argument callable to run under the alarm.
    """
    own_frame = inspect.currentframe()
    timed_out: list[bool] = []

    def interrupt(_signum: int, frame: FrameType | None) -> None:
        if _may_interrupt(frame, own_frame):
            timed_out.append(True)
            pytest.fail(f"Timeout >{seconds}s", pytrace=False)

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds, HANG_RETRY_S)
    with _resource_warnings_dropped_after(timed_out):
        try:
            try:
                call()
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
        except pytest.fail.Exception as failure:
            if timed_out:
                _release_interrupted_frames(failure)
            raise


def fails_after(
    seconds: float,
) -> Callable[[Callable[TestParams, None]], Callable[TestParams, None]]:
    """Fails the decorated test when it is still running after `seconds`, via SIGALRM (POSIX).

    pytest-timeout's signal method raises once, wherever the alarm lands. Landing in coverage's
    C-tracer callback between its data lock's acquire and release left the lock held, and the
    next traced file deadlocked the run (CI on Python 3.12/3.13, 2026-11-14); landing in a
    finalizer or GC callback dropped the exception as unraisable, and the hang ran on until the
    runner ran out of memory (CI, 2026-11-15); a later tick inside pytest's unraisable hook then
    broke the hook (macOS 3.12 CI, 2026-11-15). So the alarm repeats every HANG_RETRY_S, raises
    only inside the guarded call, and skips ticks that land in hook frames (`_is_hook_frame`).
    A callback that C code registered with a bare weakref.ref is not detectable.

    Args:
        seconds: Wall-clock budget before the test fails as a hang.
    """

    def decorate(test: Callable[TestParams, None]) -> Callable[TestParams, None]:
        @functools.wraps(test)
        def guarded(*args: TestParams.args, **kwargs: TestParams.kwargs) -> None:
            _run_under_hang_alarm(seconds, lambda: test(*args, **kwargs))

        return guarded

    return decorate


def _shortened(sample: object) -> str:
    """Returns the bounded repr of `sample`, quoting text so blank input stays visible."""
    return bounded_repr(repr(sample) if isinstance(sample, str) else sample)


def only_e8s_errors(call: Callable[[], object], sample: object = None) -> None:
    """Runs `call` under a 5 s hang alarm and fails the test on any non-E8sError exception.

    Meant for fuzz targets: hostile input may be rejected with an E8sError, never crash with
    a builtin error or hang. Skips on Windows, where SIGALRM does not exist.

    Args:
        call: Zero-argument callable that feeds one generated input to the code under test.
        sample: The generated input, named in the failure message.
    """
    if sys.platform == "win32":
        pytest.skip("SIGALRM is POSIX only")

    def guarded_call() -> None:
        try:
            call()
        except E8sError:
            return
        except Exception as error:
            pytest.fail(
                f"{type(error).__name__} escaped on input {_shortened(sample)}: "
                f"{bounded_repr(str(error))}",
                pytrace=False,
            )

    _run_under_hang_alarm(FUZZ_BUDGET_S, guarded_call)


def context_entries(error: E8sError, key: str) -> list[dict[str, JsonValue]]:
    """Returns the JSON objects listed under `key` in an error's context.

    Args:
        error: Error whose context holds a list of objects under `key`.
        key: Context key, such as `changes`.

    Returns:
        entries: The objects of that list, without a trailing `"(+n more)"` count.
    """
    listed = error.context[key]
    assert isinstance(listed, list), f"context[{key!r}] is {type(listed).__name__}, not a list"
    return [entry for entry in listed if isinstance(entry, dict)]


# Any: a test indexes into nested JSON whose shape it knows, without narrowing each level.
def json_doc(model: FrozenModel) -> dict[str, Any]:
    """Returns `model.to_json()`, typed so a test can index into nested values.

    Args:
        model: Any e8s model.

    Returns:
        document: The JSON-safe dict `to_json` returns.
    """
    return model.to_json()


def outputs_under_hash_seeds(script: str, seeds: Iterable[str] = HASH_SEEDS) -> list[str]:
    """Runs `script` in a fresh interpreter under each PYTHONHASHSEED and collects its output.

    A run may fail only with one of EXPECTED_FAILURES; any other crash fails the caller.

    Args:
        script: Python source passed to `python -c`.
        seeds: Hash seeds to run under, one subprocess each; the default covers 1, 2 and 3, not 0,
            which disables hash randomization.

    Returns:
        outputs: Per seed, stdout plus the tail of stderr.
    """
    outputs = []
    for seed in seeds:
        done = subprocess.run(
            [sys.executable, "-c", script],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            tail = done.stderr[-STDERR_CHECK_TAIL:]
            assert any(name in done.stderr for name in EXPECTED_FAILURES), tail
        outputs.append(done.stdout + done.stderr[-STDERR_KEPT_TAIL:])
    return outputs


def digests_under_hash_seeds(imports: str, output: str) -> list[list[str]]:
    """Hashes one output of the tagged show twice in each of two hash-seeded interpreters.

    Args:
        imports: Import lines the output expression needs.
        output: Python expression over `show` that evaluates to bytes.

    Returns:
        digests: Per seed, the SHA-256 of the first and the second evaluation.
    """
    script = f"{TAGGED_SHOW_SCRIPT}{imports}\nprint(digest({output}), digest({output}))\n"
    return [line.split() for line in outputs_under_hash_seeds(script, TWO_HASH_SEEDS)]
