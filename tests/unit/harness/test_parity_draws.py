#!/usr/bin/env python3

"""
test_parity_draws.py --- checks that the parity draws round the same on every platform

Contains:
    test_uniform_rounds_the_product_and_the_sum_separately: each value is CPython's unfused
        `low + (high - low) * u`, and differs from the fused result somewhere
    test_uniform_draws_what_generator_uniform_draws: same draws as numpy's `uniform`, within
        one ulp of the product, and the generator ends in the same state
    test_uniform_returns_a_float_without_a_size: one draw, as `Generator.uniform` gives
    test_regression_power_of_ten_is_correctly_rounded: every power the reader parses, 1e23 too
    test_powers_of_ten_maps_each_exponent_keeping_the_shape: arrays, repeats and empty arrays
    test_libm_checks_flag_float_powers_and_libm_calls_and_spare_exact_ones: the source checks
    test_core_area_modules_include_its_inputs_and_draws: the import walk reaches every helper
    test_regression_core_parity_inputs_never_use_libm_pow: no power a C math library rounds
    test_regression_core_parity_inputs_never_call_libm_functions: no exp, log or trig either
    test_ported_area_inputs_never_use_libm: every gated area's case modules pass both checks
"""

import ast
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from tests.parity.draws import power_of_ten, powers_of_ten, uniform
from tests.parity.run import PORTED_AREAS

SEED = 20261128
DRAWS = 20_000
LOW, HIGH = -3.0, 3.0
# Fused or not, the two results differ by at most half an ulp of the product, under one of SPAN.
SPAN = HIGH - LOW
BOUNDS = ((LOW, HIGH), (-1e4, 1e4), (1.0, 18.0), (-324.0, -290.0))
# Below 1e-324 the nearest double is 0.0; past 1e308 the power overflows a double.
LOWEST_EXPONENT, HIGHEST_EXPONENT = -330, 308
# 1e23 lies exactly halfway between two doubles; glibc's pow(10, 23) returned the upper one
# and Apple's the even one (2026-11-28: the core cases' inputs differed between Linux and macOS).
TIE_EXPONENT = 23
EVEN_NEIGHBOUR_OF_1E23 = 99_999_999_999_999_991_611_392
PARITY_FOLDER = Path(__file__).resolve().parents[2] / "parity"
PARITY_PACKAGE = "tests.parity"
EXACT_POWER_BASE = 2
LIBM_POWERS = frozenset({"pow", "math.pow", "np.power", "np.float_power"})
LIBM_FUNCTIONS = frozenset(
    {
        f"{module}.{name}"
        for module in ("math", "np")
        for name in (
            "exp",
            "exp2",
            "expm1",
            "log",
            "log10",
            "log2",
            "log1p",
            "sin",
            "cos",
            "tan",
            "asin",
            "acos",
            "atan",
            "atan2",
            "arcsin",
            "arccos",
            "arctan",
            "arctan2",
            "sinh",
            "cosh",
            "tanh",
            "cbrt",
        )
    }
)
POWER_HINT = "build powers of ten with tests.parity.draws.power_of_ten or powers_of_ten"
FUNCTION_HINT = "use exact arithmetic (Decimal, Fraction, int) on parity inputs"


@pytest.mark.parametrize(("low", "high"), BOUNDS)
def test_uniform_rounds_the_product_and_the_sum_separately(low: float, high: float) -> None:
    draws = np.random.default_rng(SEED).random(DRAWS).tolist()
    values = uniform(np.random.default_rng(SEED), low, high, size=DRAWS).tolist()
    assert values == [low + (high - low) * u for u in draws]
    assert values != [math.fma(high - low, u, low) for u in draws]


