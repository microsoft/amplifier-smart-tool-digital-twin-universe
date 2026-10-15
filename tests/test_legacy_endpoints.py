"""Legacy endpoint selection is explicit and matches across library and CLI surfaces."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import compose, runtime, state
from digital_twin_universe.cli import app
from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError


@pytest.fixture
def legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[BackendConfig, state.UniverseRecord, bytes]:
    config = BackendConfig(state_root=tmp_path / "state")
    monkeypatch.setenv("DOCKER_HOST", "unix:///ambient-peer.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "ambient-peer")
    with runtime.configured(config):
        record = state.UniverseRecord(
            id="dtu-legacy-a1b2",
            name="legacy",
            description=None,
            profile_path=Path("/missing/compose.yaml"),
            twin_machine="twin",
            created_at=state.now(),
        )
        state.write(record)
    return config, record, (record.state_path / state.RECORD_FILE).read_bytes()


@pytest.mark.parametrize(
    "capability", ["status", "list_universes", "stop", "destroy", "execute", "push_files", "pull_files", "export_files"]
)
def test_legacy_access_never_uses_ambient_context_or_changes_receipt(
    legacy: tuple[BackendConfig, state.UniverseRecord, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
) -> None:
    config, record, before = legacy

    def forbidden(**kwargs: Any) -> None:
        pytest.fail("Legacy access must select an explicit endpoint before constructing a Docker client")

    monkeypatch.setattr(compose, "DockerClient", forbidden)
    arguments: dict[str, tuple[Any, ...]] = {
        "status": (record.id,),
        "list_universes": (),
        "stop": (record.id,),
        "destroy": (record.id,),
        "execute": (record.id, "true"),
        "push_files": (record.id, tmp_path / "source", "/workspace"),
        "pull_files": (record.id, "/workspace", tmp_path / "output"),
        "export_files": (record.id, "/workspace", tmp_path / "output.tar"),
    }
    with pytest.raises(DigitalTwinUniverseError) as raised:
        getattr(lib, capability)(*arguments[capability], config=config)
    assert raised.value.code == "docker-endpoint-required"
    assert raised.value.universe_id == record.id
    assert "--docker-host" in raised.value.remedy
    assert (record.state_path / state.RECORD_FILE).read_bytes() == before
    assert not (tmp_path / "output.tar").exists()
    assert lib.recorded_ids(config) == [record.id]


def test_explicit_legacy_endpoint_is_used_without_adoption(
    legacy: tuple[BackendConfig, state.UniverseRecord, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, record, before = legacy
    selected = config.model_copy(update={"docker_host": "unix:///original.sock"})
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(compose, "DockerClient", lambda **kwargs: observed.append(kwargs))

    with runtime.configured(selected):
        compose.compose_client(record.id)
        assert state.read(record.id).docker_host is None
    assert observed[0]["host"] == selected.docker_host
    assert (record.state_path / state.RECORD_FILE).read_bytes() == before


def test_pinned_record_ignores_ambient_endpoint_and_rejects_explicit_mismatch(
    legacy: tuple[BackendConfig, state.UniverseRecord, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, record, _ = legacy
    record.docker_host = "unix:///original.sock"
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(compose, "DockerClient", lambda **kwargs: observed.append(kwargs))
    with runtime.configured(config):
        state.write(record)
        compose.compose_client(record.id)
    assert observed[0]["host"] == record.docker_host
    with (
        runtime.configured(config.model_copy(update={"docker_host": "unix:///peer.sock"})),
        pytest.raises(DigitalTwinUniverseError) as raised,
    ):
        compose.compose_client(record.id)
    assert raised.value.code == "docker-endpoint-mismatch"
    assert len(observed) == 1


@pytest.mark.parametrize(
    ("command", "capability", "arguments"),
    [
        ("list", "list_universes", []),
        ("status", "status", ["--id", "dtu-legacy-a1b2"]),
        ("exec", "execute", ["--id", "dtu-legacy-a1b2", "--command", "true"]),
        ("exec", "shell", ["--id", "dtu-legacy-a1b2"]),
        ("file-push", "push_files", ["--id", "dtu-legacy-a1b2", "--source", "source", "--destination", "/workspace"]),
        ("file-pull", "pull_files", ["--id", "dtu-legacy-a1b2", "--source", "/workspace", "--destination", "output"]),
        (
            "export-files",
            "export_files",
            ["--id", "dtu-legacy-a1b2", "--source", "/workspace", "--destination", "output.tar"],
        ),
        ("stop", "stop", ["--id", "dtu-legacy-a1b2"]),
        ("destroy", "destroy", ["--id", "dtu-legacy-a1b2"]),
    ],
)
@pytest.mark.parametrize("host", [None, "unix:///original.sock"])
def test_cli_endpoint_option_uses_library_configuration(
    monkeypatch: pytest.MonkeyPatch, command: str, capability: str, arguments: list[str], host: str | None
) -> None:
    observed: list[BackendConfig | None] = []

    def call(*args: Any, config: BackendConfig | None = None, **kwargs: Any) -> Any:
        observed.append(config)
        if capability == "list_universes":
            return []
        if capability == "shell":
            return 0
        return SimpleNamespace(model_dump_json=lambda **kwargs: "{}", complete=True, exit_code=0)

    monkeypatch.setattr(lib, capability, call)
    result = CliRunner().invoke(app, [command, *arguments, *(["--docker-host", host] if host else [])])
    assert result.exit_code == 0, result.output
    assert observed == [BackendConfig(docker_host=host) if host is not None else None]
