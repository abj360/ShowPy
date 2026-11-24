#!/usr/bin/env python3

"""
build_oracle.py --- builds a released e8s into its own venv as the parity harness's oracle

Resolves `ref` to its commit, refusing a pinned ref that names another commit, then checks it
out into a temporary git worktree under the output's parent (`.parity/`), builds
its wheel with that tree's own build backend, creates a venv on the running Python, installs
the dependencies locked at `ref` (hashes required) and then the wheel, and writes READY holding
the ref, its commit, the wheel's SHA-256 and a digest of every file in the venv. The worktree is
removed whether the build succeeds or fails. An oracle whose READY matches all four is reused
without building anything; the oracle interpreter always runs with -B, so it writes no bytecode
that would change the venv digest.

Contains:
    PINNED_COMMITS: the commit each pinned ref must resolve to
    interpreter(): returns a venv's interpreter path on this platform
    venv_digest(): returns the SHA-256 over every file's path and contents in a venv
    ready_stamp(): renders the READY text for a ref, its commit, the wheel and the venv digest
    is_ready(): tells whether an oracle folder holds a finished build of a ref's commit
    build_oracle(): builds the oracle of a ref unless it is ready; returns its interpreter
    main(): builds the oracle named on the command line and prints its interpreter
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

DEFAULT_REF = "v0.4.0"
# The commit v0.4.0 was released from; a tag moved or pushed again elsewhere is refused.
DEFAULT_COMMIT = "a6ef3caaa5a305a992aab73fccbbc4446670ef63"
PINNED_COMMITS = {DEFAULT_REF: DEFAULT_COMMIT}
DEFAULT_OUT = Path(".parity") / "oracle-v0.4.0"
PARITY_FOLDER = ".parity"
READY_FILE = "READY"
WHEEL_FOLDER = "wheel"
VENV_FOLDER = "venv"
REQUIREMENTS_FILE = "requirements.txt"
VENV_CONFIG = "pyvenv.cfg"
VERSION_KEY = "version_info"
WORKTREE_PREFIX = "worktree-"
# Variables that would point git or uv at another repository or environment than the one asked.
FOREIGN_ENVIRONMENT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "VIRTUAL_ENV",
)
RELEASE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
VERSION_CHECK = "import e8s; print(e8s.__version__)"


def interpreter(venv: Path) -> Path:
    """Returns the path of a venv's Python interpreter on this platform."""
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _running_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _venv_version(venv: Path) -> str | None:
    config = venv / VENV_CONFIG
    if not config.is_file():
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == VERSION_KEY:
            return ".".join(value.strip().split(".")[:2])
    return None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def venv_digest(venv: Path) -> str:
    """Returns the SHA-256 over the relative path and contents of every file in a venv.

    Bytecode caches count like any other file, and a symlink counts by its target text, so a
    file planted in a restored cache, or a changed one, gives another digest.

    Args:
        venv: The venv folder.

    Returns:
        digest: The hex SHA-256 of one line per file (path, tab, content digest), in path
            order.
    """
    digest = hashlib.sha256()
    for path in sorted(venv.rglob("*"), key=lambda found: found.relative_to(venv).as_posix()):
        if path.is_symlink():
            content = f"link {path.readlink().as_posix()}"
        elif path.is_file():
            content = _sha256(path)
        else:
            continue
        digest.update(f"{path.relative_to(venv).as_posix()}\t{content}\n".encode())
    return digest.hexdigest()


def ready_stamp(ref: str, commit: str, wheel_sha256: str, venv_sha256: str) -> str:
    """Renders the READY text: one `<field> <value>` line each for ref, commit, wheel and venv."""
    return f"ref {ref}\ncommit {commit}\nwheel-sha256 {wheel_sha256}\nvenv-sha256 {venv_sha256}\n"


def is_ready(out: Path, ref: str, commit: str) -> bool:
    """Tells whether an oracle folder holds a finished build of a ref's commit.

    Args:
        out: The oracle folder.
        ref: The ref asked for.
        commit: The commit the ref resolves to now.

    Returns:
        ready: True when READY names this ref and commit, the SHA-256 of the one wheel in the
            folder and the venv's current digest, and the venv's interpreter exists and runs the
            running Python's minor version.
    """
    ready = out / READY_FILE
    wheels = sorted((out / WHEEL_FOLDER).glob("*.whl"))
    if not ready.is_file() or len(wheels) != 1 or not interpreter(out / VENV_FOLDER).is_file():
        return False
    if _venv_version(out / VENV_FOLDER) != _running_version():
        return False
    expected = ready_stamp(ref, commit, _sha256(wheels[0]), venv_digest(out / VENV_FOLDER))
    return ready.read_text(encoding="utf-8") == expected


def _environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name not in FOREIGN_ENVIRONMENT}


def _run(command: Sequence[str | Path], *, cwd: Path, what: str) -> str:
    """Runs a command, returning its output; on failure prints it and exits with an E: line."""
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        env=_environment(),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        raise SystemExit(f"E: {what} failed (exit {result.returncode})")
    return result.stdout


