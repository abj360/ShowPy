#!/usr/bin/env python3

"""
gen_third_party_notices.py --- writes the licence notices of the Rust crates linked into e8s._core

cargo-about reads the normal dependency graph of e8s-py, the crate built as e8s._core, with build
and dev dependencies left out and our own unpublished crates ignored (about.toml). It renders
about.hbs with each licence text taken from the crates' own sources. It runs offline on the
locked graph, so the same Cargo.lock always gives the same bytes. The cargo-about it runs is the
one `nox -s rust` installs into .cargo-tools at the version the noxfile pins.

Contains:
    NOTICE_FILE: the notice file the wheel ships, relative to the repository root
    cargo_about(): returns the path of the cargo-about that the rust session installs
    render_notices(): runs cargo about over e8s-py's dependency graph and returns the notice text
    notice_licences(): lists the licence ids a notice text names, sorted and without repeats
    gen_third_party_notices(): writes the notice file, or with check compares it; returns the
        exit code
    main(): parses --check and returns gen_third_party_notices' exit code
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE_FILE = Path("e8s/licenses/THIRD_PARTY_RUST.txt")
CARGO_TOOLS_BIN = Path(".cargo-tools/bin")
EXTENSION_MANIFEST = "crates/e8s-py/Cargo.toml"
ABOUT_CONFIG = "about.toml"
ABOUT_TEMPLATE = "about.hbs"
INSTALL_COMMAND = "uv run nox -s rust"
GENERATE_COMMAND = "uv run python scripts/gen_third_party_notices.py"
# about.hbs puts each licence block under an 80-character rule; a licence text never does.
LICENCE_HEADER = re.compile(r"^={80}\nLicense: (?P<licence>[^\n]+)$", re.MULTILINE)
ERROR_EXIT = 2


def cargo_about(root: Path = ROOT) -> Path:
    """Returns the path of the cargo-about that `nox -s rust` installs under a checkout."""
    name = "cargo-about.exe" if sys.platform == "win32" else "cargo-about"
    return root / CARGO_TOOLS_BIN / name


def render_notices(root: Path = ROOT) -> str:
    """Runs cargo about over e8s-py's dependency graph and returns the notice text.

    Args:
        root: The repository checkout whose locked graph is read.

    Returns:
        notices: The rendered notice text, ending in exactly one newline.

    Raises:
        FileNotFoundError: cargo-about is not installed under root; the message gives the fix.
        subprocess.CalledProcessError: cargo could not fetch the locked crates, or cargo-about
            failed, for example on a crate whose licence it cannot identify.
    """
    tool = cargo_about(root)
    if not tool.is_file():
        raise FileNotFoundError(f"cargo-about not found at {tool}; run: {INSTALL_COMMAND}")
    subprocess.run(["cargo", "fetch", "--locked"], cwd=root, check=True, capture_output=True)
    generated = subprocess.run(
        [
            str(tool),
            "generate",
            "--frozen",
            "--fail",
            "--manifest-path",
            EXTENSION_MANIFEST,
            "--config",
            ABOUT_CONFIG,
            ABOUT_TEMPLATE,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    # The template leaves blank lines after the last licence; the file ends in one newline.
    return generated.stdout.decode("utf-8").rstrip("\n") + "\n"


def notice_licences(text: str) -> list[str]:
    """Lists the licence ids that a notice text names in its licence headers.

    Args:
        text: A notice text rendered from about.hbs.

    Returns:
        licences: Each id once, sorted.
    """
    return sorted({match["licence"] for match in LICENCE_HEADER.finditer(text)})


def gen_third_party_notices(*, check: bool, root: Path = ROOT) -> int:
    """Writes the notice file, or with check compares it with a fresh rendering.

    Args:
        check: Compare instead of writing, and fail when the committed file is stale.
        root: The repository checkout to render and write in.

    Returns:
        exit_code: 0 when written or current, 1 when stale, 2 when rendering failed.
    """
    try:
        notices = render_notices(root).encode("utf-8")
    except FileNotFoundError as error:
        print(f"E: {error}", file=sys.stderr)
        return ERROR_EXIT
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip().splitlines()
        print(f"E: {' '.join(error.cmd)} failed: {detail[-1] if detail else ''}", file=sys.stderr)
        return ERROR_EXIT
    notice = root / NOTICE_FILE
    if not check:
        notice.parent.mkdir(parents=True, exist_ok=True)
        notice.write_bytes(notices)
        print(f"Wrote {NOTICE_FILE.as_posix()}")
        return 0
    if not notice.is_file() or notice.read_bytes() != notices:
        print(f"E: {NOTICE_FILE.as_posix()} is stale; run: {GENERATE_COMMAND}", file=sys.stderr)
        return 1
    print(f"Checked {NOTICE_FILE.as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parses --check and returns gen_third_party_notices' exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else None)
    parser.add_argument(
        "--check", action="store_true", help="fail when the committed notice file is stale"
    )
    return gen_third_party_notices(check=parser.parse_args(argv).check)


if __name__ == "__main__":
    sys.exit(main())
