#!/usr/bin/env python3

"""
test_port_rules.py --- tests the rules every port to the compiled core must follow

A ported package records its module disposition in `tests/unit/rules/ported/<area>.toml`: the
modules it deleted (`moves`), the modules that only re-export the core (`reexport`), the
modules that stay in Python (`stays`) and the files still allowed to import scipy or shapely
(`interop`). Until a package is ported no such file exists, and the two tests that read them
run once, assert nothing and say so in their test id.

Contains:
    CORE_AREAS: names the seven submodules of e8s._core
    find_unsafe(): lists file and line of each `unsafe` keyword in Rust source, comments and
        strings left out
    test_no_unsafe_in_our_crates: fails on any `unsafe` in the workspace's Rust sources
    test_unsafe_scan_flags_code_and_skips_comments_and_strings: proves the scan can fail
    public_core_callables(): lists the public callables of the seven core submodules
    run_examples(): runs the `>>>` examples in the docstrings of named callables
    missing_examples(): lists the functions whose docstring has no `>>>` example
    test_core_docstring_examples_run: runs the `>>>` examples of every public core callable
    test_every_public_core_function_has_an_example: fails on a public core function whose
        docstring has no `>>>` example
    test_example_checks_flag_a_failing_and_a_missing_example: proves both checks can fail
    find_frozen_model_bypasses(): lists file and line of each `object.__setattr__` or
        `object.__delattr__` use
    test_library_never_bypasses_frozen_models: fails on any such use under e8s/
    test_frozen_model_bypass_scan_flags_calls_and_lookups: proves the scan can fail
    Disposition: holds one ported package's module disposition
    load_disposition(): reads and checks one disposition file
    disposition_problems(): lists how a package's modules break its disposition
    foreign_imports(): lists file, line and module of each scipy or shapely import in a package
    test_ported_package_matches_its_disposition: fails when a ported package breaks its file
    test_ported_packages_import_no_scipy_or_shapely: fails on such an import outside `interop`
    test_disposition_check_flags_each_broken_rule: proves the disposition check can fail
    test_foreign_import_scan_flags_imports_outside_interop: proves the import scan can fail
"""

import ast
import doctest
import importlib
import inspect
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPO_ROOT / "e8s"
CRATE_ROOTS = (REPO_ROOT / "crates", REPO_ROOT / "fuzz")
PORTED_ROOT = Path(__file__).resolve().parent / "ported"
CORE_AREAS = ("core", "geo", "time", "sheet", "cast", "engine", "forms")
# The engine and the forms ports share the choreo package; each lists only its own modules.
AREA_PACKAGES = {area: area for area in CORE_AREAS} | {"engine": "choreo", "forms": "choreo"}
DISPOSITION_KEYS = frozenset({"moves", "reexport", "stays", "interop"})
CORE_PACKAGE = "e8s._core"
FOREIGN_PACKAGES = frozenset({"scipy", "shapely"})
BYPASS_ATTRIBUTES = frozenset({"__setattr__", "__delattr__"})
UNSAFE = re.compile(r"\bunsafe\b")
# A Rust comment or literal: the scan blanks these before it looks for the keyword.
RUST_NON_CODE = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r'|b?r(?P<hashes>#*)".*?"(?P=hashes)'
    r'|b?"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])'",
    re.DOTALL,
)
NO_PORTED_PACKAGE = "no-ported-package-yet"
MIN_RUST_FILES = 5
MIN_LIBRARY_FILES = 100
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no e8s tree"
)


def _blank_non_code(source: str) -> str:
    """Replaces comments and literals by spaces, keeping every newline so lines still count."""
    return RUST_NON_CODE.sub(lambda found: re.sub(r"[^\n]", " ", found.group()), source)


def find_unsafe(source: str, name: str) -> list[str]:
    """Lists `name:line` for each `unsafe` keyword in Rust source, comments and strings left out.

    Args:
        source: The text of one Rust file.
        name: The file's name, used in the result.

    Returns:
        hits: One `name:line` entry per `unsafe` keyword.
    """
    code = _blank_non_code(source)
    return [
        f"{name}:{code.count(chr(10), 0, found.start()) + 1}" for found in UNSAFE.finditer(code)
    ]


