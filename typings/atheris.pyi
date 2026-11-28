"""
atheris.pyi --- type stub for the parts of atheris 3.1 that scripts/fuzz_storage.py calls

Atheris installs only on Linux x86_64, so checkers on other hosts have no package to read.

Contains:
    instrument_imports(): returns a context manager that adds libFuzzer coverage to the imports
    Setup(): registers the libFuzzer arguments and the function that takes each input
    Fuzz(): runs libFuzzer until its time limit or a crash
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

def instrument_imports(
    include: Sequence[str] | None = None, exclude: Sequence[str] | None = None
) -> AbstractContextManager[None]: ...
def Setup(args: Sequence[str], test_one_input: Callable[[bytes], None]) -> list[str]: ...
def Fuzz() -> None: ...
