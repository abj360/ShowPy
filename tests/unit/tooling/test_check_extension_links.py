#!/usr/bin/env python3

"""
test_check_extension_links.py --- tests the gate that refuses an rpath or libpython in the wheel

The dumps are excerpts in the formats of `otool -l` (macOS) and `readelf -d` (Linux). The
regression test checks the extension the checkout's own build installed, which maturin builds
the way it builds a wheel.

Contains:
    test_macho_links_reads_rpaths_and_loaded_libraries: LC_RPATH paths and LC_LOAD_DYLIB names
    test_elf_links_reads_rpaths_and_needed_libraries: RPATH, RUNPATH and NEEDED entries
    test_link_findings_flag_each_rpath_and_each_libpython: libSystem passes, libpython does not
    test_extension_from_wheel_needs_exactly_one_extension: a wheel without one cannot pass
    test_main_fails_closed_on_a_wheel_without_an_extension: exit 2, never a pass
    test_regression_built_extension_has_no_rpath_and_no_libpython: the checkout's extension is
        clean, so the test-only rpath and libpython never reach a wheel
"""

import sys
import zipfile
from pathlib import Path

import pytest

from scripts.check_extension_links import (
    HOST_TOOLS,
    LinkCheckError,
    check_extension,
    elf_links,
    extension_from_wheel,
    link_findings,
    macho_links,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MACHO_DUMP = """\
_core.abi3.so:
Load command 4
          cmd LC_ID_DYLIB
      cmdsize 56
         name @rpath/e8s._core.abi3.so (offset 24)
Load command 11
          cmd LC_LOAD_DYLIB
      cmdsize 56
         name /usr/lib/libSystem.B.dylib (offset 24)
Load command 12
          cmd LC_LOAD_DYLIB
      cmdsize 96
         name /opt/python/lib/libpython3.14.dylib (offset 24)
Load command 13
          cmd LC_RPATH
      cmdsize 48
         path /opt/python/lib (offset 12)
"""
ELF_DUMP = """\
Dynamic section at offset 0x2dd8 contains 27 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libgcc_s.so.1]
 0x0000000000000001 (NEEDED)             Shared library: [libpython3.14.so.1.0]
 0x000000000000000f (RPATH)              Library rpath: [/opt/python/lib]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN/lib]
 0x000000000000000e (SONAME)             Library soname: [_core.abi3.so]
"""


def test_macho_links_reads_rpaths_and_loaded_libraries() -> None:
    assert macho_links(MACHO_DUMP) == (
        ["/opt/python/lib"],
        ["/usr/lib/libSystem.B.dylib", "/opt/python/lib/libpython3.14.dylib"],
    )


def test_elf_links_reads_rpaths_and_needed_libraries() -> None:
    assert elf_links(ELF_DUMP) == (
        ["/opt/python/lib", "$ORIGIN/lib"],
        ["libgcc_s.so.1", "libpython3.14.so.1.0"],
    )


def test_link_findings_flag_each_rpath_and_each_libpython() -> None:
    libraries = [
        "/usr/lib/libSystem.B.dylib",
        "libpython3.14.so.1.0",
        "/Library/Frameworks/Python.framework/Versions/3.14/Python",
    ]
    assert link_findings(["/opt/python/lib"], libraries) == [
        "rpath /opt/python/lib",
        "links libpython3.14.so.1.0",
        "links /Library/Frameworks/Python.framework/Versions/3.14/Python",
    ]
    assert link_findings([], ["/usr/lib/libSystem.B.dylib", "libgcc_s.so.1"]) == []


def _wheel(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"")
    return path


def test_extension_from_wheel_needs_exactly_one_extension(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path / "e8s.whl", ["e8s/__init__.py", "e8s/_core.abi3.so"])
    assert extension_from_wheel(wheel, tmp_path / "out").name == "_core.abi3.so"
    empty = _wheel(tmp_path / "empty.whl", ["e8s/__init__.py"])
    with pytest.raises(LinkCheckError, match=r"holds 0 e8s\._core extensions"):
        extension_from_wheel(empty, tmp_path / "out")


def test_main_fails_closed_on_a_wheel_without_an_extension(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if sys.platform not in HOST_TOOLS:
        pytest.skip(f"{sys.platform} has no rpath; the check does not run there")
    assert main([str(_wheel(tmp_path / "empty.whl", ["e8s/__init__.py"]))]) == 2
    assert capsys.readouterr().out.startswith("E: ")


def test_regression_built_extension_has_no_rpath_and_no_libpython() -> None:
    """The test binaries link libpython with an rpath; the built extension must carry neither."""
    if sys.platform not in HOST_TOOLS:
        pytest.skip(f"{sys.platform} has no rpath; the check does not run there")
    built = sorted((REPO_ROOT / "e8s").glob("_core.*.so"))
    if not built:
        pytest.skip("needs the checkout's own build; the installed wheel has no e8s tree")
    assert [check_extension(extension) for extension in built] == [[] for _ in built]
