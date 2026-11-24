#!/usr/bin/env python3

"""
test_check_no_fma.py --- tests the gate that refuses fused multiply-adds outside libm's fma

The disassemblies under tests/fixtures/disassembly were recorded with the check's own flags by
the llvm-objdump of Rust 1.100.0's llvm-tools, from an arm64 cdylib built with line tables and
`--remap-path-prefix` to /home/dev. In it, `exact` calls `libm::fma`, `fused` calls
`f64::mul_add`, `vector` calls `vfmaq_f64`, `m::fma` is a function named fma outside libm and
`plain` multiplies then adds. The optimized build uses fat LTO, which inlines libm's fma into
`exact`; the unoptimized excerpt keeps libm's fma and `<f64>::mul_add` as their own symbols.

Contains:
    symbol_section(): returns the lines of one symbol of a disassembly
    test_flags_an_fma_outside_libm_fma: mul_add, a vector fmla and a non-libm `fma` are flagged
    test_accepts_an_fma_inside_libm_fma: libm's fma passes, inlined or as its own symbol
    test_inlined_libm_fma_needs_its_inlining_record: without line tables it is flagged
    test_libm_source_alone_does_not_pass_an_fma: an FMA in libm's sources but not in fma fails
    test_fma_mnemonic_matches_only_fused_multiply_adds: aarch64 and x86 FMA mnemonics, and no
        other instruction
    test_count_instructions_sees_only_instruction_lines: headers and records are not counted
    test_main_fails_closed_on_a_missing_library: no library is an error, never a pass
"""

from pathlib import Path

import pytest

from scripts.check_no_fma import (
    FMA_MNEMONIC,
    count_instructions,
    fma_outside_libm,
    main,
)

DISASSEMBLY = Path(__file__).resolve().parents[2] / "fixtures" / "disassembly"
OPTIMIZED = DISASSEMBLY / "fma-probe-optimized.txt"
UNOPTIMIZED = DISASSEMBLY / "fma-probe-unoptimized.txt"
OUTSIDE_LIBM_OPTIMIZED = [
    "fma_probe::m::fma 3c0: fmadd d0, d0, d1, d2",
    "_fused 418: fmadd d0, d0, d1, d2",
    "_vector 42c: fmla.2d v0, v2, v1",
]
OUTSIDE_LIBM_UNOPTIMIZED = ["<f64>::mul_add 8c0: fmadd d0, d0, d1, d2"]
INLINED_LIBM_FMA = "_exact"
LIBM_FMA_SYMBOL = "libm::math::arch::aarch64::fma"
FMA_MNEMONICS = (
    "fmadd",
    "fmsub",
    "fnmadd",
    "fnmsub",
    "fmla",
    "fmla.2d",
    "fmls",
    "fmlal2",
    "vfmadd231sd",
    "vfmsub132ps",
    "vfnmadd213pd",
    "vfnmsub231ss",
    "vfmaddsub132pd",
    "vfmsubadd231ps",
)
OTHER_MNEMONICS = ("fmul", "fadd", "fsub", "fnmul", "fminnm", "fmov", "mov.16b", "vmulsd", "ret")
ERROR_EXIT = 2


def symbol_section(disassembly: str, symbol: str) -> str:
    """Returns the lines of one symbol of a disassembly, from its label to the next blank line."""
    lines = disassembly.splitlines()
    start = lines.index(next(line for line in lines if line.endswith(f" <{symbol}>:")))
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return "\n".join(lines[start:end])


def test_flags_an_fma_outside_libm_fma() -> None:
    assert fma_outside_libm(OPTIMIZED.read_text(encoding="utf-8")) == OUTSIDE_LIBM_OPTIMIZED
    assert fma_outside_libm(UNOPTIMIZED.read_text(encoding="utf-8")) == OUTSIDE_LIBM_UNOPTIMIZED


def test_accepts_an_fma_inside_libm_fma() -> None:
    inlined = symbol_section(OPTIMIZED.read_text(encoding="utf-8"), INLINED_LIBM_FMA)
    own_symbol = symbol_section(UNOPTIMIZED.read_text(encoding="utf-8"), LIBM_FMA_SYMBOL)
    for section in (inlined, own_symbol):
        assert "\tfmadd\t" in section
        assert fma_outside_libm(section) == []


def test_inlined_libm_fma_needs_its_inlining_record() -> None:
    """Fails closed when the library has no line tables, rather than trusting the caller."""
    inlined = symbol_section(OPTIMIZED.read_text(encoding="utf-8"), INLINED_LIBM_FMA)
    without_records = "\n".join(line for line in inlined.splitlines() if not line.startswith(";"))
    assert fma_outside_libm(without_records) == ["_exact 410: fmadd d0, d0, d1, d2"]


def test_libm_source_alone_does_not_pass_an_fma() -> None:
    """Passes only libm's fma, not any function inlined from libm's sources."""
    inlined = symbol_section(OPTIMIZED.read_text(encoding="utf-8"), INLINED_LIBM_FMA)
    renamed = inlined.replace(": fma inlined into fma", ": sin inlined into fma").replace(
        "end of fma inlined into fma", "end of sin inlined into fma"
    )
    assert renamed != inlined
    assert fma_outside_libm(renamed) == ["_exact 410: fmadd d0, d0, d1, d2"]


@pytest.mark.parametrize("mnemonic", FMA_MNEMONICS)
def test_fma_mnemonic_matches_only_fused_multiply_adds(mnemonic: str) -> None:
    assert FMA_MNEMONIC.match(mnemonic)
    assert not any(FMA_MNEMONIC.match(other) for other in OTHER_MNEMONICS)


def test_count_instructions_sees_only_instruction_lines() -> None:
    inlined = symbol_section(OPTIMIZED.read_text(encoding="utf-8"), INLINED_LIBM_FMA)
    assert count_instructions(inlined) == 2
    assert count_instructions(OPTIMIZED.read_text(encoding="utf-8").split("Disassembly")[0]) == 0


def test_main_fails_closed_on_a_missing_library(tmp_path: Path) -> None:
    assert main([str(tmp_path / "lib_core.so")]) == ERROR_EXIT
