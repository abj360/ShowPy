#!/usr/bin/env python3

"""
sandbox_child.py --- runs one script inside the isolated build subprocess

Reads a JSON request on stdin, executes the script with restricted builtins and an import
allowlist, and writes one JSON reply line on stdout. Only sandbox.py starts this module.

Contains:
    SAFE_BUILTINS: lists the builtins a script may use
    guarded_import(): imports a module only when the sandbox policy allows it
    execute(): runs a request and returns the reply object
    main(): reads stdin, executes, writes the reply
"""

import builtins
import contextlib
import io
import json
import sys
import traceback
from collections.abc import Mapping
from functools import partial
from types import ModuleType
from typing import Any

from stage.cast.select import select
from stage.choreo.engine import PathEngine
from stage.choreo.form_place import place
from stage.choreo.models import Form, Set
from stage.show.build_models import BuildContext
from stage.show.models import Show
from stage.show.sandbox import _module_allowed

SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "frozenset",
    "int", "isinstance", "len", "list", "map", "max", "min", "next", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "Exception",
    "ValueError", "TypeError", "KeyError", "IndexError", "ZeroDivisionError", "StopIteration",
    "True", "False", "None", "__build_class__", "object", "property", "staticmethod",
    "classmethod", "super",
)  # fmt: skip
MAX_STDOUT = 20_000


# Any: mirrors the signature of builtins.__import__.
def guarded_import(
    name: str,
    globals_: Mapping[str, Any] | None = None,
    locals_: Mapping[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> ModuleType:
    """Imports a module only when the sandbox policy allows it."""
    if level != 0 or not _module_allowed(name):
        raise ImportError(f"import of {name!r} is not allowed in set scripts")
    return builtins.__import__(name, globals_, locals_, fromlist, level)


def _context(show: Show, set_id: str | None) -> BuildContext:
    index = show.set_index(set_id) if set_id is not None else len(show.sets)
    prev = show.sets[index - 1] if index > 0 else None
    return BuildContext(
        show=show,
        prev=prev,
        set_id=set_id or "",
        counts=show.set(set_id).counts if set_id is not None else 0.0,
        surface=show.sheet,
        timeline=show.timeline,
        cast=show.cast,
        select=partial(select, show),
        engine=PathEngine(show),
    )


# Any: requests and replies are JSON objects.
def execute(request: Mapping[str, Any]) -> dict[str, Any]:
    """Runs a request and returns the reply object; script errors become ok=False."""
    show = Show.model_validate(request["show"])
    ctx = _context(show, request.get("set_id"))
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS}
    namespace: dict[str, Any] = {
        "__builtins__": {**safe, "__import__": guarded_import},
        "__name__": "set_script",
    }
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            exec(compile(request["source"], "<set script>", "exec"), namespace)
            entry = "build" if request["mode"] == "set" else "main"
            result = namespace[entry](ctx)
    except Exception as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        return {
            "ok": False,
            "error": f"{type(error).__name__}: {error} (line {frame.lineno})",
            "logs": ctx.logs,
        }
    return _reply(request["mode"], result, ctx, printed.getvalue())


def _reply(mode: str, result: object, ctx: BuildContext, stdout: str) -> dict[str, Any]:
    if mode == "analysis":
        return {"ok": True, "result": result, "stdout": stdout[:MAX_STDOUT], "logs": ctx.logs}
    forms: list[Form] = []
    if isinstance(result, list) and result and all(isinstance(f, Form) for f in result):
        forms = [f.model_copy(update={"set": ctx.set_id}) for f in result]
        result = place(ctx.show, ctx.set_id, forms)
    if not isinstance(result, Set):
        return {
            "ok": False,
            "error": f"build(ctx) returned {type(result).__name__}, not a Set or list of Forms",
            "logs": ctx.logs,
        }
    dumped = [f.model_dump(mode="json") for f in forms]
    return {"ok": True, "set": result.model_dump(mode="json"), "forms": dumped, "logs": ctx.logs}


def main() -> None:
    """Reads one JSON request from stdin and writes one JSON reply line to stdout."""
    reply = execute(json.loads(sys.stdin.read()))
    sys.stdout.write(json.dumps(reply, default=str) + "\n")


if __name__ == "__main__":
    main()
