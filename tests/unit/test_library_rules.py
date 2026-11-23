#!/usr/bin/env python3

"""
test_library_rules.py --- tests source-level rules that every e8s module must follow

Contains:
    find_model_copy_updates(): lists file and line of each unvalidated model_copy use
    test_library_never_calls_model_copy_with_update: fails on any such call in e8s/
    test_model_copy_ban_detects_an_offending_call: proves the scan can fail
    test_model_copy_ban_detects_kwargs_aliases_and_getattr: proves the scan sees evasions
    find_citations(): lists file and line of each ADR, ledger-ID or docs-path citation
    test_library_cites_no_adr_docs_or_ledger_ids: fails on any such citation in e8s/
    test_citation_scan_flags_a_docstring_and_a_comment: proves the scan can fail
    test_citation_scan_ignores_runtime_strings: proves plain string literals are not flagged
    find_code_spellings(): lists file, line and code of each raised or reported error code
    test_every_raised_code_is_catalogued: fails on a code that is not an ErrorCode member
    test_every_error_code_is_used: fails on an ErrorCode member no library module uses
    test_code_scan_flags_literals_and_unknown_members: proves the code scan can fail
    test_every_error_code_has_a_one_line_docstring: fails on a member without a docstring
    test_error_code_docs_are_current: fails when docs/error-codes.md differs from its generator
    find_positional_booleans(): lists file, line and parameter of each positional bool parameter
    test_regression_library_has_no_positional_boolean_parameter: fails on any such parameter
    test_regression_public_functions_take_booleans_by_keyword: fails when a public signature
        takes a bool positionally
    test_positional_boolean_scan_flags_a_definition: proves the scan can fail
    test_public_names_are_cached_after_first_use: requires a resolved name to land in the module
    find_geo_foreign_imports(): lists each import in geo source outside e8s.core and e8s.geo
    GEO_FORWARDING_IMPORTS: lists the function-level imports of the geo stubs, removed in v0.6.0
    test_geo_imports_only_core: fails when geo imports another e8s package, bar the listed stubs
    test_geo_layering_scan_allows_only_listed_function_imports: proves an unlisted import is flagged
    test_geo_layering_scan_flags_a_foreign_import: proves the layering scan can fail
    test_geo_layering_scan_resolves_relative_imports: checks `from ..cast import x` against layers
"""

import ast
import importlib.util
import inspect
import io
import re
import tokenize
import warnings
from pathlib import Path
from types import ModuleType

import pytest

import e8s
from e8s._api_table import PUBLIC_API
from e8s.core.deprecation import DeprecatedWarning
from e8s.core.error_codes import ErrorCode

CITATION_PATTERN = re.compile(r"\bADR\b|\bUP-\d+\.\d+-\d+\b|\bdocs/")
MIN_LIBRARY_FILES = 100
REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = REPO_ROOT / "e8s"
ERROR_CODES_PATH = LIBRARY_ROOT / "core" / "error_codes.py"
ERROR_CODE_DOCS_PATH = REPO_ROOT / "docs" / "error-codes.md"
ERROR_CODE_GENERATOR_PATH = REPO_ROOT / "scripts" / "gen_error_codes.py"
GEO_PACKAGE = "e8s.geo"
LITERAL = "literal"
MODEL_COPY = "model_copy"
MODEL_COPY_OVERRIDE = Path("core") / "types.py"
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no e8s tree"
)