@pytest.mark.parametrize("size", [DRAWS, (DRAWS // 2, 2)])
def test_uniform_draws_what_generator_uniform_draws(size: int | tuple[int, ...]) -> None:
    ours, numpys = np.random.default_rng(SEED), np.random.default_rng(SEED)
    values = uniform(ours, LOW, HIGH, size=size)
    expected = numpys.uniform(LOW, HIGH, size=size)
    assert values.shape == expected.shape
    assert np.all(np.abs(values - expected) <= np.spacing(SPAN))
    assert np.all((values >= LOW) & (values < HIGH))
    assert ours.random(8).tolist() == numpys.random(8).tolist()


def test_uniform_returns_a_float_without_a_size() -> None:
    value = uniform(np.random.default_rng(SEED), 1.0, 18.0)
    assert type(value) is float
    assert value == 1.0 + 17.0 * np.random.default_rng(SEED).random()


def test_regression_power_of_ten_is_correctly_rounded() -> None:
    exponents = range(LOWEST_EXPONENT, HIGHEST_EXPONENT + 1)
    exact = {k: float(10**k) if k >= 0 else 1 / 10**-k for k in exponents}
    assert [k for k in exponents if power_of_ten(k) != exact[k]] == []
    assert all(type(power_of_ten(k)) is float for k in (-5, 0, 5))
    assert int(power_of_ten(TIE_EXPONENT)) == EVEN_NEIGHBOUR_OF_1E23


def test_powers_of_ten_maps_each_exponent_keeping_the_shape() -> None:
    exponents = np.array([[23, -7, 0], [23, 308, -324]], dtype=np.int64)
    powers = powers_of_ten(exponents)
    assert powers.dtype == np.float64
    assert powers.shape == exponents.shape
    assert powers.ravel().tolist() == [power_of_ten(k) for k in exponents.ravel().tolist()]
    assert powers_of_ten(np.array([], dtype=np.int64)).shape == (0,)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _int_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int


def _exact_power(base: ast.expr, exponent: ast.expr) -> bool:
    if not isinstance(base, ast.Constant):
        return False
    if type(base.value) in (int, float) and base.value == EXACT_POWER_BASE:
        return True
    return _int_literal(base) and _int_literal(exponent)


def _rounded_power(node: ast.AST) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return not _exact_power(node.left, node.right)
    if isinstance(node, ast.AugAssign):
        return isinstance(node.op, ast.Pow)
    return isinstance(node, ast.Call) and _dotted(node.func) in LIBM_POWERS


def libm_powers(source: str) -> list[str]:
    """Lists each power whose result a C math library rounds, as `<line>: <expression>`.

    A power of 2 is exact, and so is an int literal raised to an int literal. Any other `**`,
    `pow` or numpy power goes through `pow`, whose last bit differs between libraries.
    """
    return _shown([node for node in ast.walk(ast.parse(source)) if _rounded_power(node)])


def libm_calls(source: str) -> list[str]:
    """Lists each call of an exponential, logarithm or trigonometric function of math or numpy."""
    return _shown(
        [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and _dotted(node.func) in LIBM_FUNCTIONS
        ]
    )


def _shown(nodes: list[ast.AST]) -> list[str]:
    located = [node for node in nodes if isinstance(node, ast.expr | ast.stmt)]
    ordered = sorted(located, key=lambda node: (node.lineno, node.col_offset))
    return [f"{node.lineno}: {ast.unparse(node)}" for node in ordered]


def _parity_imports(path: Path) -> set[Path]:
    modules = [
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    prefix = f"{PARITY_PACKAGE}."
    return {
        PARITY_FOLDER / f"{module.removeprefix(prefix)}.py"
        for module in modules
        if module.startswith(prefix)
    }


def area_modules(area: str) -> list[Path]:
    """Lists cases_<area>.py and every tests.parity module it imports, directly or not."""
    seen: set[Path] = set()
    pending = {PARITY_FOLDER / f"cases_{area}.py"}
    while pending:
        path = pending.pop()
        seen.add(path)
        pending |= _parity_imports(path) - seen
    return sorted(seen)


def _findings(area: str, check: Callable[[str], list[str]]) -> list[str]:
    return [
        f"tests/parity/{path.name}:{finding}"
        for path in area_modules(area)
        for finding in check(path.read_text(encoding="utf-8"))
    ]


def test_libm_checks_flag_float_powers_and_libm_calls_and_spare_exact_ones() -> None:
    flagged = "a = 10.0 ** k\nb = 10 ** -3\nc = pow(10, k)\nd = np.power(10.0, ks)\ne **= 2\n"
    spared = "a = 2.0 ** k\nb = 2 ** (w - 1)\nc = 10**640\nd = -(2**63) - 1\n"
    assert [line.split(":")[0] for line in libm_powers(flagged)] == ["1", "2", "3", "4", "5"]
    assert libm_powers(spared) == []
    assert libm_calls("x = math.log10(v)\ny = np.exp(a)\nz = math.floor(v)\n") == [
        "1: math.log10(v)",
        "2: np.exp(a)",
    ]


def test_core_area_modules_include_its_inputs_and_draws() -> None:
    names = {path.name for path in area_modules("core")}
    assert {"cases_core.py", "core_json.py", "json_inputs.py", "draws.py"} <= names


def test_regression_core_parity_inputs_never_use_libm_pow() -> None:
    found = _findings("core", libm_powers)
    assert found == [], f"{POWER_HINT}:\n" + "\n".join(found)


def test_regression_core_parity_inputs_never_call_libm_functions() -> None:
    found = _findings("core", libm_calls)
    assert found == [], f"{FUNCTION_HINT}:\n" + "\n".join(found)


@pytest.mark.parametrize("area", sorted(PORTED_AREAS))
def test_ported_area_inputs_never_use_libm(area: str) -> None:
    found = _findings(area, libm_powers) + _findings(area, libm_calls)
    assert found == [], f"{POWER_HINT}; {FUNCTION_HINT}:\n" + "\n".join(found)
