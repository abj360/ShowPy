#!/usr/bin/env python3

"""
test_third_party_notices.py --- tests the Rust crate licence policy and the notices the wheel ships

The regeneration test needs the cargo-about that `nox -s rust` installs into .cargo-tools, so it
carries the `cargo_tools` mark: the default suite deselects it and the rust session runs it.

Contains:
    noxfile_license_expression(): returns the licence expression the noxfile's wheel check expects
    test_third_party_notices_are_current: regenerating the notice file twice gives its committed
        bytes both times
    test_license_expression_names_every_linked_crate_licence: the distribution's expression is
        the v0.4.0 expression and each other licence the notice file names, in sorted order
    test_notice_file_ships_as_a_licence_file: pyproject's license-files lists the notice file
    test_notice_names_only_linked_crates_once: no proc-macro or build-only crate, and each
        linked crate once
    test_notice_licences_reads_only_licence_headers: proves the notice parser can fail
    test_notice_crates_reads_only_used_by_lines: proves the crate parser can fail
    test_parse_cargo_tree_reads_each_package_once: names and versions, path and repeat markers
        dropped
    test_linked_licences_keeps_only_linked_crates_once: drops unlinked crates, repeats and
        licences no linked crate carries
    test_format_notices_puts_each_licence_under_its_header: the parsers read back what the
        renderer writes
    test_render_notices_names_the_install_command_without_cargo_about: a missing tool fails
        with the fix, never as a pass
    test_check_fails_closed_without_cargo_about: the --check run exits 2, not 0
    test_check_fails_on_a_stale_or_missing_notice_file: compares bytes and names the fix
    test_generate_writes_the_rendered_notices: the write run stores the rendering as is
    test_deny_config_bans_the_avoid_list: cargo deny allows only the permissive licences, bans
        the avoid list and refuses unmaintained, yanked and non-crates.io crates
    test_notice_config_accepts_the_deny_allow_list: cargo-about accepts the same licences, in
        the same order, and leaves out build and dev dependencies and our own crates
"""

import ast
import tomllib
from pathlib import Path

import pytest

import scripts.gen_third_party_notices as notices_script
from scripts.gen_third_party_notices import (
    NOTICE_FILE,
    LicenceBlock,
    format_notices,
    gen_third_party_notices,
    linked_licences,
    notice_crates,
    notice_licences,
    parse_cargo_tree,
    render_notices,
)

ROOT = Path(__file__).resolve().parents[3]
NOTICE = ROOT / NOTICE_FILE
PYPROJECT = ROOT / "pyproject.toml"
NOXFILE = ROOT / "noxfile.py"
DENY_CONFIG = ROOT / "deny.toml"
ABOUT_CONFIG = ROOT / "about.toml"
V040_LICENCES = ("PolyForm-Noncommercial-1.0.0", "MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts")
ALLOWED_LICENCES = [
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Zlib",
    "Unicode-3.0",
    "Apache-2.0 WITH LLVM-exception",
]
AVOIDED_CRATES = {"clipper2", "geo-clipper", "cp_sat", "minicbor", "poisson-diskus", "serde_cbor"}
CRATES_IO = "https://github.com/rust-lang/crates.io-index"
SEPARATOR = "=" * 80
# Compile-time crates of pyo3: its proc macros and their dependencies, and its build script's.
UNLINKED_CRATES = {
    "heck",
    "proc-macro2",
    "pyo3-build-config",
    "pyo3-macros",
    "pyo3-macros-backend",
    "quote",
    "syn",
    "target-lexicon",
    "unicode-ident",
}
CARGO_TREE = "\n".join(
    (
        "e8s-py v0.4.0 (/home/dev/e8s/crates/e8s-py)",
        "libm v0.2.16",
        "pyo3 v0.30.1",
        "pyo3-ffi v0.30.1",
        "libm v0.2.16",
        "pyo3 v0.30.1 (*)",
        "",
    )
)
MIT_TEXT = "MIT text\n"
REPORT = {
    "licenses": [
        {
            "id": "MIT",
            "text": MIT_TEXT,
            "used_by": [
                {"crate": {"name": "pyo3", "version": "0.30.1"}},
                {"crate": {"name": "syn", "version": "3.0.10"}},
                {"crate": {"name": "pyo3", "version": "0.30.1"}},
                {"crate": {"name": "libm", "version": "0.2.16"}},
            ],
        },
        {
            "id": "Unicode-3.0",
            "text": "Unicode text\n",
            "used_by": [{"crate": {"name": "unicode-ident", "version": "1.0.27"}}],
        },
        {
            "id": "BSD-2-Clause",
            "text": "BSD text\n",
            "used_by": [{"crate": {"name": "pyo3", "version": "0.29.0"}}],
        },
    ]
}

pytestmark = pytest.mark.skipif(
    not (ROOT / "e8s").is_dir(), reason="needs a source checkout; the wheel test has no e8s tree"
)