def _is_model_copy(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == MODEL_COPY


def _is_getattr_of_model_copy(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and any(isinstance(arg, ast.Constant) and arg.value == MODEL_COPY for arg in node.args)
    )


def find_model_copy_updates(source: str, name: str) -> list[str]:
    """Lists `name:line` for each unvalidated `model_copy` use in the source.

    Flags a call that passes `update` or `**kwargs`, a `getattr(x, "model_copy")` and any
    reference that is not an immediate call, such as an alias or a callback.
    """
    tree = ast.parse(source)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    hits: set[int] = set()
    for node in (item for item in ast.walk(tree) if isinstance(item, ast.expr)):
        if _is_getattr_of_model_copy(node):
            hits.add(node.lineno)
        elif isinstance(node, ast.Call) and _is_model_copy(node.func):
            if any(keyword.arg in {"update", None} for keyword in node.keywords):
                hits.add(node.lineno)
        elif _is_model_copy(node) and id(node) not in called:
            hits.add(node.lineno)
    return [f"{name}:{line}" for line in sorted(hits)]


def test_library_never_calls_model_copy_with_update() -> None:
    """Requires every library edit to go through the revalidating FrozenModel.replace."""
    offenders = [
        hit
        for path in sorted(LIBRARY_ROOT.rglob("*.py"))
        if path.relative_to(LIBRARY_ROOT) != MODEL_COPY_OVERRIDE
        for hit in find_model_copy_updates(
            path.read_text(encoding="utf-8"), str(path.relative_to(LIBRARY_ROOT.parent))
        )
    ]
    assert not offenders, "model_copy(update=) skips validators; use .replace(): " + ", ".join(
        offenders
    )


def test_model_copy_ban_detects_an_offending_call() -> None:
    """Reports the line of an update call and ignores a plain copy."""
    source = "a = x.model_copy()\nb = x.model_copy(update={'k': 1})\n"
    assert find_model_copy_updates(source, "m.py") == ["m.py:2"]


@pytest.mark.parametrize(
    "source",
    [
        "x.model_copy(**changes)\n",
        "copy = x.model_copy\ncopy(update={})\n",
        "run(x.model_copy)\n",
        "getattr(x, 'model_copy')(update={})\n",
    ],
)
def test_model_copy_ban_detects_kwargs_aliases_and_getattr(source: str) -> None:
    """Reports a spread, an alias, a callback and a getattr lookup of `model_copy`."""
    assert find_model_copy_updates(source, "m.py")


def find_citations(source: str, name: str) -> list[str]:
    """Lists `name:line` for each ADR, ledger-ID or docs-path citation in a docstring or comment."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if doc is None or CITATION_PATTERN.search(doc) is None:
            continue
        first = node.body[0]
        for offset, text in enumerate(doc.splitlines()):
            if CITATION_PATTERN.search(text):
                hits.append(f"{name}:{first.lineno + offset}")
    hits.extend(
        f"{name}:{token.start[0]}"
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and CITATION_PATTERN.search(token.string)
    )
    return sorted(hits)


def test_library_cites_no_adr_docs_or_ledger_ids() -> None:
    """Requires docstrings and comments to state the rule, never point at an ADR or ledger ID."""
    paths = sorted(LIBRARY_ROOT.rglob("*.py"))
    assert len(paths) >= MIN_LIBRARY_FILES, f"scan found only {len(paths)} library files"
    offenders = [
        hit
        for path in paths
        for hit in find_citations(
            path.read_text(encoding="utf-8"), str(path.relative_to(LIBRARY_ROOT.parent))
        )
    ]
    assert not offenders, "state the rule itself instead of citing it: " + ", ".join(offenders)


def test_citation_scan_flags_a_docstring_and_a_comment() -> None:
    """Reports a planted ADR in a docstring, a ledger ID in a comment and a docs path."""
    source = (
        '"""Module.\n\nSee ADR 0003.\n"""\n'
        "# bug UP-1.2-03\n"
        "def f() -> None:\n"
        '    """Reads docs/notes.md."""\n'
    )
    assert find_citations(source, "m.py") == ["m.py:3", "m.py:5", "m.py:7"]


def test_citation_scan_ignores_runtime_strings() -> None:
    """Leaves a plain string literal alone, since only docstrings and comments are scanned."""
    assert find_citations('MESSAGE = "see docs/guide for ADR 5"\n', "m.py") == []


def _assigned_code(node: ast.AST) -> ast.expr | None:
    """Returns the value given to a `code=` keyword or a `default_code` assignment, if any."""
    if isinstance(node, ast.keyword) and node.arg == "code":
        return node.value
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "default_code" for target in node.targets
    ):
        return node.value
    return None


def find_code_spellings(source: str, name: str) -> list[tuple[str, str, str]]:
    """Lists (`name:line`, how, code) for each error code spelled in the source.

    A code is spelled as a string literal passed as `code=` or assigned to `default_code`
    (how is `literal`), or as an `ErrorCode.<NAME>` attribute (how is `member`).
    """
    hits: list[tuple[str, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        literal = _assigned_code(node)
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
            hits.append((f"{name}:{literal.lineno}", LITERAL, literal.value))
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ErrorCode"
        ):
            hits.append((f"{name}:{node.lineno}", "member", node.attr))
    return sorted(hits)


def _library_code_spellings() -> list[tuple[str, str, str]]:
    return [
        hit
        for path in sorted(LIBRARY_ROOT.rglob("*.py"))
        if path != ERROR_CODES_PATH
        for hit in find_code_spellings(
            path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT))
        )
    ]


def test_every_raised_code_is_catalogued() -> None:
    """Requires every raised or reported code to be spelled as a member of ErrorCode."""
    members = {code.name for code in ErrorCode}
    offenders = [
        f"{where} {code}"
        for where, how, code in _library_code_spellings()
        if how == LITERAL or code not in members
    ]
    assert not offenders, "spell each code as an ErrorCode member: " + ", ".join(offenders)


def test_every_error_code_is_used() -> None:
    """Requires every ErrorCode member to be raised or reported somewhere in the library."""
    used = {code for _, _, code in _library_code_spellings()}
    unused = sorted(code.name for code in ErrorCode if code.name not in used)
    assert not unused, "remove codes the library never raises: " + ", ".join(unused)


def test_code_scan_flags_literals_and_unknown_members() -> None:
    """Reports a literal `code=`, a literal `default_code` and an ErrorCode attribute."""
    source = (
        "class E(E8sError):\n"
        "    default_code = 'OLD'\n"
        "raise E('m', hint='h', code='NOPE')\n"
        "raise E('m', hint='h', code=ErrorCode.MADE_UP)\n"
    )
    assert find_code_spellings(source, "m.py") == [
        ("m.py:2", LITERAL, "OLD"),
        ("m.py:3", LITERAL, "NOPE"),
        ("m.py:4", "member", "MADE_UP"),
    ]


def _load_code_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_error_codes", ERROR_CODE_GENERATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_error_code_has_a_one_line_docstring() -> None:
    meanings: dict[str, str] = _load_code_generator().code_meanings()
    assert sorted(meanings) == sorted(code.name for code in ErrorCode)
    assert all(meaning and "\n" not in meaning for meaning in meanings.values())


def test_error_code_docs_are_current() -> None:
    """Requires docs/error-codes.md to be the generator's output, byte for byte."""
    rendered: str = _load_code_generator().render_docs()
    assert rendered == ERROR_CODE_DOCS_PATH.read_text(encoding="utf-8"), (
        "E: docs/error-codes.md differs from ErrorCode; "
        "run: uv run python scripts/gen_error_codes.py"
    )


def _is_boolean_annotation(annotation: ast.expr | None) -> bool:
    """Tells whether an annotation is `bool` or a union that contains it."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _is_boolean_annotation(annotation.left) or _is_boolean_annotation(annotation.right)
    return isinstance(annotation, ast.Name) and annotation.id == "bool"


def find_positional_booleans(source: str, name: str) -> list[str]:
    """Lists `name:line:parameter` for each positional parameter annotated as a bool."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        found.extend(
            f"{name}:{arg.lineno}:{arg.arg}"
            for arg in (*node.args.posonlyargs, *node.args.args)
            if _is_boolean_annotation(arg.annotation)
        )
    return found


