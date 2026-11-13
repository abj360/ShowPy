#!/usr/bin/env python3

"""
test_sandbox_isolation.py --- regression tests for the sandbox holes found on 2026-11-11

Each test runs a real script that reached outside the sandbox before the fix.
"""

import http.server
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from stage.core.errors import SchemaError, StorageError, ValidationError
from stage.examples import two_set_show
from stage.show import sandbox
from stage.show.sandbox import check_script, run_analysis_script

SECRET = "host-secret-7f3a"


@pytest.fixture
def served(tmp_path: Path) -> Iterator[str]:
    (tmp_path / "data.txt").write_text("1 2\n3 4\n")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

    def handler(*args: object, **kwargs: object) -> Quiet:
        return Quiet(*args, directory=str(tmp_path), **kwargs)  # type: ignore[arg-type]

    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/data.txt"
    server.shutdown()
    server.server_close()


def test_regression_numpy_cannot_read_host_files(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text(f"{SECRET}\n")
    source = (
        f"import numpy as np\n\ndef main(ctx):\n"
        f"    return str(np.loadtxt({str(secret)!r}, dtype=str))\n"
    )
    with pytest.raises(ValidationError) as info:
        run_analysis_script(two_set_show(), source)
    assert SECRET not in str(info.value)


def test_regression_numpy_cannot_write_host_files(tmp_path: Path) -> None:
    target = tmp_path / "out.npy"
    source = (
        f"import numpy as np\n\ndef main(ctx):\n"
        f"    np.save({str(target)!r}, np.zeros(2))\n    return 1\n"
    )
    with pytest.raises(ValidationError):
        run_analysis_script(two_set_show(), source)
    assert not target.exists()


def test_regression_numpy_has_no_network(served: str) -> None:
    source = f"import numpy as np\n\ndef main(ctx):\n    return np.loadtxt({served!r}).tolist()\n"
    with pytest.raises(ValidationError):
        run_analysis_script(two_set_show(), source)


def test_regression_file_repository_is_not_importable() -> None:
    for module in ("stage.show.file_repository", "stage.show.repositories"):
        with pytest.raises(SchemaError):
            check_script(f"import {module}\n\ndef build(ctx):\n    return ctx.prev\n")


def test_host_files_outside_the_library_are_invisible() -> None:
    source = "import stage.show.sandbox_child as c\n\ndef main(ctx):\n    return c.__file__\n"
    with pytest.raises(SchemaError):
        check_script(source)
    probe = "def main(ctx):\n    import math\n    return math.pi\n"
    assert run_analysis_script(two_set_show(), probe).result == pytest.approx(3.14159, abs=1e-4)


def test_missing_bubblewrap_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_bwrap_path", lambda: None)
    with pytest.raises(StorageError):
        run_analysis_script(two_set_show(), "def main(ctx):\n    return 1\n")


def test_process_isolation_must_be_requested_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "_bwrap_path", lambda: None)
    result = run_analysis_script(
        two_set_show(), "def main(ctx):\n    return 1\n", isolation="process"
    )
    assert result.result == 1


def test_regression_bwrap_binds_every_import_directory_but_not_the_repo() -> None:
    import sys

    import stage

    command = sandbox._bwrap_command("bwrap")
    bound = {command[i + 1] for i, arg in enumerate(command) if arg == "--ro-bind"}
    package = Path(stage.__file__).parent
    for entry in sys.path:
        path = Path(entry)
        if not path.is_dir() or package.is_relative_to(path):
            continue
        assert any(path == Path(b) or path.is_relative_to(b) for b in bound), entry
    assert str(package.parent) not in bound


def test_regression_child_crash_message_includes_its_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    crash = [sys.executable, "-c", "import sys; sys.stderr.write('boom-7f3a'); sys.exit(3)"]
    monkeypatch.setattr(sandbox, "_command", lambda isolation: crash)
    with pytest.raises(ValidationError) as info:
        run_analysis_script(two_set_show(), "def main(ctx):\n    return 1\n")
    assert "boom-7f3a" in info.value.message


def test_regression_private_tmp_is_mounted_before_the_binds() -> None:
    command = sandbox._bwrap_command("bwrap")
    first_bind = command.index("--ro-bind")
    assert command.index("--tmpfs") < first_bind


def test_regression_symlinks_on_the_interpreter_path_are_recreated(tmp_path: Path) -> None:
    real = tmp_path / "cpython-3.14.7" / "bin"
    real.mkdir(parents=True)
    (real / "python3.14").write_text("")
    (tmp_path / "cpython-3.14").symlink_to(tmp_path / "cpython-3.14.7")
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(tmp_path / "cpython-3.14" / "bin" / "python3.14")
    args = sandbox._link_args(str(venv_bin / "python"))
    pairs = {(args[i + 1], args[i + 2]) for i, a in enumerate(args) if a == "--symlink"}
    assert (str(tmp_path / "cpython-3.14.7"), str(tmp_path / "cpython-3.14")) in pairs
