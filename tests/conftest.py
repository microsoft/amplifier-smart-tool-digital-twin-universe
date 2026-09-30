"""Fixtures shared by the test suite: Docker availability, host ports, state isolation, and git repositories."""

from collections.abc import Callable, Iterator
from contextlib import suppress
import itertools
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import state

NEEDS_DOCKER = "needs_docker"
# A committer the host's global git config cannot change, so a commit works on a machine with no identity set.
GIT = ("git", "-c", "user.email=test@dtu.invalid", "-c", "user.name=DTU Test", "-c", "commit.gpgsign=false")

MakeRepository = Callable[[str, dict[str, str]], Path]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip every `needs_docker` test at once when this machine has no usable daemon, probing only if one asks."""
    if not any(item.get_closest_marker(NEEDS_DOCKER) for item in items):
        return
    if shutil.which("docker") is not None and lib.check().ok:
        return
    skip = pytest.mark.skip(reason="needs a usable Docker daemon")
    for item in items:
        if item.get_closest_marker(NEEDS_DOCKER):
            item.add_marker(skip)


@pytest.fixture
def free_port(worker_id: str) -> int:
    """A host port nothing holds right now, and that no other test worker will be handed.

    Each xdist worker draws from its own range and never repeats a port, so two workers launching at once cannot
    collide; binding first skips anything the machine already holds. Without xdist the kernel picks, as before.
    """
    if worker_id == "master":
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
    for port in _worker_ports.setdefault(worker_id, itertools.count(_worker_port_base(worker_id))):
        with socket.socket() as sock, suppress(OSError):
            sock.bind(("127.0.0.1", port))
            return port
    raise AssertionError("unreachable: itertools.count never ends")


def _worker_port_base(worker_id: str) -> int:
    """`gw3` draws from 21500 upward: a range per worker, well clear of the ephemeral ports Docker itself uses."""
    return WORKER_PORT_BASE + int(worker_id.removeprefix("gw")) * WORKER_PORT_SPAN


_worker_ports: dict[str, Iterator[int]] = {}
WORKER_PORT_BASE = 20000
WORKER_PORT_SPAN = 500


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Universe records under the test's own directory, so a test never sees or removes a real universe."""
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path / "state")
    return tmp_path / "state"


@pytest.fixture
def make_repository(tmp_path: Path) -> MakeRepository:
    """Build a git repository on the host with one commit, for a profile to serve."""

    def make(name: str, files: dict[str, str]) -> Path:
        repository = tmp_path / "repositories" / name
        repository.mkdir(parents=True)
        for relative, content in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "init", "-b", "main", "-q"], cwd=repository, check=True)
        subprocess.run([*GIT, "add", "."], cwd=repository, check=True)
        subprocess.run([*GIT, "commit", "-q", "-m", "initial"], cwd=repository, check=True)
        return repository

    return make


@pytest.fixture
def served_repository(make_repository: MakeRepository) -> Path:
    """An installable repository with a known marker in its committed content and in its console script."""
    return make_repository(
        "probe",
        {
            "MARKER.txt": "served-from-the-universe\n",
            "pyproject.toml": (
                "[project]\n"
                'name = "dtu-probe"\n'
                'version = "0.1.0"\n'
                'requires-python = ">=3.10"\n'
                "[project.scripts]\n"
                'dtu-probe = "dtu_probe:main"\n'
                "[build-system]\n"
                'requires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n'
            ),
            "src/dtu_probe/__init__.py": 'def main() -> None:\n    print("dtu-probe-installed")\n',
        },
    )