def test_regression_library_has_no_positional_boolean_parameter() -> None:
    """Requires every bool parameter under e8s/ and scripts/ to be keyword-only."""
    paths = [*LIBRARY_ROOT.rglob("*.py"), *(REPO_ROOT / "scripts").glob("*.py")]
    offenders = [
        hit
        for path in paths
        for hit in find_positional_booleans(path.read_text(encoding="utf-8"), str(path))
    ]
    assert not offenders, f"E: positional bool parameters: {offenders}"


def test_regression_public_functions_take_booleans_by_keyword() -> None:
    """Requires no public function or method to accept a bool positionally."""
    offenders: list[str] = []
    for public_name, _module, _attribute in PUBLIC_API:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecatedWarning)
            obj = getattr(e8s, public_name)
        targets = [obj] if inspect.isfunction(obj) else []
        if inspect.isclass(obj):
            targets = [
                member
                for _, member in inspect.getmembers(obj, inspect.isfunction)
                if member.__qualname__.startswith(obj.__name__ + ".")
            ]
        for target in targets:
            offenders.extend(
                f"{public_name}:{target.__qualname__}:{parameter.name}"
                for parameter in inspect.signature(target).parameters.values()
                if parameter.annotation in (bool, "bool")
                and parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            )
    assert not offenders, f"E: positional bool parameters: {offenders}"


