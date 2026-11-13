#!/usr/bin/env python3

"""
sandbox.py --- checks set scripts against a policy and runs them in an isolated subprocess

Three layers, strongest last. An AST policy rejects unsafe constructs. The script then runs
in a separate interpreter (`python -I`) with restricted builtins, an import allowlist, CPU and
memory limits, a wall-clock timeout and an empty environment. Finally, by default, that
interpreter runs under bubblewrap: no network, a read-only filesystem holding only the
interpreter, the virtualenv and the `stage` package, and a private /tmp. The Python layers
alone are defense in depth (numpy can read files, for example); bubblewrap is the boundary.
Without bubblewrap, running a script needs an explicit `isolation="process"`.

Contains:
    ALLOWED_MODULES: lists the module roots a script may import
    Isolation: names the two ways to run a script: `bwrap` (default) or bare `process`
    check_script(): rejects scripts that break the policy, naming the offending line
    script_hash(): hashes a script with the set it builds from, for staleness
    run_set_script(): builds one set by running a script's build(ctx) in a subprocess
    run_analysis_script(): runs a script's main(ctx) in a subprocess and captures its output
"""

import ast
import json
import resource
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import stage
from stage.choreo.models import Form, Set
from stage.core.errors import SchemaError, StorageError, TimeRangeError, ValidationError
from stage.core.ids import SetId
from stage.core.util import content_hash
from stage.show.build_models import BuildResult, ScriptResult
from stage.show.models import Show

ALLOWED_MODULES = frozenset(
    {"math", "itertools", "functools", "collections", "dataclasses", "typing", "numpy", "stage"}
)
BLOCKED_STAGE_MODULES = (
    "stage.show.storage",
    "stage.show.repositories",
    "stage.show.file_repository",
    "stage.show.sandbox",
    "stage.show.sandbox_child",
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
CPU_LIMIT_SIGNALS = (-signal.SIGXCPU, -signal.SIGKILL, 128 + signal.SIGXCPU, 128 + signal.SIGKILL)
BWRAP = "bwrap"
STDERR_IN_MESSAGE = 300
SYSTEM_DIRS = ("/usr", "/lib", "/lib64", "/bin", "/sbin")

type Isolation = Literal["bwrap", "process"]


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


def _bwrap_path() -> str | None:
    return shutil.which(BWRAP)


def _bind_args(path: str) -> list[str]:
    target = Path(path)
    if target.is_symlink():
        return ["--symlink", str(target.readlink()), path]
    return ["--ro-bind", path, path] if target.exists() else []


def _import_dirs() -> list[str]:
    """Lists the directories the child needs: prefixes, sys.path entries, the stage package.

    An entry that contains the stage package (the repo root, from an editable install) is
    replaced by the package directory itself, so the rest of the repo stays invisible.
    """
    package = Path(stage.__file__).resolve().parent
    wanted = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), package}
    for entry in sys.path:
        path = Path(entry or ".").resolve()
        if path.is_dir() and not package.is_relative_to(path):
            wanted.add(path)
    minimal = [p for p in wanted if not any(p != q and p.is_relative_to(q) for q in wanted)]
    return sorted(str(p) for p in minimal)


def _link_args(executable: str, bound: Sequence[str] = ()) -> list[str]:
    """Recreates every symlink met while resolving the interpreter path, hop by hop.

    uv points a venv's python through a symlinked version directory; binding only the
    resolved target would leave that path missing inside the sandbox. Symlinks inside an
    already-bound directory exist there already and are skipped.
    """
    roots = [Path(b) for b in bound]
    args: list[str] = []
    pending, seen = [Path(executable)], set()
    while pending:
        path = pending.pop()
        for prefix in (*reversed(path.parents), path):
            if prefix in seen or not prefix.is_symlink():
                continue
            seen.add(prefix)
            target = prefix.readlink()
            target = target if target.is_absolute() else prefix.parent / target
            if not any(prefix.parent.is_relative_to(root) for root in roots):
                args += ["--symlink", str(target), str(prefix)]
            pending.append(target)
    return args


def _bwrap_command(bwrap: str) -> list[str]:
    mounts = ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
    for path in SYSTEM_DIRS:
        mounts += _bind_args(path)
    bound = _import_dirs()
    for path in bound:
        mounts += ["--ro-bind", path, path]
    mounts += _link_args(sys.executable, [*bound, *SYSTEM_DIRS])
    flags = ["--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    return [bwrap, *mounts, *flags, "--"]


def _command(isolation: Isolation) -> list[str]:
    child = [sys.executable, "-I", "-m", CHILD_MODULE]
    if isolation == "process":
        return child
    bwrap = _bwrap_path()
    if bwrap is None:
        raise StorageError(
            "bubblewrap is not installed, so scripts cannot run isolated",
            hint="install it (apt install bubblewrap) or pass isolation='process' to run "
            "without filesystem and network isolation",
        )
    return [*_bwrap_command(bwrap), *child]


# Any: the child protocol exchanges JSON objects.
def _run_child(payload: dict[str, Any], timeout_s: float, isolation: Isolation) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            _command(isolation),
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
        tail = completed.stderr.strip()[-STDERR_IN_MESSAGE:]
        raise ValidationError(
            f"script process failed (exit {completed.returncode}): {tail}",
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


def script_hash(show: Show, set_id: SetId, source: str) -> str:
    """Hashes a set script together with the previous set it builds from.

    A set is up to date only while both its script and its input are unchanged.

    >>> from stage.examples import two_set_show
    >>> len(script_hash(two_set_show(), "2", "def build(ctx): return ctx.prev"))
    64
    """
    index = show.set_index(set_id)
    previous = show.sets[index - 1] if index > 0 else None
    return content_hash({"source": source, "input": content_hash(previous) if previous else ""})


def run_set_script(
    show: Show,
    set_id: SetId,
    source: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    isolation: Isolation = "bwrap",
) -> BuildResult:
    """Builds one set by running `build(ctx)` in an isolated subprocess.

    build(ctx) returns a Set with the requested id, or a list of Forms that the child places
    with stage.choreo.form_place.place; the result is stamped with the source hash.
    `isolation="process"` skips bubblewrap and must be chosen deliberately.
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
    reply = _run_child(payload, timeout_s, isolation)
    built = Set.model_validate(reply["set"]).model_copy(
        update={"source_hash": script_hash(show, set_id, source)}
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
    show: Show,
    source: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    isolation: Isolation = "bwrap",
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
    reply = _run_child(payload, timeout_s, isolation)
    duration = (time.perf_counter() - started) * 1000.0
    return ScriptResult(stdout=reply["stdout"], result=reply["result"], duration_ms=duration)