def _rust_files() -> list[Path]:
    return sorted(
        path
        for root in CRATE_ROOTS
        if root.is_dir()
        for path in root.rglob("*.rs")
        if "target" not in path.relative_to(root).parts
    )


def test_no_unsafe_in_our_crates() -> None:
    """Requires every Rust file of our crates and fuzz targets to hold no `unsafe` keyword."""
    files = _rust_files()
    assert len(files) >= MIN_RUST_FILES
    hits = [
        hit
        for path in files
        for hit in find_unsafe(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
    ]
    assert not hits, "remove unsafe code: " + ", ".join(hits)


def test_unsafe_scan_flags_code_and_skips_comments_and_strings() -> None:
    source = (
        "// unsafe in a comment\n"
        "/* unsafe\n block */\n"
        'let a = "unsafe";\n'
        'let b = r#"unsafe "quoted""#;\n'
        "unsafe fn f() {}\n"
        "fn g() { unsafe { h() } }\n"
        "let not_unsafe_name = 1;\n"
    )
    assert find_unsafe(source, "m.rs") == ["m.rs:6", "m.rs:7"]


def public_core_callables() -> list[tuple[str, Callable[..., object]]]:
    """Lists the public callables of the seven core submodules.

    Returns:
        callables: (dotted name, callable) pairs, sorted by name.
    """
    found: list[tuple[str, Callable[..., object]]] = []
    for area in CORE_AREAS:
        module = importlib.import_module(f"{CORE_PACKAGE}.{area}")
        for name, value in sorted(vars(module).items()):
            if not name.startswith("_") and callable(value):
                found.append((f"{module.__name__}.{name}", value))
    return found


def run_examples(callables: list[tuple[str, Callable[..., object]]]) -> tuple[int, int]:
    """Runs the `>>>` examples in the docstrings of named callables.

    Args:
        callables: (dotted name, callable) pairs.

    Returns:
        counts: The number of examples that failed and the number run.
    """
    parser = doctest.DocTestParser()
    runner = doctest.DocTestRunner()
    for name, value in callables:
        runner.run(parser.get_doctest(inspect.getdoc(value) or "", {}, name, None, 0))
    failed, attempted = runner.summarize(verbose=False)
    return failed, attempted


def missing_examples(callables: list[tuple[str, Callable[..., object]]]) -> list[str]:
    """Lists the names of the functions whose docstring has no `>>>` example."""
    return [
        name
        for name, value in callables
        if inspect.isroutine(value) and ">>>" not in (inspect.getdoc(value) or "")
    ]


def test_core_docstring_examples_run() -> None:
    """Runs the `>>>` examples of every public core callable, as module doctests run."""
    failed, attempted = run_examples(public_core_callables())
    assert failed == 0, f"{failed} of {attempted} core docstring examples failed"


def test_every_public_core_function_has_an_example() -> None:
    """Requires each public core function's docstring to carry a `>>>` example."""
    missing = missing_examples(public_core_callables())
    assert not missing, "add a >>> example to: " + ", ".join(missing)


FAILING_EXAMPLE = "Returns one.\n\n>>> 1 + 1\n3\n"


def _returns_one() -> int:
    return 1


def _also_returns_one() -> int:
    return 1


def test_example_checks_flag_a_failing_and_a_missing_example(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set at run time: written as a docstring, pytest's doctest collection would run it too.
    monkeypatch.setattr(_returns_one, "__doc__", FAILING_EXAMPLE)
    callables: list[tuple[str, Callable[..., object]]] = [
        ("m.documented", _returns_one),
        ("m.undocumented", _also_returns_one),
    ]
    assert run_examples(callables) == (1, 1)
    assert "in m.documented\nFailed example:" in capsys.readouterr().out
    assert missing_examples(callables) == ["m.undocumented"]


def _is_bypass(node: ast.Attribute | ast.Call) -> bool:
    """Tells whether a node looks up `object.__setattr__` or `object.__delattr__`."""
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "object"
            and node.attr in BYPASS_ATTRIBUTES
        )
    return (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "object"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in BYPASS_ATTRIBUTES
    )


def find_frozen_model_bypasses(source: str, name: str) -> list[str]:
    """Lists `name:line` for each `object.__setattr__` or `object.__delattr__` use.

    A call, a bare lookup and `getattr(object, "__setattr__")` all count: each writes to an
    object behind its own `__setattr__`, which is how a frozen model refuses a write.
    """
    lines = {
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute | ast.Call) and _is_bypass(node)
    }
    return [f"{name}:{line}" for line in sorted(lines)]