def test_positional_boolean_scan_flags_a_definition() -> None:
    """Shows the scan reports a positional bool and accepts a keyword-only one."""
    assert find_positional_booleans("def f(a: int, force: bool = False): ...", "m") == ["m:1:force"]
    assert find_positional_booleans("def f(a: int, *, force: bool = False): ...", "m") == []
    assert find_positional_booleans("def f(a: int, test: Callable[[int], bool]): ...", "m") == []


def test_public_names_are_cached_after_first_use() -> None:
    if "Show" in vars(e8s):
        delattr(e8s, "Show")
    resolved = e8s.Show
    assert vars(e8s)["Show"] is resolved


def _imported_modules(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Returns the absolute modules an import names, resolving relative ones against `package`."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        return [node.module] if node.module else []
    parts = package.split(".")
    base = ".".join(parts[: len(parts) - node.level + 1])
    if node.module:
        return [f"{base}.{node.module}"]
    return [f"{base}.{alias.name}" for alias in node.names]


def _imports_in_functions(
    node: ast.AST, function: str | None = None
) -> list[tuple[ast.Import | ast.ImportFrom, str | None]]:
    """Lists each import under a node with the name of its innermost enclosing function."""
    found: list[tuple[ast.Import | ast.ImportFrom, str | None]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Import | ast.ImportFrom):
            found.append((child, function))
        inner = (
            child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else function
        )
        found.extend(_imports_in_functions(child, inner))
    return found


def find_geo_foreign_imports(
    source: str,
    name: str,
    package: str = GEO_PACKAGE,
    allowed: frozenset[tuple[str, str, str]] = frozenset(),
) -> list[str]:
    """Lists `name:line:module` for each import outside e8s.core and e8s.geo.

    Args:
        source: The text of a geo module.
        name: The label to report for the module.
        package: The package the module belongs to, for resolving relative imports.
        allowed: (module label, imported module, function name) triples that may import upward
            inside that function; a module-level import is never allowed.

    Returns:
        offenders: One entry per offending runtime or TYPE_CHECKING import.
    """
    found: list[str] = []
    for node, function in _imports_in_functions(ast.parse(source)):
        found.extend(
            f"{name}:{node.lineno}:{module}"
            for module in _imported_modules(node, package)
            if module.split(".")[0] == "e8s"
            and module not in ("e8s.core", "e8s.geo")
            and not module.startswith(("e8s.core.", "e8s.geo."))
            and (function is None or (name, module, function) not in allowed)
        )
    return sorted(found, key=lambda entry: int(entry.split(":")[1]))


GEO_FORWARDING_FUNCTIONS = {
    ("transforms.py", "e8s.choreo.set_transforms"): (
        "shift",
        "move_to",
        "rotate",
        "scale",
        "expand",
        "contract",
        "set_interval",
        "mirror",
        "symmetrize",
        "skew",
        "affine",
    ),
    ("snapping.py", "e8s.choreo.set_snapping"): (
        "snap",
        "snap_to_guides",
        "align",
        "distribute",
    ),
    ("measure.py", "e8s.choreo.set_measure"): (
        "on_field_ids",
        "neighbors",
        "nearest",
        "symmetry_error",
    ),
    ("analysis.py", "e8s.choreo.set_analysis"): (
        "density_map",
        "coverage",
        "balance",
        "symmetry_score",
        "change_amount",
    ),
}
SHEET_FORWARDING_FUNCTIONS = ("snap_to_guides", "density_map", "coverage", "balance")
# Deprecated Set forwarding from geo to choreo; delete this table when v0.6.0 removes the stubs.
GEO_FORWARDING_IMPORTS = frozenset(
    [
        (module, target, function)
        for (module, target), functions in GEO_FORWARDING_FUNCTIONS.items()
        for function in functions
    ]
    + [
        (module, "e8s.choreo.models", function)
        for (module, _target), functions in GEO_FORWARDING_FUNCTIONS.items()
        for function in functions
    ]
    + [
        (module, "e8s.sheet.models", function)
        for (module, _target), functions in GEO_FORWARDING_FUNCTIONS.items()
        for function in functions
        if function in SHEET_FORWARDING_FUNCTIONS
    ]
)


def test_geo_imports_only_core() -> None:
    offenders = [
        offender
        for path in sorted((LIBRARY_ROOT / "geo").glob("*.py"))
        for offender in find_geo_foreign_imports(
            path.read_text(encoding="utf-8"), path.name, allowed=GEO_FORWARDING_IMPORTS
        )
    ]
    assert offenders == []


def test_geo_layering_scan_allows_only_listed_function_imports() -> None:
    source = (
        "def shift(obj):\n"
        "    from e8s.choreo.set_transforms import shift as twin\n"
        "    return twin(obj)\n"
        "def stray(obj):\n"
        "    from e8s.choreo.set_transforms import shift as twin\n"
        "    return twin(obj)\n"
        "def shift_other(obj):\n"
        "    from e8s.choreo.set_snapping import snap as twin\n"
        "    return twin(obj)\n"
        "from e8s.choreo.set_transforms import shift\n"
    )
    allowed = frozenset({("x.py", "e8s.choreo.set_transforms", "shift")})
    assert find_geo_foreign_imports(source, "x.py", allowed=allowed) == [
        "x.py:5:e8s.choreo.set_transforms",
        "x.py:8:e8s.choreo.set_snapping",
        "x.py:10:e8s.choreo.set_transforms",
    ]


def test_geo_layering_scan_flags_a_foreign_import() -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "from e8s.core.types import Box2\n"
        "if TYPE_CHECKING:\n"
        "    from e8s.cast.models import Spot\n"
        "import e8s.choreo.models\n"
    )
    assert find_geo_foreign_imports(source, "x.py") == [
        "x.py:4:e8s.cast.models",
        "x.py:5:e8s.choreo.models",
    ]


def test_geo_layering_scan_resolves_relative_imports() -> None:
    source = (
        "from . import curves\n"
        "from .affine import compose\n"
        "from ..core.types import Box2\n"
        "from ..cast.models import Spot\n"
        "from .. import choreo\n"
    )
    assert find_geo_foreign_imports(source, "x.py") == [
        "x.py:4:e8s.cast.models",
        "x.py:5:e8s.choreo",
    ]
