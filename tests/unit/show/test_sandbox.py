#!/usr/bin/env python3

"""
test_sandbox.py --- tests the set-script policy and the isolated build runner
"""

import pytest

from stage.core.errors import SchemaError, TimeRangeError, ValidationError
from stage.show.sandbox import check_script, run_analysis_script, run_set_script
from tests.fixtures.shows import two_set_show

GOOD = '''
from stage.choreo.models import Set


def build(ctx):
    """Moves every spot of the previous set 1 m upstage."""
    moved = {p.entity: {"y": p.y + 1.0} for p in ctx.prev.placements}
    ctx.log("moved", count=len(moved))
    built = ctx.prev.with_placements(moved)
    return built.model_copy(update={"id": ctx.set_id, "counts": ctx.counts})
'''


@pytest.mark.parametrize(
    "source",
    [
        "import stagecheck\ndef build(ctx): return None",
        "from stagecheck import run\ndef build(ctx): return None",
        "import os\ndef build(ctx): return None",
        "import subprocess\ndef build(ctx): return None",
        "def build(ctx): return open('/etc/passwd').read()",
        "def build(ctx): return ctx.__class__.__mro__",
        "def build(ctx): return eval('1')",
        "def build(ctx): return getattr(ctx, 'show')",
        "x = 1",
    ],
)
def test_policy_rejects_unsafe_or_incomplete_scripts(source: str) -> None:
    with pytest.raises(SchemaError):
        check_script(source)


def test_policy_accepts_library_math_and_numpy() -> None:
    check_script(
        "import math\nimport numpy as np\nfrom stage.geo.shapes import line\n\n"
        "def build(ctx):\n    return ctx.prev\n"
    )


def test_policy_names_the_offending_line() -> None:
    with pytest.raises(SchemaError) as info:
        check_script("def build(ctx):\n    import os\n    return None\n")
    assert info.value.context["line"] == 2


def test_run_set_script_builds_a_set_in_a_subprocess() -> None:
    show = two_set_show()
    result = run_set_script(show, "2", GOOD)
    assert result.set.get("T1").y == 6.0
    assert result.set.source_hash
    assert any("moved" in line for line in result.logs)
    assert result.duration_ms > 0


def test_run_set_script_reports_script_errors() -> None:
    with pytest.raises(ValidationError) as info:
        run_set_script(two_set_show(), "2", "def build(ctx):\n    return 1 / 0\n")
    assert "ZeroDivisionError" in info.value.message


def test_run_set_script_rejects_wrong_return_type() -> None:
    with pytest.raises(ValidationError):
        run_set_script(two_set_show(), "2", "def build(ctx):\n    return 42\n")


def test_run_set_script_times_out() -> None:
    with pytest.raises(TimeRangeError):
        run_set_script(
            two_set_show(), "2", "def build(ctx):\n    while True:\n        pass\n", timeout_s=1.0
        )


def test_run_analysis_script_returns_result_and_stdout() -> None:
    source = (
        "def main(ctx):\n    print('sets', len(ctx.show.sets))\n"
        "    return {'spots': len(ctx.cast.spots)}\n"
    )
    result = run_analysis_script(two_set_show(), source)
    assert result.result == {"spots": 4}
    assert result.stdout.strip() == "sets 2"


def test_imports_outside_policy_fail_at_runtime_too() -> None:
    source = "def build(ctx):\n    import stage.show.storage\n    return ctx.prev\n"
    with pytest.raises(SchemaError):
        check_script(source)


def test_run_set_script_places_returned_forms() -> None:
    source = (
        "from stage.choreo.forms import line_form\n\n"
        "def build(ctx):\n"
        "    form = line_form('f2', ['T1', 'T2', 'T3', 'T4'], (0.0, 12.0), (6.0, 12.0))\n"
        "    return [form]\n"
    )
    result = run_set_script(two_set_show(), "2", source)
    assert result.set.get("T4").point == (6.0, 12.0)
    assert result.set.get("T1").form == "f2"
