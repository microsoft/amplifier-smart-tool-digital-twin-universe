"""Preservation exports work after Stop and never publish a partial archive."""

import hashlib
from pathlib import Path
import stat
import sys
import time
from types import SimpleNamespace

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import export as export_module
from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL, SERVICE_LABEL
from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError


@pytest.fixture
def export_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = BackendConfig(state_root=tmp_path / "state", docker_host="unix:///owned/docker.sock")
    monkeypatch.setattr(state, "STATE_ROOT", config.state_root)
    record = state.UniverseRecord(
        id="dtu-export-test",
        name="export-test",
        description=None,
        profile_path=tmp_path / "compose.yaml",
        twin_machine="app",
        created_at=state.now(),
        docker_host=config.docker_host,
        phase="stopped",
    )
    state.write(record)
    container = SimpleNamespace(
        id="immutable-container-id",
        config=SimpleNamespace(labels={PROJECT_LABEL: record.id, SERVICE_LABEL: record.twin_machine}),
        state=SimpleNamespace(status="exited", running=False),
    )
    script = tmp_path / "docker_stub.py"
    script.write_text(
        "import sys\nassert sys.argv[1:] == ['cp', 'immutable-container-id:/workspace', '-']\nsys.stdout.buffer.write(b'archive-bytes')\n"
    )
    calls: list[dict] = []
    containers = [container]

    def listing(**kwargs: object) -> list[SimpleNamespace]:
        calls.append(kwargs)
        return containers

    client = SimpleNamespace(docker_cmd=[sys.executable, str(script)], container=SimpleNamespace(list=listing))
    monkeypatch.setattr(export_module, "compose_client", lambda project: client)
    return SimpleNamespace(
        config=config,
        record=record,
        script=script,
        calls=calls,
        containers=containers,
        destination=tmp_path / "out.tar",
    )


def test_export_from_stopped_exact_owned_twin_is_private_and_hashed(export_setup: SimpleNamespace) -> None:
    setup = export_setup
    result = lib.export_files(setup.record.id, "/workspace", setup.destination, config=setup.config)
    assert result.bytes == len(b"archive-bytes")
    assert result.sha256 == hashlib.sha256(b"archive-bytes").hexdigest()
    assert setup.destination.read_bytes() == b"archive-bytes"
    assert stat.S_IMODE(setup.destination.stat().st_mode) == 0o600
    assert setup.calls == [
        {"all": True, "filters": [("label", f"{PROJECT_LABEL}={setup.record.id}"), ("label", f"{SERVICE_LABEL}=app")]}
    ]
    assert state.read(setup.record.id).phase == "stopped"


@pytest.mark.parametrize(
    ("script", "kwargs", "code"),
    [
        ("import sys\nsys.stdout.buffer.write(b'x' * 100000)\n", {"max_bytes": 10}, "export-too-large"),
        ("import time\ntime.sleep(10)\n", {"timeout_seconds": 1}, "export-timeout"),
        ("import sys\nsys.stdout.buffer.write(b'partial')\nsys.exit(1)\n", {}, "export-failed"),
        ("pass\n", {}, "export-empty"),
    ],
)
def test_failure_keeps_environment_and_publishes_no_partial(
    export_setup: SimpleNamespace, script: str, kwargs: dict, code: str
) -> None:
    setup = export_setup
    setup.script.write_text(script)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_files(setup.record.id, "/workspace", setup.destination, config=setup.config, **kwargs)
    assert raised.value.code == code
    assert raised.value.universe_id == setup.record.id
    assert not setup.destination.exists()
    assert not list(setup.destination.parent.glob(".out.tar.*"))
    assert state.read(setup.record.id).phase == "stopped"


def test_existing_archive_is_never_overwritten(export_setup: SimpleNamespace) -> None:
    setup = export_setup
    setup.destination.write_bytes(b"retained")
    with pytest.raises(DigitalTwinUniverseError, match="already exists"):
        lib.export_files(setup.record.id, "/workspace", setup.destination, config=setup.config)
    assert setup.destination.read_bytes() == b"retained"
    assert not setup.calls


@pytest.mark.parametrize("selection", ["wrong-project", "wrong-service", "duplicate", "missing"])
def test_mismatched_or_ambiguous_twin_is_not_exported(export_setup: SimpleNamespace, selection: str) -> None:
    setup = export_setup
    if selection == "wrong-project":
        setup.containers[0].config.labels[PROJECT_LABEL] = "dtu-peer"
    elif selection == "wrong-service":
        setup.containers[0].config.labels[SERVICE_LABEL] = "database"
    elif selection == "duplicate":
        setup.containers.append(setup.containers[0])
    else:
        setup.containers.clear()
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_files(setup.record.id, "/workspace", setup.destination, config=setup.config)
    assert raised.value.code == "export-twin-ambiguous"
    assert not setup.destination.exists()


def test_full_stderr_pipe_cannot_block_export(export_setup: SimpleNamespace) -> None:
    setup = export_setup
    setup.script.write_text(
        "import sys\nsys.stderr.buffer.write(b'x' * 1000000)\nsys.stdout.buffer.write(b'archive')\n"
    )
    result = lib.export_files(setup.record.id, "/workspace", setup.destination, config=setup.config)
    assert result.bytes == 7


def test_inherited_pipe_writer_cannot_extend_timeout(export_setup: SimpleNamespace) -> None:
    setup = export_setup
    setup.script.write_text(
        "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])\n"
    )
    started = time.monotonic()
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_files(setup.record.id, "/workspace", setup.destination, timeout_seconds=1, config=setup.config)
    assert raised.value.code == "export-timeout"
    assert time.monotonic() - started < 4
    assert not setup.destination.exists()


@pytest.mark.parametrize("source", ["relative", "/workspace/../secret", "/workspace\0secret"])
def test_invalid_source_refused_before_container_access(export_setup: SimpleNamespace, source: str) -> None:
    setup = export_setup
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_files(setup.record.id, source, setup.destination, config=setup.config)
    assert raised.value.code == "export-path-invalid"
    assert not setup.calls
