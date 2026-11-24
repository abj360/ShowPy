#!/usr/bin/env python3

"""
check_no_fma.py --- refuses fused multiply-add instructions outside libm's fma in a built library

The compiled core must give the same bits on every platform. A fused multiply-add rounds once
where a multiply then an add round twice, so the only FMA allowed is the one the code asks for
through libm's correctly rounded `fma`. The library is disassembled with the llvm-objdump of the
pinned toolchain's llvm-tools. An FMA instruction passes only when its enclosing symbol is libm's
fma, or when the innermost function inlined at it is `fma` from libm's sources: libm's fma is
inlined into its callers, so its symbol alone does not cover it. The inlining records come from
the library's line tables; a library built without them fails on every inlined libm fma.

Contains:
    FMA_MNEMONIC: matches the aarch64 and x86 fused multiply-add mnemonics
    DisassemblyError: reports a library that could not be disassembled
    fma_outside_libm(): lists the FMA instructions of a disassembly that libm's fma did not emit
    count_instructions(): counts the instruction lines of a disassembly
    objdump_path(): locates llvm-objdump in the active Rust toolchain's llvm-tools
    check_no_fma(): disassembles a library and lists its FMA instructions outside libm's fma
    main(): checks one library, prints each finding as an E: line, exits 1 on any
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Scalar fmadd/fmsub/fnmadd/fnmsub and vector fmla/fmls (with fmlal/fmlsl) on aarch64, with
# Apple's `.2d` arrangement suffix, and the x86 vfmadd/vfmsub/vfnmadd/vfnmsub family.
FMA_MNEMONIC = re.compile(r"^(?:f(?:n?m(?:add|sub)|ml[as](?:l2?)?)|vfn?m(?:add|sub)\w*)(?:\.\w+)?$")
SYMBOL_LINE = re.compile(r"^[0-9a-f]+ <(?P<symbol>.+)>:$")
INLINE_LINE = re.compile(
    r"^; .+?:\d+:\d+: (?P<end>end of )?(?P<callee>.+) inlined into (?P<caller>.+)$"
)
SOURCE_LINE = re.compile(r"^; (?P<file>.+):\d+$")
INSTRUCTION_LINE = re.compile(
    r"^\s+(?P<address>[0-9a-f]+):\s+(?P<mnemonic>\S+)(?:\s+(?P<operands>.*?))?\s*$"
)
LIBM_FMA_SYMBOL = re.compile(r"^libm(?:\[[0-9a-f]+\])?::(?:\w+::)*fma(?:::h[0-9a-f]{16})?$")
LIBM_FMA_CALLEE = re.compile(r"^fma(?:<.+>)?$")
LIBM_SOURCE = re.compile(r"[/\\]libm-\d+\.\d+\.\d+[/\\]src[/\\]")
OBJDUMP_FLAGS = (
    "--disassemble",
    "--line-numbers",
    "--no-show-raw-insn",
    "--demangle",
    "--debug-inlined-funcs=limits-only",
)
OBJDUMP = "llvm-objdump"
LLVM_TOOLS_FIX = "rustup component add llvm-tools"
NO_SYMBOL = "?"
ERROR_EXIT = 2


class DisassemblyError(RuntimeError):
    """Reports a library that could not be disassembled, so the check cannot pass."""


@dataclass
class _Cursor:
    """Tracks where in the library the next instruction of a disassembly lies.

    Attributes:
        symbol: Demangled name of the enclosing symbol.
        source: Source file of the innermost frame, from the last line record.
        inlined: Open inlined calls as (callee, caller) pairs, innermost last.
    """

    symbol: str = NO_SYMBOL
    source: str = ""
    inlined: list[tuple[str, str]] = field(default_factory=list)


def _advance(cursor: _Cursor, line: str) -> bool:
    """Updates the cursor from a symbol, inlining or source line; returns whether it was one."""
    if symbol := SYMBOL_LINE.match(line):
        cursor.symbol, cursor.source = symbol["symbol"], ""
        cursor.inlined.clear()
        return True
    if inline := INLINE_LINE.match(line):
        frame = (inline["callee"], inline["caller"])
        if not inline["end"]:
            cursor.inlined.append(frame)
        elif frame in cursor.inlined:
            del cursor.inlined[len(cursor.inlined) - 1 - cursor.inlined[::-1].index(frame)]
        return True
    if source := SOURCE_LINE.match(line):
        cursor.source = source["file"]
        return True
    return False


def _inside_libm_fma(cursor: _Cursor) -> bool:
    """Tells whether the cursor's instruction was emitted by libm's fma."""
    if LIBM_FMA_SYMBOL.match(cursor.symbol):
        return True
    if not cursor.inlined:
        return False
    callee = cursor.inlined[-1][0]
    return bool(LIBM_FMA_CALLEE.match(callee) and LIBM_SOURCE.search(cursor.source))


def fma_outside_libm(disassembly: str) -> list[str]:
    """Lists the FMA instructions of an llvm-objdump disassembly that libm's fma did not emit.

    Args:
        disassembly: Output of llvm-objdump run with OBJDUMP_FLAGS.

    Returns:
        findings: One `<symbol> <address>: <instruction>` line per FMA outside libm's fma.
    """
    cursor = _Cursor()
    findings: list[str] = []
    for line in disassembly.splitlines():
        if _advance(cursor, line):
            continue
        instruction = INSTRUCTION_LINE.match(line)
        if not instruction or not FMA_MNEMONIC.match(instruction["mnemonic"]):
            continue
        if not _inside_libm_fma(cursor):
            text = f"{instruction['mnemonic']} {instruction['operands'] or ''}".rstrip()
            findings.append(f"{cursor.symbol} {instruction['address']}: {text}")
    return findings


def count_instructions(disassembly: str) -> int:
    """Counts the instruction lines of an llvm-objdump disassembly."""
    return sum(1 for line in disassembly.splitlines() if INSTRUCTION_LINE.match(line))


def _rustc_print(item: str) -> str:
    """Returns one `rustc --print` value of the toolchain active in the working directory."""
    command = ("rustc", "--print", item)
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def objdump_path() -> Path:
    """Locates llvm-objdump in the llvm-tools component of the active Rust toolchain.

    Returns:
        objdump: Path of the llvm-objdump binary.
    """
    sysroot, host = _rustc_print("sysroot"), _rustc_print("host-tuple")
    suffix = ".exe" if sys.platform == "win32" else ""
    objdump = Path(sysroot) / "lib" / "rustlib" / host / "bin" / f"{OBJDUMP}{suffix}"
    if not objdump.is_file():
        raise DisassemblyError(f"no {OBJDUMP} at {objdump}; fix: {LLVM_TOOLS_FIX}")
    return objdump


def check_no_fma(path: Path) -> list[str]:
    """Disassembles a built library and lists its FMA instructions outside libm's fma.

    Args:
        path: The library to check, built unstripped and with line tables.

    Returns:
        findings: One line per FMA instruction outside libm's fma; empty when there is none.
    """
    if not path.is_file():
        raise DisassemblyError(f"no library at {path}")
    command = (str(objdump_path()), *OBJDUMP_FLAGS, str(path))
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise DisassemblyError(f"{OBJDUMP} failed on {path}: {result.stderr.strip()}")
    if count_instructions(result.stdout) == 0:
        raise DisassemblyError(f"{OBJDUMP} found no instructions in {path}")
    return fma_outside_libm(result.stdout)


def main(argv: list[str] | None = None) -> int:
    """Checks one library, prints each finding as an E: line and returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument("library", type=Path, help="built library to disassemble")
    library: Path = parser.parse_args(argv).library
    try:
        findings = check_no_fma(library)
    except DisassemblyError as error:
        print(f"E: {error}", file=sys.stderr)
        return ERROR_EXIT
    for finding in findings:
        print(f"E: FMA outside libm::fma: {finding}", file=sys.stderr)
    if findings:
        return 1
    print(f"Checked {library.name}: no FMA outside libm::fma")
    return 0


if __name__ == "__main__":
    sys.exit(main())
