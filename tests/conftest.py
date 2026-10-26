"""Fixtures shared by the test suite: Docker availability, host ports, state isolation, and git repositories."""

from collections.abc import Iterator
from contextlib import suppress
import functools
import itertools
from pathlib import Path
import shutil
import socket

from fakes import PROBE_FILES, MakeRepository, create_repository
import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import state

NEEDS_DOCKER = "needs_docker"


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
    return functools.partial(create_repository, tmp_path / "repositories")


@pytest.fixture
def served_repository(make_repository: MakeRepository) -> Path:
    """An installable repository with a known marker in its committed content and in its console script."""
    return make_repository("probe", PROBE_FILES)
