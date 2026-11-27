"""Drive the real Compose/Bake streaming wrapper with a private fake Docker executable."""

from concurrent.futures import CancelledError
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import NamedTuple

import pytest
from python_on_whales.exceptions import DockerException

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import launch, overlay, runtime, state
from digital_twin_universe.schemas import BackendConfig, Destroyed, DigitalTwinUniverseError, Universe

OPERATION = "durable-request"
DOCKER = """\
from pathlib import Path
import json, os, signal, sys

root = Path(__file__).parent
args = sys.argv[1:]
with (root / "calls").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if "info" in args:
    assert args[-3:] == ["info", "--format", "{{json .ID}}"]
    assert args[:2] == ["--host", os.environ["TEST_DTU_DOCKER_HOST"]]
    is_after = (root / "last-up").exists()
    if is_after and os.environ.get("TEST_DTU_ENGINE_UNAVAILABLE"):
        sys.exit(1)
    print(json.dumps(os.environ.get("TEST_DTU_NEW_ENGINE", "fixture-engine") if is_after else "fixture-engine"))
elif "config" in args:
    print(json.dumps({"name": "test", "services": {
        "twin": {"image": "test:twin", "build": {"context": str(root)}}
    }}))
elif "version" in args and "buildx" in args:
    print("github.com/docker/buildx v0.37.2")
elif "bake" in args or "up" in args:
    phase = "bake" if "bake" in args else "bootstrap" if args[-1] == "helper" else "final"
    if phase == "final":
        (root / "last-up").touch()
    sys.stderr.write("image exported; PRIVATE-STDERR\\n")
    if os.environ.get("TEST_DTU_FAIL_PHASE") == phase:
        if os.environ.get("TEST_DTU_SIGNAL"):
            os.kill(os.getpid(), signal.SIGTERM)
        sys.exit(int(os.environ.get("TEST_DTU_EXIT", 1)))
else:
    sys.exit("unexpected invocation: " + repr(args))
"""


class FakeDocker(NamedTuple):
    config: BackendConfig
    profile: Path
    log: Path

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def launch(self) -> Universe:
        return lib.launch(self.profile, operation_id=OPERATION, config=self.config)

    def witness_path(self) -> Path:
        with runtime.configured(self.config):
            return state.launch_witness_path(state.new_id("launch", OPERATION))


@pytest.fixture
def docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    if not hasattr(socket, "AF_UNIX") or os.name != "posix":
        pytest.skip("launch witness v1 requires a POSIX Unix socket")
    # A synthetic socket gives stat a real endpoint identity; the fake CLI never connects to it.
    # Use a relative bind to fit the Unix sockaddr limit, even under a long pytest temporary root.
    monkeypatch.chdir(tmp_path)
    with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as endpoint:
        endpoint.bind("d.sock")
    host = f"unix://{tmp_path / 'd.sock'}"
    config = BackendConfig(state_root=tmp_path / "state", docker_host=host)
    profile = tmp_path / "compose.yaml"
    profile.write_text("services:\n  twin:\n    image: test:twin\n")
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\n" + DOCKER)
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("TEST_DTU_DOCKER_HOST", host)
    monkeypatch.setattr(launch, "measure", lambda record: Universe.model_construct(id=record.id))
    return FakeDocker(config, profile, tmp_path / "calls")


def gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        overlay,
        "render",
        lambda record, loaded: overlay.Overlay(path=None, bootstrap=["helper"], ca_path=None, gateway_builds=["twin"]),
    )
    monkeypatch.setattr(overlay, "export_ca", lambda record, rendered: None)


