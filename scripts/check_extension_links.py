#!/usr/bin/env python3

"""
check_extension_links.py --- refuses an rpath or a linked libpython in a wheel's compiled core

A wheel's extension must take the Python symbols from the interpreter that loads it and must
find nothing through a search path baked in on the build machine. The test binaries link
libpython and carry an rpath to it; a wheel that did the same would load a second Python, or
none, on any other machine. On macOS the extension's load commands come from `otool -l`; on
Linux its dynamic section comes from `readelf -d`. Windows has no rpath, and an abi3 extension
links python3.dll there by design, so the check does not run on it.

Contains:
    macho_links(): lists the rpaths and linked libraries of an `otool -l` dump
    elf_links(): lists the rpaths and linked libraries of a `readelf -d` dump
    link_findings(): names each rpath and each linked libpython
    extension_from_wheel(): extracts a wheel's e8s._core extension into a folder
    check_extension(): dumps one extension with the host's tool and lists its findings
    main(): checks the extension of one wheel, exits 1 on a finding and 2 when it cannot check
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MACHO_COMMAND = re.compile(r"^\s*cmd (?P<command>\S+)\s*$")
MACHO_VALUE = re.compile(r"^\s*(?:path|name) (?P<value>.+?) \(offset \d+\)\s*$")
MACHO_RPATH = "LC_RPATH"
MACHO_LIBRARIES = frozenset({"LC_LOAD_DYLIB", "LC_LOAD_WEAK_DYLIB", "LC_REEXPORT_DYLIB"})
ELF_ENTRY = re.compile(r"^\s*0x[0-9a-f]+ \((?P<tag>\w+)\)\s+.*?\[(?P<value>.*)\]\s*$")
ELF_RPATHS = frozenset({"RPATH", "RUNPATH"})
ELF_LIBRARY = "NEEDED"
LIBPYTHON = re.compile(r"(?:^|/)(?:libpython\d|Python\.framework/|Python$)")
EXTENSION = re.compile(r"^e8s/_core\.[^/]*(?:\.so|\.pyd)$")
HOST_TOOLS = {"darwin": ("otool", "-l"), "linux": ("readelf", "-d")}
TOOL_FIXES = {"otool": "xcode-select --install", "readelf": "apt-get install binutils"}
ERROR_EXIT = 2


class LinkCheckError(RuntimeError):
    """Reports an extension that could not be checked, so the check cannot pass."""


def macho_links(dump: str) -> tuple[list[str], list[str]]:
    """Lists the rpaths and linked libraries in the load commands of an `otool -l` dump.

    Args:
        dump: The output of `otool -l` for one Mach-O file.

    Returns:
        links: The LC_RPATH paths, then the paths of the libraries the file loads.
    """
    rpaths: list[str] = []
    libraries: list[str] = []
    command = ""
    for line in dump.splitlines():
        if found := MACHO_COMMAND.match(line):
            command = found["command"]
        elif found := MACHO_VALUE.match(line):
            if command == MACHO_RPATH:
                rpaths.append(found["value"])
            elif command in MACHO_LIBRARIES:
                libraries.append(found["value"])
    return rpaths, libraries


def elf_links(dump: str) -> tuple[list[str], list[str]]:
    """Lists the rpaths and linked libraries in the dynamic section of a `readelf -d` dump.

    Args:
        dump: The output of `readelf -d` for one ELF file.

    Returns:
        links: The RPATH and RUNPATH entries, then the NEEDED libraries.
    """
    rpaths: list[str] = []
    libraries: list[str] = []
    for line in dump.splitlines():
        found = ELF_ENTRY.match(line)
        if found is None:
            continue
        if found["tag"] in ELF_RPATHS:
            rpaths.append(found["value"])
        elif found["tag"] == ELF_LIBRARY:
            libraries.append(found["value"])
    return rpaths, libraries


def link_findings(rpaths: list[str], libraries: list[str]) -> list[str]:
    """Names each rpath and each linked libpython of one extension."""
    found = [f"rpath {path}" for path in rpaths]
    return found + [f"links {library}" for library in libraries if LIBPYTHON.search(library)]


def extension_from_wheel(wheel: Path, folder: Path) -> Path:
    """Extracts the one e8s._core extension of a wheel into a folder and returns its path."""
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if EXTENSION.match(name)]
        if len(names) != 1:
            raise LinkCheckError(f"{wheel.name} holds {len(names)} e8s._core extensions, not 1")
        return Path(archive.extract(names[0], folder))


def check_extension(extension: Path, platform: str = sys.platform) -> list[str]:
    """Dumps one extension with the host's tool and lists its findings.

    Args:
        extension: The compiled e8s._core file.
        platform: The host, as `sys.platform` names it; it picks the tool and the dump format.

    Returns:
        findings: One line per rpath and per linked libpython; empty when the file is clean.
    """
    tool, flag = HOST_TOOLS[platform]
    executable = shutil.which(tool)
    if executable is None:
        raise LinkCheckError(f"{tool} not found; run: {TOOL_FIXES[tool]}")
    dumped = subprocess.run(
        [executable, flag, str(extension)], capture_output=True, encoding="utf-8", check=False
    )
    if dumped.returncode != 0 or not dumped.stdout.strip():
        raise LinkCheckError(f"{tool} {flag} failed on {extension.name}: {dumped.stderr.strip()}")
    links = macho_links if platform == "darwin" else elf_links
    rpaths, libraries = links(dumped.stdout)
    if not libraries:
        raise LinkCheckError(f"{tool} {flag} listed no linked library for {extension.name}")
    return link_findings(rpaths, libraries)


def main(argv: list[str] | None = None) -> int:
    """Checks the e8s._core extension of one wheel.

    Args:
        argv: Command-line arguments; defaults to sys.argv.

    Returns:
        status: 0 when clean or on a host without rpaths, 1 on a finding, 2 when it cannot check.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="the built wheel")
    args = parser.parse_args(argv)
    if sys.platform not in HOST_TOOLS:
        print(f"Skipping link check: {sys.platform} has no rpath")
        return 0
    with tempfile.TemporaryDirectory() as folder:
        try:
            extension = extension_from_wheel(args.wheel, Path(folder))
            findings = check_extension(extension)
        except (LinkCheckError, OSError, zipfile.BadZipFile) as error:
            print(f"E: {error}")
            return ERROR_EXIT
    for finding in findings:
        print(f"E: {extension.name}: {finding}")
    if findings:
        return 1
    print(f"Checked {extension.name}: no rpath, no libpython")
    return 0


if __name__ == "__main__":
    sys.exit(main())