def noxfile_license_expression() -> str:
    """Returns the licence expression the noxfile's wheel check asserts on the built wheel."""
    for node in ast.parse(NOXFILE.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "LICENSE_EXPRESSION":
            assert isinstance(node.value, ast.Call)
            parts = ast.literal_eval(node.value.args[0])
            return " AND ".join(parts)
    raise AssertionError("noxfile defines no LICENSE_EXPRESSION")


@pytest.mark.cargo_tools
def test_third_party_notices_are_current() -> None:
    first = render_notices()
    assert render_notices() == first
    assert NOTICE.read_bytes() == first.encode()


def test_license_expression_names_every_linked_crate_licence() -> None:
    named = notice_licences(NOTICE.read_text(encoding="utf-8"))
    assert "MIT" in named
    added = sorted(set(named) - set(V040_LICENCES))
    expected = " AND ".join((*V040_LICENCES, *added))
    assert tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["license"] == expected
    assert noxfile_license_expression() == expected


def test_notice_file_ships_as_a_licence_file() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert NOTICE_FILE.as_posix() in project["license-files"]


def test_notice_names_only_linked_crates_once() -> None:
    named = notice_crates(NOTICE.read_text(encoding="utf-8"))
    assert named
    assert len(named) == len(set(named))
    assert {crate.split()[0] for crate in named} & UNLINKED_CRATES == set()


def test_notice_crates_reads_only_used_by_lines() -> None:
    text = "\n".join(
        (
            "Used by:",
            "    fake 1.0.0",
            SEPARATOR,
            "License: MIT",
            "Used by:",
            "    libm 0.2.16",
            "    pyo3 0.30.1",
            "",
            "    indented licence text",
        )
    )
    assert notice_crates(text) == ["libm 0.2.16", "pyo3 0.30.1"]


def test_parse_cargo_tree_reads_each_package_once() -> None:
    assert parse_cargo_tree(CARGO_TREE) == frozenset(
        {("e8s-py", "0.4.0"), ("libm", "0.2.16"), ("pyo3", "0.30.1"), ("pyo3-ffi", "0.30.1")}
    )


def test_linked_licences_keeps_only_linked_crates_once() -> None:
    blocks = linked_licences(REPORT, parse_cargo_tree(CARGO_TREE))
    assert blocks == [LicenceBlock("MIT", ("pyo3 0.30.1", "libm 0.2.16"), MIT_TEXT)]


def test_format_notices_puts_each_licence_under_its_header() -> None:
    blocks = [
        LicenceBlock("BSD-2-Clause", ("numpy 0.30.0",), "BSD text\n"),
        LicenceBlock("MIT", ("libm 0.2.16", "pyo3 0.30.1"), "MIT text\n\n"),
    ]
    text = format_notices(blocks)
    assert text.endswith("MIT text\n")
    assert not text.endswith("\n\n")
    assert notice_licences(text) == ["BSD-2-Clause", "MIT"]
    assert notice_crates(text) == ["numpy 0.30.0", "libm 0.2.16", "pyo3 0.30.1"]


def test_notice_licences_reads_only_licence_headers() -> None:
    text = "\n".join(
        (
            "Third-party Rust crates",
            "License: Fake-1.0",
            SEPARATOR,
            "License: Unicode-3.0",
            "License: Body-Line-1.0",
            SEPARATOR,
            "License: Apache-2.0 WITH LLVM-exception",
            SEPARATOR,
            "License: Unicode-3.0",
        )
    )
    assert notice_licences(text) == ["Apache-2.0 WITH LLVM-exception", "Unicode-3.0"]
    assert notice_licences("License: MIT") == []


def test_render_notices_names_the_install_command_without_cargo_about(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run: uv run nox -s rust"):
        render_notices(tmp_path)


def test_check_fails_closed_without_cargo_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert gen_third_party_notices(check=True, root=tmp_path) == 2
    assert "run: uv run nox -s rust" in capsys.readouterr().err


def test_check_fails_on_a_stale_or_missing_notice_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(notices_script, "render_notices", lambda root: f"Rendered {root.name}\n")
    assert gen_third_party_notices(check=True, root=tmp_path) == 1
    notice = tmp_path / NOTICE_FILE
    notice.parent.mkdir(parents=True)
    notice.write_bytes(f"Rendered {tmp_path.name}\r\n".encode())
    assert gen_third_party_notices(check=True, root=tmp_path) == 1
    assert (
        "is stale; run: uv run python scripts/gen_third_party_notices.py" in capsys.readouterr().err
    )
    notice.write_bytes(f"Rendered {tmp_path.name}\n".encode())
    assert gen_third_party_notices(check=True, root=tmp_path) == 0


def test_generate_writes_the_rendered_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        notices_script, "render_notices", lambda root: f"Licence {root.name} \u00e9\n"
    )
    assert gen_third_party_notices(check=False, root=tmp_path) == 0
    assert (tmp_path / NOTICE_FILE).read_bytes() == f"Licence {tmp_path.name} \u00e9\n".encode()


def test_deny_config_bans_the_avoid_list() -> None:
    deny = tomllib.loads(DENY_CONFIG.read_text(encoding="utf-8"))
    assert deny["licenses"]["allow"] == ALLOWED_LICENCES
    assert deny["licenses"]["private"] == {"ignore": True}
    banned = {entry["crate"] for entry in deny["bans"]["deny"]}
    assert banned == AVOIDED_CRATES
    assert all(entry["reason"] for entry in deny["bans"]["deny"])
    assert deny["advisories"]["unmaintained"] == "all"
    assert deny["advisories"]["yanked"] == "deny"
    assert deny["sources"] == {
        "unknown-registry": "deny",
        "unknown-git": "deny",
        "allow-registry": [CRATES_IO],
        "allow-git": [],
    }


def test_notice_config_accepts_the_deny_allow_list() -> None:
    about = tomllib.loads(ABOUT_CONFIG.read_text(encoding="utf-8"))
    assert about["accepted"] == ALLOWED_LICENCES
    assert about["ignore-build-dependencies"] is True
    assert about["ignore-dev-dependencies"] is True
    assert about["private"] == {"ignore": True}