def test_success_is_bound_to_exact_dispatch_and_read_after_process_restart(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway(monkeypatch)
    project = docker.launch().id
    witness = lib.launch_terminal_witness(OPERATION, docker.config)
    assert witness is not None
    assert witness.version == 1
    assert witness.operation_id == OPERATION
    assert witness.project == project == lib.launch_id(OPERATION, docker.config)
    assert witness.docker_endpoint_sha256 == hashlib.sha256((docker.config.docker_host or "").encode()).hexdigest()
    assert witness.engine_id == "fixture-engine"
    socket_stat = Path("d.sock").stat()
    assert (witness.socket_device, witness.socket_inode) == (socket_stat.st_dev, socket_stat.st_ino)
    assert witness.phase == "final-up"
    assert witness.completed == ("bootstrap-up", "buildx-bake", "final-up")
    assert witness.error_category is None
    assert witness.rollback == "not-dispatched-by-dtu"
    assert docker.witness_path().stat().st_mode & 0o777 == 0o600
    assert "PRIVATE-STDERR" not in docker.witness_path().read_text()
    assert "--load" in next(args for args in docker.calls() if "bake" in args)
    up = [args for args in docker.calls() if "up" in args]
    assert all("--wait" in args and args[args.index("--project-name") + 1] == project for args in up)
    assert up[0][-1] == "helper"
    assert "--no-build" in up[1]
    calls = docker.calls()
    # Exercise a fresh consumer with no Docker executable on PATH and no live probe.
    code = """
from pathlib import Path
import sys
from digital_twin_universe.lib import BackendConfig, launch_terminal_witness
c = BackendConfig(state_root=Path(sys.argv[1]), docker_host=sys.argv[2])
w = launch_terminal_witness(sys.argv[3], config=c)
assert w is not None
print(w.model_dump_json())
"""
    observation = subprocess.run(
        [sys.executable, "-c", code, str(docker.config.state_root), docker.config.docker_host or "", OPERATION],
        env={**os.environ, "PATH": str(docker.profile.parent / "absent")},
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )
    assert json.loads(observation.stdout) == witness.model_dump(mode="json")
    assert docker.calls() == calls


@pytest.mark.parametrize(
    ("phase", "exit_code", "signal"),
    [("bootstrap", 1, False), ("bake", 1, False), ("final", 1, False), ("final", 130, False), ("final", 1, True)],
)
def test_export_progress_generic_exit_and_signal_are_not_terminal_evidence(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch, phase: str, exit_code: int, signal: bool
) -> None:
    gateway(monkeypatch)
    monkeypatch.setenv("TEST_DTU_FAIL_PHASE", phase)
    monkeypatch.setenv("TEST_DTU_EXIT", str(exit_code))
    if signal:
        monkeypatch.setenv("TEST_DTU_SIGNAL", "1")
    with pytest.raises(DigitalTwinUniverseError, match="PRIVATE-STDERR") as failure:
        docker.launch()
    assert failure.value.code == "launch-failed"
    assert failure.value.operation_id == OPERATION
    assert isinstance(failure.value.__cause__, DockerException)
    assert failure.value.__cause__.return_code == (-15 if signal else exit_code)
    assert not docker.witness_path().exists()
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None
    calls = docker.calls()
    with pytest.raises(DigitalTwinUniverseError, match="not replayed"):
        docker.launch()
    assert docker.calls() == calls


def test_post_dispatch_error_is_distinct_from_a_failed_dispatch(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_measure(record: state.UniverseRecord) -> Universe:
        raise ValueError("PRIVATE-MEASUREMENT-ERROR")

    monkeypatch.setattr(launch, "measure", fail_measure)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        docker.launch()
    assert caught.value.code == "launch-failed"
    assert caught.value.operation_id == OPERATION
    witness = lib.launch_terminal_witness(OPERATION, docker.config)
    assert witness is not None
    assert witness.completed == ("final-up",)
    assert witness.error_category == "post-dispatch-failed"
    assert "PRIVATE-MEASUREMENT-ERROR" not in docker.witness_path().read_text()
    assert lib.launch_terminal_witness(OPERATION, BackendConfig(state_root=docker.profile.parent / "foreign")) is None
    assert lib.launch_terminal_witness("another-operation", docker.config) is None
    wrong_host = docker.config.model_copy(update={"docker_host": "unix:///different.sock"})
    assert lib.launch_terminal_witness(OPERATION, wrong_host) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [("TEST_DTU_NEW_ENGINE", "replacement"), ("TEST_DTU_NEW_ENGINE", ""), ("TEST_DTU_ENGINE_UNAVAILABLE", "yes")],
)
def test_changed_missing_or_unreachable_engine_keeps_settlement_unknown(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(key, value)
    assert docker.launch().id
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_socket_replacement_keeps_settlement_unknown(docker: FakeDocker, monkeypatch: pytest.MonkeyPatch) -> None:
    def replace_socket(record: state.UniverseRecord) -> Universe:
        Path("d.sock").rename("old.sock")
        with closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as new:
            new.bind("d.sock")
        return Universe.model_construct(id=record.id)

    monkeypatch.setattr(launch, "measure", replace_socket)
    assert docker.launch().id
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_unsupported_endpoint_keeps_settlement_unknown(docker: FakeDocker) -> None:
    unsupported = docker.config.model_copy(update={"docker_host": "tcp://localhost:2375"})
    assert lib.launch(docker.profile, operation_id=OPERATION, config=unsupported).id
    assert lib.launch_terminal_witness(OPERATION, unsupported) is None
    assert not any("info" in args for args in docker.calls())


@pytest.mark.parametrize("error", [KeyboardInterrupt, CancelledError])
def test_cancelled_after_dispatch_before_witness_is_unknown(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch, error: type[BaseException]
) -> None:
    def cancel(record: state.UniverseRecord) -> Universe:
        raise error()

    monkeypatch.setattr(launch, "measure", cancel)
    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            docker.launch()
    else:
        with pytest.raises(DigitalTwinUniverseError, match="CancelledError"):
            docker.launch()
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_cancelled_docker_measurement_does_not_publish_witness(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel(record: state.UniverseRecord) -> Universe:
        raise DockerException(["docker", "compose", "ps"], 130)

    monkeypatch.setattr(launch, "measure", cancel)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        docker.launch()
    assert caught.value.code == "launch-failed"
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_cleanup_error_cannot_mask_cancellation_as_settlement(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel(record: state.UniverseRecord) -> Universe:
        original = Path.unlink

        def fail_unlink(self: Path, missing_ok: bool = False) -> None:
            if self.name == "launch.private.yaml":
                raise OSError("local cleanup failed")
            original(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        raise KeyboardInterrupt

    monkeypatch.setattr(launch, "measure", cancel)
    with pytest.raises(DigitalTwinUniverseError, match="OSError"):
        docker.launch()
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_witness_survives_state_removal_without_replaying_docker(docker: FakeDocker) -> None:
    id = docker.launch().id
    witness = lib.launch_terminal_witness(OPERATION, docker.config)
    assert witness is not None
    calls = docker.calls()
    with runtime.configured(docker.config):
        # Only simulate an independently completed destroy's state transition.
        state.remember_cleanup(state.read(id), Destroyed(id=id, removed=[], outcome="removed"))
        state.remove(id)
    assert lib.launch_terminal_witness(OPERATION, docker.config) == witness
    with pytest.raises(DigitalTwinUniverseError) as caught:
        docker.launch()
    assert caught.value.code == "operation-removed"
    assert docker.calls() == calls


def test_process_dies_after_compose_return_before_publication(docker: FakeDocker) -> None:
    code = """
from pathlib import Path
import os
import sys
from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import launch
c = lib.BackendConfig(state_root=Path(sys.argv[1]), docker_host=sys.argv[2])
launch.measure = lambda record: os._exit(17)
lib.launch(Path(sys.argv[3]), config=c, operation_id=sys.argv[4])
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(docker.config.state_root),
            docker.config.docker_host or "",
            str(docker.profile),
            OPERATION,
        ],
        check=False,
        timeout=10,
    )
    assert result.returncode == 17
    assert Path("last-up").exists()
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None
    calls = docker.calls()
    with runtime.configured(docker.config):
        # Launch has even saved "ready"; neither this phase nor the missing process yields a witness.
        assert state.read(lib.launch_id(OPERATION, docker.config)).phase == "ready"
    assert docker.launch().id == lib.launch_id(OPERATION, docker.config)
    assert docker.calls() == calls
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


@pytest.mark.parametrize(
    "update",
    [{"version": 2}, {"operation_id": "unrelated"}, {"project": "dtu-op-" + "0" * 32}, {"completed": ["buildx-bake"]}],
)
def test_corrupt_mismatched_or_future_record_is_unknown(docker: FakeDocker, update: dict[str, object]) -> None:
    docker.launch()
    path = docker.witness_path()
    path.write_text(json.dumps(json.loads(path.read_text()) | update))
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None


def test_failed_witness_write_does_not_change_launch_result(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = state.atomic_text

    def fail_witness(path: Path, text: str) -> None:
        if path == docker.witness_path():
            raise OSError("fixture disk failure")
        original(path, text)

    monkeypatch.setattr(state, "atomic_text", fail_witness)
    assert docker.launch().id
    assert lib.launch_terminal_witness(OPERATION, docker.config) is None
