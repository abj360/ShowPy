#!/usr/bin/env python3

"""
test_library_rules.py --- tests source-level rules that every showpy module must follow

Contains:
    find_model_copy_updates(): lists file and line of each model_copy call that passes update
    test_library_never_calls_model_copy_with_update: fails on any such call in showpy/
    test_model_copy_ban_detects_an_offending_call: proves the scan can fail
    find_citations(): lists file and line of each ADR, ledger-ID or docs-path citation
    test_library_cites_no_adr_docs_or_ledger_ids: fails on any such citation in showpy/
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
"""

import ast
import importlib.util
import inspect
import io
import re
import tokenize
from pathlib import Path
from types import ModuleType

import pytest

import showpy
from showpy._api_table import PUBLIC_API
from showpy.core.error_codes import ErrorCode

CITATION_PATTERN = re.compile(r"\bADR\b|\bUP-\d+\.\d+-\d+\b|\bdocs/")
MIN_LIBRARY_FILES = 100
REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_ROOT = REPO_ROOT / "showpy"
ERROR_CODES_PATH = LIBRARY_ROOT / "core" / "error_codes.py"
ERROR_CODE_DOCS_PATH = REPO_ROOT / "docs" / "error-codes.md"
ERROR_CODE_GENERATOR_PATH = REPO_ROOT / "scripts" / "gen_error_codes.py"
LITERAL = "literal"
pytestmark = pytest.mark.skipif(
    not LIBRARY_ROOT.is_dir(), reason="needs a source checkout; the wheel test has no showpy tree"
)


def find_model_copy_updates(source: str, name: str) -> list[str]:
    """Lists `name:line` for each `model_copy(update=...)` call in the source."""
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
        and any(keyword.arg == "update" for keyword in node.keywords)
    ]


def test_library_never_calls_model_copy_with_update() -> None:
    """Requires every library edit to go through the revalidating FrozenModel.replace."""
    offenders = [
        hit
        for path in sorted(LIBRARY_ROOT.rglob("*.py"))
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
        "class E(ShowPyError):\n"
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
    """Requires every bool parameter under showpy/ and scripts/ to be keyword-only."""
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
        obj = getattr(showpy, public_name)
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