def _refuse_links(out: Path) -> None:
    for folder in (out, out.parent):
        if folder.is_symlink():
            raise SystemExit(f"E: {folder} is a symlink; the oracle is built only in a real folder")


def _require_inside_parity(out: Path, root: Path) -> None:
    """Refuses an oracle folder that is not strictly inside <root>/.parity, before any deletion."""
    parity = (root / PARITY_FOLDER).resolve()
    resolved = (root / out).resolve()
    if resolved == parity or not resolved.is_relative_to(parity):
        raise SystemExit(f"E: the oracle folder {out} must be inside {parity}")


def _resolve_ref(ref: str, root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root,
        env=_environment(),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"E: ref {ref} is not in this repository; run git fetch --tags")
    commit = result.stdout.strip()
    pinned = PINNED_COMMITS.get(ref, commit)
    if commit != pinned:
        raise SystemExit(f"E: {ref} resolves to {commit}, not {pinned}; the tag moved")
    return commit


def _remove_worktree(root: Path, worktree: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=root,
        env=_environment(),
        capture_output=True,
        check=False,
    )
    shutil.rmtree(worktree, ignore_errors=True)
    _run(["git", "worktree", "prune"], cwd=root, what="git worktree prune")


def _build_wheel(worktree: Path, out: Path) -> Path:
    wheels = (out / WHEEL_FOLDER).absolute()
    command: list[str | Path] = [
        "uv",
        "build",
        "--wheel",
        "--python",
        sys.executable,
        "--out-dir",
        wheels,
        ".",
    ]
    _run(command, cwd=worktree, what="building the wheel")
    found = sorted(wheels.glob("*.whl"))
    if len(found) != 1:
        raise SystemExit(f"E: building the wheel left {len(found)} wheels in {wheels}")
    return found[0]


def _install(worktree: Path, out: Path, wheel: Path) -> Path:
    requirements = (out / REQUIREMENTS_FILE).absolute()
    export: list[str | Path] = [
        "uv",
        "export",
        "--frozen",
        "--format",
        "requirements-txt",
        "--no-dev",
    ]
    export += ["--all-extras", "--no-emit-project", "--quiet", "--output-file", requirements]
    _run(export, cwd=worktree, what="exporting the locked dependencies")
    venv = (out / VENV_FOLDER).absolute()
    _run(["uv", "venv", "--python", sys.executable, venv], cwd=worktree, what="creating the venv")
    python = interpreter(venv)
    install: list[str | Path] = ["uv", "pip", "install", "--quiet", "--python", python]
    _run([*install, "--require-hashes", "-r", requirements], cwd=out, what="installing deps")
    _run([*install, "--no-deps", wheel], cwd=out, what="installing the wheel")
    return python


def _check_version(python: Path, ref: str, out: Path) -> None:
    version = _run([python, "-I", "-B", "-c", VERSION_CHECK], cwd=out, what="importing e8s")
    version = version.strip()
    release = RELEASE_TAG.match(ref)
    if release and version != release.group(1):
        raise SystemExit(f"E: the oracle of {ref} reports e8s {version}")
    print(f"Found e8s {version}", flush=True)


def build_oracle(ref: str = DEFAULT_REF, out: Path = DEFAULT_OUT) -> Path:
    """Builds the wheel of a ref into its own venv unless a matching build is ready.

    Args:
        ref: The git ref to build, a release tag by default.
        out: The oracle folder, inside .parity/ of the repository root (the working
            directory); any other folder is refused before anything is deleted.

    Returns:
        python: The absolute path of the oracle venv's interpreter.
    """
    root = Path.cwd()
    _refuse_links(out)
    _require_inside_parity(out, root)
    python = interpreter(out / VENV_FOLDER).absolute()
    commit = _resolve_ref(ref, root)
    if is_ready(out, ref, commit):
        print(f"Found oracle {ref} ({commit[:12]}) in {out}", flush=True)
        return python
    print(f"Setting up oracle {ref} ({commit[:12]})", flush=True)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    worktree = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX, dir=out.parent)).absolute()
    try:
        _run(["git", "worktree", "add", "--detach", worktree, commit], cwd=root, what="git")
        wheel = _build_wheel(worktree, out)
        _install(worktree, out, wheel)
    finally:
        _remove_worktree(root, worktree)
    _check_version(python, ref, out)
    stamp = ready_stamp(ref, commit, _sha256(wheel), venv_digest(out / VENV_FOLDER))
    (out / READY_FILE).write_text(stamp, encoding="utf-8")
    return python


def main(argv: Sequence[str] | None = None) -> None:
    """Builds the oracle named on the command line and prints its interpreter path."""
    parser = argparse.ArgumentParser(description="Build a released e8s as the parity oracle.")
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    arguments = parser.parse_args(argv)
    print(build_oracle(arguments.ref, arguments.out))


if __name__ == "__main__":
    main()
