#!/usr/bin/env python3

"""
test_packaging.py --- tests that the installed distribution exposes its name, version, schema,
bundled fonts and compiled core, and that its wheel holds the package files and nothing else
"""

import importlib
import importlib.metadata
import importlib.util
import json
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest
from packaging.version import Version

import e8s
from e8s.core.resources import schema_path
from e8s.geo.fonts import MAX_FONT_BYTES

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY_ROOT = REPO_ROOT / "e8s"
CARGO_MANIFEST = REPO_ROOT / "Cargo.toml"
CORE_AREAS = ("core", "geo", "time", "sheet", "cast", "engine", "forms")
EXTENSIONS = frozenset({"e8s/_core.abi3.so", "e8s/_core.pyd"})
DIST_INFO_SUFFIX = ".dist-info"
LICENSES_FOLDER = "licenses"
DIST_INFO_FILES = frozenset({"METADATA", "WHEEL", "RECORD"})
# maturin records the crates linked into e8s._core as a CycloneDX bill of materials.
SBOM = "sboms/e8s-py.cyclonedx.json"


def test_version_is_exported() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", e8s.__version__)


def test_version_matches_the_cargo_manifest() -> None:
    manifest = tomllib.loads(CARGO_MANIFEST.read_text(encoding="utf-8"))
    assert e8s.__version__ == str(Version(manifest["workspace"]["package"]["version"]))


def test_core_extension_imports_with_its_seven_submodules() -> None:
    core = importlib.import_module("e8s._core")
    assert core.__version__ == e8s.__version__
    for area in CORE_AREAS:
        module = importlib.import_module(f"e8s._core.{area}")
        assert getattr(core, area) is module
        assert module.__name__ == f"e8s._core.{area}"
        assert module.__doc__
        assert [name for name in vars(module) if not name.startswith("__")] == []


def test_schema_is_a_package_resource() -> None:
    assert schema_path("showpy.show.v1.json").is_file()


def test_schemas_ship_their_mit_notice() -> None:
    notice = schema_path("LICENSE-MIT.txt").read_text()
    assert notice.startswith("MIT License\n\nCopyright (c) 2026 abj360\n")
    assert "Permission is hereby granted, free of charge" in notice


def test_distribution_name_is_e8s() -> None:
    assert importlib.metadata.metadata("e8s")["Name"] == "e8s"


LICENSE_EXPRESSION = " AND ".join(
    ("PolyForm-Noncommercial-1.0.0", "MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts")
)
LICENSE_FILES = {
    "LICENSE",
    "e8s/schemas/LICENSE-MIT.txt",
    "e8s/fonts/licenses/HERSHEY.txt",
    "e8s/fonts/licenses/MIT.txt",
    "e8s/fonts/licenses/OFL-1.1.txt",
}


def test_distribution_states_its_licences() -> None:
    metadata = importlib.metadata.metadata("e8s")
    assert metadata["License-Expression"] == LICENSE_EXPRESSION
    assert set(metadata.get_all("License-File") or []) == LICENSE_FILES
    assert not [c for c in metadata.get_all("Classifier") or [] if c.startswith("License ::")]


def test_import_name_is_e8s() -> None:
    assert importlib.import_module("e8s").__name__ == "e8s"
    assert importlib.util.find_spec("stage") is None


BUNDLED = ("simplex", "hershey-roman", "hershey-script", "hershey-greek", "relief-singleline")
# Mirrors the pre-commit check-added-large-files limit (--maxkb=1024).
BUNDLED_FONT_BUDGET_BYTES = 1024 * 1024
LICENCES = ("licenses/OFL-1.1.txt", "licenses/HERSHEY.txt", "licenses/MIT.txt", "NOTICE.txt")


def test_bundled_fonts_ship_with_their_licences() -> None:
    fonts = files("e8s").joinpath("fonts")
    for name in BUNDLED:
        data = fonts.joinpath(f"{name}.json").read_bytes()
        assert len(data) < BUNDLED_FONT_BUDGET_BYTES
        font = json.loads(data)
        assert font["format"] == "showpy.font.v1"
        assert font["license"] in {"MIT", "OFL-1.1", "LicenseRef-Hershey-Fonts"}
    assert all(fonts.joinpath(path).is_file() for path in LICENCES)


def test_bundled_font_budget_fits_the_loader_cap() -> None:
    assert BUNDLED_FONT_BUDGET_BYTES <= MAX_FONT_BYTES


def tracked_package_files(root: Path) -> set[str]:
    """Lists every file git tracks under the e8s package folder of a checkout."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "e8s"], cwd=root, capture_output=True, check=True, text=True
    )
    return {path for path in listed.stdout.split("\0") if path}


def build_distribution(kind: str, out: Path) -> Path:
    """Builds one distribution of the source checkout with uv and returns its path.

    Args:
        kind: The uv build flag that picks the distribution, `--wheel` or `--sdist`.
        out: The empty folder the distribution is written to.

    Returns:
        built: Path of the single file uv wrote to out.
    """
    if not LIBRARY_ROOT.is_dir():
        pytest.skip("needs a source checkout; the installed wheel has no e8s tree")
    uv = shutil.which("uv")
    assert uv is not None, "E: uv is not on PATH; the distribution checks build with it"
    subprocess.run(
        [uv, "build", kind, "--out-dir", str(out), str(REPO_ROOT)], capture_output=True, check=True
    )
    (built,) = (path for path in out.iterdir() if path.name != ".gitignore")
    return built


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Builds the wheel of the source checkout into a fresh folder and returns its path."""
    return build_distribution("--wheel", tmp_path_factory.mktemp("wheel"))


def test_wheel_holds_every_tracked_package_file_plus_the_extension(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
    metadata = {name for name in names if name.split("/")[0].endswith(DIST_INFO_SUFFIX)}
    package = names - metadata
    extension = package & EXTENSIONS
    assert len(extension) == 1, sorted(extension)
    assert package - extension == tracked_package_files(REPO_ROOT)
    inside = [name.split("/", 1)[1] for name in metadata]
    licences = {name.split("/", 1)[1] for name in inside if name.startswith(f"{LICENSES_FOLDER}/")}
    assert licences == LICENSE_FILES
    others = {name for name in inside if not name.startswith(f"{LICENSES_FOLDER}/")}
    assert others == DIST_INFO_FILES | {SBOM}


def test_sdist_carries_the_toolchain_pin(tmp_path: Path) -> None:
    sdist = build_distribution("--sdist", tmp_path)
    with tarfile.open(sdist) as archive:
        names = {name.split("/", 1)[1] for name in archive.getnames() if "/" in name}
    assert {"rust-toolchain.toml", "Cargo.toml", "Cargo.lock"} <= names
