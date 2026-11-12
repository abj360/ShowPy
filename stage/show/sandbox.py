#!/usr/bin/env python3

"""
sandbox.py --- checks set scripts against a policy and runs them in an isolated subprocess

Defense in depth, not a security boundary: an AST policy rejects unsafe constructs, then the
script runs in a separate interpreter (`python -I`) with restricted builtins, an import
allowlist, CPU and memory limits, a wall-clock timeout and an empty environment. Scripts
can never import stagecheck (CLAUDE.md rule 4).

Contains:
    ALLOWED_MODULES: lists the module roots a script may import
    check_script(): rejects scripts that break the policy, naming the offending line
    run_set_script(): builds one set by running a script's build(ctx) in a subprocess
    run_analysis_script(): runs a script's main(ctx) in a subprocess and captures its output
"""

import ast
import json
import resource
import signal
import subprocess
import sys
import time
from typing import Any

from stage.choreo.models import Form, Set
from stage.core.errors import SchemaError, TimeRangeError, ValidationError
from stage.core.ids import SetId
from stage.core.util import content_hash
from stage.show.build_models import BuildResult, ScriptResult
from stage.show.models import Show

ALLOWED_MODULES = frozenset(
    {"math", "itertools", "functools", "collections", "dataclasses", "typing", "numpy", "stage"}
)
BLOCKED_STAGE_MODULES = (
    "stage.show.storage",
    "stage.show.sandbox",
    "stage.show.build",
    "stage.export.video",
)
FORBIDDEN_NAMES = frozenset(
    {
        "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars",
        "getattr", "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit",
        "memoryview", "__builtins__",
    }
)  # fmt: skip
CHILD_MODULE = "stage.show.sandbox_child"
DEFAULT_TIMEOUT_S = 60.0
MEMORY_LIMIT_BYTES = 2 * 1024**3
CPU_GRACE_S = 1
ENTRY_POINTS = {"set": "build", "analysis": "main"}
CPU_LIMIT_SIGNALS = (-signal.SIGXCPU, -signal.SIGKILL)


def _module_allowed(name: str) -> bool:
    if name.split(".")[0] not in ALLOWED_MODULES:
        return False
    return not any(name == b or name.startswith(b + ".") for b in BLOCKED_STAGE_MODULES)


def _reject(node: ast.AST, message: str, hint: str) -> None:
    line = getattr(node, "lineno", 0)
    raise SchemaError(f"line {line}: {message}", hint=hint, context={"line": line})


def _check_node(node: ast.AST) -> None:
    if isinstance(node, ast.Import | ast.ImportFrom):
        names = (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
        )
        for name in names:
            if name.split(".")[0] == "stagecheck":
                _reject(
                    node,
                    "scripts may not import stagecheck",
                    "run checks through the run_checks tool",
                )
            if not _module_allowed(name):
                _reject(
                    node,
                    f"import of {name!r} is not allowed",
                    f"import only from {sorted(ALLOWED_MODULES)}",
                )
    elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
        _reject(
            node,
            f"{node.id} is not allowed",
            "use the stage library instead of builtins that reach outside",
        )
    elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
        _reject(node, f"dunder attribute {node.attr} is not allowed", "use public attributes only")


def check_script(source: str, entry: str = ENTRY_POINTS["set"]) -> None:
    """Rejects scripts that break the policy; the script must define `entry(ctx)` at top level.

    >>> check_script("def build(ctx):" + chr(10) + "    return ctx.prev")
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise SchemaError(
            f"line {error.lineno}: {error.msg}",
            hint="fix the syntax",
            context={"line": error.lineno},
        ) from error
    for node in ast.walk(tree):
        _check_node(node)
    entries = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == entry]
    if not entries or len(entries[0].args.args) != 1:
        raise SchemaError(
            f"script must define {entry}(ctx)",
            hint=f"add a top-level def {entry}(ctx):",
            context={"line": 0},
        )


def _limits(timeout_s: float) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    cpu = int(timeout_s) + CPU_GRACE_S
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))


# Any: the child protocol exchanges JSON objects.
def _run_child(payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-m", CHILD_MODULE],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={},
            preexec_fn=lambda: _limits(timeout_s),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeRangeError(
            f"script ran longer than {timeout_s} s",
            hint="make the script faster or raise timeout_s",
            context={"timeout_s": timeout_s},
        ) from error
    if completed.returncode in CPU_LIMIT_SIGNALS:
        raise TimeRangeError(
            f"script used more than its CPU budget of {timeout_s} s",
            hint="make the script faster or raise timeout_s",
            context={"timeout_s": timeout_s},
        )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValidationError(
            "script process failed",
            hint="check the script for crashes or memory use",
            context={"stderr": completed.stderr[-1000:]},
        )
    reply: dict[str, Any] = json.loads(completed.stdout.splitlines()[-1])
    if not reply.get("ok"):
        raise ValidationError(
            str(reply.get("error")),
            hint="fix the script error shown",
            context={"logs": reply.get("logs", [])},
        )
    return reply


def run_set_script(
    show: Show, set_id: SetId, source: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> BuildResult:
    """Builds one set by running `build(ctx)` in an isolated subprocess.

    build(ctx) returns a Set with the requested id, or a list of Forms that the child places
    with stage.choreo.form_place.place; the result is stamped with the source hash.
    """
    check_script(source)
    show.set_index(set_id)
    started = time.perf_counter()
    payload = {
        "mode": "set",
        "show": show.model_dump(mode="json"),
        "set_id": set_id,
        "source": source,
    }
    reply = _run_child(payload, timeout_s)
    built = Set.model_validate(reply["set"]).model_copy(
        update={"source_hash": content_hash(source)}
    )
    if built.id != set_id:
        raise ValidationError(
            f"script built set {built.id}, expected {set_id}",
            hint="return a set with id ctx.set_id",
        )
    duration = (time.perf_counter() - started) * 1000.0
    forms = tuple(Form.model_validate(f) for f in reply.get("forms", []))
    return BuildResult(
        set_id=set_id, set=built, forms=forms, logs=tuple(reply["logs"]), duration_ms=duration
    )


def run_analysis_script(
    show: Show, source: str, timeout_s: float = DEFAULT_TIMEOUT_S
) -> ScriptResult:
    """Runs `main(ctx)` in an isolated subprocess and returns its JSON result and printed output."""
    check_script(source, ENTRY_POINTS["analysis"])
    started = time.perf_counter()
    payload = {
        "mode": "analysis",
        "show": show.model_dump(mode="json"),
        "set_id": None,
        "source": source,
    }
    reply = _run_child(payload, timeout_s)
    duration = (time.perf_counter() - started) * 1000.0
    return ScriptResult(stdout=reply["stdout"], result=reply["result"], duration_ms=duration)