def test_library_never_bypasses_frozen_models() -> None:
    """Requires no module under e8s/ to write to an object around its own `__setattr__`."""
    files = sorted(LIBRARY_ROOT.rglob("*.py"))
    assert len(files) >= MIN_LIBRARY_FILES
    hits = [
        hit
        for path in files
        for hit in find_frozen_model_bypasses(
            path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT))
        )
    ]
    assert not hits, "build a new model instead of writing to a frozen one: " + ", ".join(hits)


def test_frozen_model_bypass_scan_flags_calls_and_lookups() -> None:
    source = (
        "object.__setattr__(self, 'key', key)\n"
        "write = object.__delattr__\n"
        "getattr(object, '__setattr__')(self, 'a', 1)\n"
        "super().__setattr__('a', 1)\n"
        "self.__setattr__('a', 1)\n"
    )
    assert find_frozen_model_bypasses(source, "m.py") == ["m.py:1", "m.py:2", "m.py:3"]


@dataclass(frozen=True)
class Disposition:
    """Holds one ported package's module disposition.

    Attributes:
        package: The package folder under the library root, such as `geo`.
        moves: Module names the port deleted.
        reexport: Module names that hold only a docstring and imports from the core.
        stays: Module names that stay in Python.
        interop: Repository paths still allowed to import scipy or shapely.
    """

    package: str
    moves: tuple[str, ...]
    reexport: tuple[str, ...]
    stays: tuple[str, ...]
    interop: tuple[str, ...]


def load_disposition(path: Path) -> Disposition:
    """Reads and checks one disposition file.

    Args:
        path: A `ported/<area>.toml` file; its stem names the core area.

    Returns:
        disposition: The file's lists, with the package the area ports.
    """
    table = tomllib.loads(path.read_text(encoding="utf-8"))
    assert path.stem in AREA_PACKAGES, f"{path.name}: unknown area {path.stem!r}"
    assert set(table) == DISPOSITION_KEYS, f"{path.name}: keys must be {sorted(DISPOSITION_KEYS)}"
    for key in sorted(DISPOSITION_KEYS):
        names = table[key]
        assert isinstance(names, list), f"{path.name}: {key} is not a list"
        assert all(isinstance(name, str) for name in names), f"{path.name}: {key} holds a non-text"
    return Disposition(
        package=AREA_PACKAGES[path.stem],
        moves=tuple(table["moves"]),
        reexport=tuple(table["reexport"]),
        stays=tuple(table["stays"]),
        interop=tuple(table["interop"]),
    )


def _is_reexport(tree: ast.Module) -> bool:
    """Tells whether a module holds only a docstring and imports from the compiled core."""
    body = tree.body[1:] if ast.get_docstring(tree) is not None else tree.body
    return all(
        isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module is not None
        and (node.module == CORE_PACKAGE or node.module.startswith(f"{CORE_PACKAGE}."))
        for node in body
    )


def disposition_problems(disposition: Disposition, library_root: Path) -> list[str]:
    """Lists how a package's modules break its disposition.

    Args:
        disposition: The package's disposition.
        library_root: The folder that holds the library's packages.

    Returns:
        problems: One line per broken rule; empty when the package matches.
    """
    package = library_root / disposition.package
    listed = [*disposition.moves, *disposition.reexport, *disposition.stays]
    problems = [f"{name} is listed twice" for name in sorted(set(listed)) if listed.count(name) > 1]
    problems += [
        f"{name} moved but exists"
        for name in disposition.moves
        if (package / f"{name}.py").exists()
    ]
    for name in [*disposition.reexport, *disposition.stays]:
        if not (package / f"{name}.py").is_file():
            problems.append(f"{name} is listed but missing")
    for name in disposition.reexport:
        path = package / f"{name}.py"
        if path.is_file() and not _is_reexport(ast.parse(path.read_text(encoding="utf-8"))):
            problems.append(f"{name} holds more than imports from {CORE_PACKAGE}")
    root = library_root.parent
    problems += [
        f"{path} is interop but missing"
        for path in disposition.interop
        if not (root / path).is_file()
    ]
    return problems


def foreign_imports(disposition: Disposition, library_root: Path) -> list[str]:
    """Lists `path:line module` for each scipy or shapely import of a ported package's modules.

    Args:
        disposition: The package's disposition; its `interop` files are skipped.
        library_root: The folder that holds the library's packages.

    Returns:
        hits: One entry per import, outside the `interop` files.
    """
    root = library_root.parent
    package = library_root / disposition.package
    hits: list[str] = []
    for name in [*disposition.reexport, *disposition.stays]:
        path = package / f"{name}.py"
        where = path.relative_to(root).as_posix()
        if where in disposition.interop or not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
                modules = [node.module]
            hits += [
                f"{where}:{node.lineno} {module}"
                for module in modules
                if module.split(".")[0] in FOREIGN_PACKAGES
            ]
    return hits


def _ported_files() -> list[object]:
    """Lists one parameter per disposition file, or one that says no package is ported yet."""
    files = sorted(PORTED_ROOT.glob("*.toml"))
    if not files:
        return [pytest.param(None, id=NO_PORTED_PACKAGE)]
    return [pytest.param(path, id=path.stem) for path in files]


@pytest.mark.parametrize("path", _ported_files())
def test_ported_package_matches_its_disposition(path: Path | None) -> None:
    if path is None:
        return
    problems = disposition_problems(load_disposition(path), LIBRARY_ROOT)
    assert not problems, f"{path.name}: " + "; ".join(problems)


@pytest.mark.parametrize("path", _ported_files())
def test_ported_packages_import_no_scipy_or_shapely(path: Path | None) -> None:
    if path is None:
        return
    hits = foreign_imports(load_disposition(path), LIBRARY_ROOT)
    assert not hits, "import the core instead of scipy or shapely: " + ", ".join(hits)


def _write_package(library_root: Path, modules: dict[str, str]) -> None:
    package = library_root / "geo"
    package.mkdir(parents=True)
    for name, source in modules.items():
        (package / f"{name}.py").write_text(source, encoding="utf-8")


def test_disposition_check_flags_each_broken_rule(tmp_path: Path) -> None:
    library_root = tmp_path / "e8s"
    _write_package(
        library_root,
        {
            "affine": "def f(): ...\n",
            "regions": '"""Doc."""\nfrom e8s._core.geo import area\nimport numpy\n',
            "clock": '"""Doc."""\nfrom e8s._core.time import seconds\n',
            "models": "",
        },
    )
    disposition = Disposition(
        package="geo",
        moves=("affine",),
        reexport=("regions", "clock"),
        stays=("models", "models", "fonts"),
        interop=("e8s/geo/curve_base.py",),
    )
    assert disposition_problems(disposition, library_root) == [
        "models is listed twice",
        "affine moved but exists",
        "fonts is listed but missing",
        f"regions holds more than imports from {CORE_PACKAGE}",
        "e8s/geo/curve_base.py is interop but missing",
    ]


def test_foreign_import_scan_flags_imports_outside_interop(tmp_path: Path) -> None:
    library_root = tmp_path / "e8s"
    _write_package(
        library_root,
        {
            "models": "import scipy.optimize\nfrom shapely import Polygon\nimport numpy\n",
            "curve_base": "import shapely\n",
        },
    )
    disposition = Disposition(
        package="geo",
        moves=(),
        reexport=(),
        stays=("models", "curve_base"),
        interop=("e8s/geo/curve_base.py",),
    )
    assert foreign_imports(disposition, library_root) == [
        "e8s/geo/models.py:1 scipy.optimize",
        "e8s/geo/models.py:2 shapely",
    ]
