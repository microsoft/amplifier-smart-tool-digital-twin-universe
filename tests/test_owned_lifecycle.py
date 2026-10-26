"""Ownership and recovery contracts without a Docker daemon or model provider."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

from fakes import universe_record
import pytest
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.capabilities.create_profile import create
from digital_twin_universe.capabilities.universe import compose, execute, launch, overlay, runtime, state, stop
from digital_twin_universe.capabilities.universe import destroy as destroy_module
from digital_twin_universe.capabilities.universe.profile import Profile, XDtu
from digital_twin_universe.capabilities.universe.validate import Validation
from digital_twin_universe.cli import app
from digital_twin_universe.schemas import BackendConfig, Check, DigitalTwinUniverseError, ProfileReport, Universe


def config_at(path: Path) -> BackendConfig:
    return BackendConfig(state_root=path, docker_host="unix:///owned-test.sock")


def universe(value: state.UniverseRecord) -> Universe:
    return Universe(
        id=value.id,
        name=value.name,
        description=None,
        profile_path=value.profile_path,
        twin_machine=value.twin_machine,
        state="running",
        services=[],
        urls=[],
        state_path=value.state_path,
        created_at=value.created_at,
        operation_id=value.operation_id,
        phase=value.phase,
        expected_services=value.expected_services,
    )


@pytest.fixture
def launch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[BackendConfig, Path, list[str]]:
    config = config_at(tmp_path / "state")
    profile = tmp_path / "compose.yaml"
    profile.write_text("name: owned\nservices:\n  twin:\n    image: alpine\n")
    definition = {
        "name": "owned",
        "services": {"twin": {"image": "alpine", "environment": {"TOKEN": "private$token"}}},
        "volumes": {"artifacts": {"name": "owned_artifacts"}},
    }
    loaded = Profile(profile, "owned", None, "twin", ["twin"], XDtu(), definition)
    report = ProfileReport(
        path=profile, name="owned", twin_machine="twin", services=["twin"], ok=True, errors=[], warnings=[]
    )
    monkeypatch.setattr(launch.validate_module, "validate", lambda value: Validation(report, loaded, None))
    effects: list[str] = []

    def client(id: str, files: list[Path]) -> SimpleNamespace:
        def resolved() -> dict[str, Any]:
            return {**definition, "name": id, "volumes": {"artifacts": {"name": f"{id}_artifacts"}}}

        return SimpleNamespace(compose=SimpleNamespace(config=lambda return_json: resolved()))

    monkeypatch.setattr(launch, "compose_client", client)
    monkeypatch.setattr(launch, "_up", lambda client, value, timeout_seconds, **kwargs: effects.append(value.id))
    monkeypatch.setattr(launch, "measure", universe)
    return config, profile, effects


def test_operation_id_is_root_scoped_and_needs_no_directory(tmp_path: Path) -> None:
    one, two = config_at(tmp_path / "one"), config_at(tmp_path / "two")
    assert lib.launch_id("request", one) == lib.launch_id("request", one)
    assert lib.launch_id("request", one) != lib.launch_id("request", two)
    assert not (tmp_path / "one").exists()


def test_retry_observes_completed_launch_without_repeating_effects_or_reading_source(
    launch_fixture: tuple[BackendConfig, Path, list[str]],
) -> None:
    config, profile, effects = launch_fixture
    first = lib.launch(profile, operation_id="request", config=config)
    profile.unlink()
    second = lib.launch(profile, operation_id="request", config=config)
    assert first.id == second.id
    assert effects == [first.id]
    assert second.operation_id == "request"
    assert second.phase == "ready"
    assert not (first.state_path / "launch.private.yaml").exists()
    lifecycle = (first.state_path / state.LIFECYCLE_FILE).read_text()
    assert "private" not in lifecycle
    assert f"{first.id}_artifacts" in lifecycle


def test_render_failure_returns_exact_owner_and_repeat_does_not_render(
    launch_fixture: tuple[BackendConfig, Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, profile, effects = launch_fixture
    calls: list[str] = []

    def fail(value: state.UniverseRecord, loaded: Profile) -> None:
        calls.append(value.id)
        raise DigitalTwinUniverseError("snapshot-failed", "Repository cannot be read.", "Inspect it.")

    monkeypatch.setattr(overlay, "render", fail)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.launch(profile, operation_id="request", config=config)
    assert caught.value.universe_id == lib.launch_id("request", config)
    assert caught.value.operation_id == "request"
    with pytest.raises(DigitalTwinUniverseError, match="not replayed") as repeated:
        lib.launch(profile, operation_id="request", config=config)
    assert repeated.value.code == "operation-incomplete"
    assert calls == [caught.value.universe_id]
    assert effects == []


def test_crashed_process_leaves_receipt_and_releases_lifecycle_lock(tmp_path: Path) -> None:
    config = config_at(tmp_path / "state")
    id = lib.launch_id("crash", config)
    script = """from pathlib import Path
import os, sys
from digital_twin_universe.capabilities.universe import state, runtime
from digital_twin_universe.schemas import BackendConfig
with runtime.configured(BackendConfig(state_root=Path(sys.argv[1]), docker_host="unix:///owned-test.sock")):
    with state.lifecycle_lock(sys.argv[2]):
        state.allocate(state.UniverseRecord(id=sys.argv[2], name="owned", description=None, profile_path=Path("/missing"), twin_machine="twin", created_at=state.now(), operation_id="crash", phase="launching", docker_host="unix:///owned-test.sock"))
        os._exit(17)
"""
    finished = subprocess.run([sys.executable, "-c", script, str(config.state_root), id], timeout=10)
    assert finished.returncode == 17
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.launch("missing", operation_id="crash", config=config)
    assert caught.value.code == "operation-incomplete"
    assert caught.value.universe_id == id


def test_live_owner_excludes_other_process_cleanup(tmp_path: Path) -> None:
    config = config_at(tmp_path / "state")
    id = "dtu-owned-test"
    script = """from pathlib import Path
import sys
from digital_twin_universe.capabilities.universe import state, runtime
from digital_twin_universe.schemas import BackendConfig
with runtime.configured(BackendConfig(state_root=Path(sys.argv[1]))):
    with state.lifecycle_lock(sys.argv[2]):
        print("owned", flush=True)
        sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(config.state_root), id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "owned"
        with pytest.raises(DigitalTwinUniverseError) as caught:
            lib.destroy(id, config=config)
        assert caught.value.code == "operation-busy"
    finally:
        child.communicate("done\n", timeout=10)
    with runtime.configured(config), state.lifecycle_lock(id):
        pass


def test_corrupt_receipt_is_discoverable_without_hiding_healthy_records(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    healthy = universe_record(config=config)
    broken = tmp_path / "dtu-broken"
    broken.mkdir()
    (broken / state.RECORD_FILE).write_text('{"partial":')
    with runtime.configured(config):
        assert [value.id for value in state.read_all()] == [healthy.id]
    assert lib.recorded_ids(config) == ["dtu-broken", healthy.id]
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.status("dtu-broken", config)
    assert caught.value.code == "state-unreadable"


def test_config_is_isolated_across_concurrent_threads(tmp_path: Path) -> None:
    def observe(index: int) -> str:
        config = config_at(tmp_path / str(index))
        return lib.launch_id("same", config)

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(observe, range(12)))
    assert len(set(ids)) == 12


def test_launch_cleanup_never_claims_machine_wide_inventory_difference(tmp_path: Path) -> None:
    destroyed: list[str] = []

    def fail(profile: Path, timeout_seconds: int) -> Universe:
        raise DigitalTwinUniverseError("launch-failed", "Own launch failed.", "Inspect.", universe_id="dtu-own")

    run = SimpleNamespace(
        universes=SimpleNamespace(launch=fail, recorded=lambda: ["dtu-own", "dtu-stranger"]),
        compose=tmp_path / "compose.yaml",
        deadline=float("inf"),
        tool_ids=set(),
        notes=[],
        destroy_universe=lambda id: destroyed.append(id),
    )
    # Avoid infinity in ceil while keeping this fixture's deadline independent from a model.
    import time

    run.deadline = time.monotonic() + 60
    verdict = create.Run.verify_draft(
        cast(create.Run, run), [Check(command="true", expect_stdout=None, status="pending", detail=None)], False
    )
    assert verdict.failure is not None
    assert destroyed == ["dtu-own"]
    assert run.tool_ids == {"dtu-own"}


def test_drafts_are_unique_and_stale_promotion_preserves_both_versions(tmp_path: Path) -> None:
    final = tmp_path / "profile"
    first, second = create._allocate_draft(final), create._allocate_draft(final)
    assert first != second
    (first / "compose.yaml").write_text("first")
    (second / "compose.yaml").write_text("second")
    create._promote(cast(create.Run, SimpleNamespace(draft=first, target_revision=None)), final)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        create._promote(cast(create.Run, SimpleNamespace(draft=second, target_revision=None)), final)
    assert caught.value.code == "profile-changed"
    assert (final / "compose.yaml").read_text() == "first"
    assert (second / "compose.yaml").read_text() == "second"


def test_lost_destroy_ack_returns_receipt_without_new_docker_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_at(tmp_path)
    value = universe_record(config=config)
    down: list[str] = []
    inventory = SimpleNamespace(list=lambda **kwargs: [])
    client = SimpleNamespace(
        container=inventory,
        network=inventory,
        volume=inventory,
        compose=SimpleNamespace(down=lambda **kwargs: down.append(value.id)),
    )
    monkeypatch.setattr(destroy_module, "compose_client", lambda id: client)
    assert lib.destroy(value.id, config).complete
    assert lib.destroy(value.id, config).complete
    assert down == [value.id]
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.launch("missing", operation_id="other", config=config.model_copy(update={"state_root": tmp_path / "other"}))
    assert caught.value.code == "profile-not-found"


def test_stop_preserves_state_and_reports_running_residue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_at(tmp_path)
    value = universe_record(config=config)
    running = SimpleNamespace(name="owned-twin", state=SimpleNamespace(running=True, restarting=False))
    client = SimpleNamespace(
        compose=SimpleNamespace(stop=lambda **kwargs: None),
        container=SimpleNamespace(list=lambda **kwargs: [running], stop=lambda owned, time: None),
    )
    monkeypatch.setattr(stop, "compose_client", lambda id: client)
    result = lib.stop(value.id, config)
    assert result.outcome == "incomplete"
    assert value.state_path.exists()
    running.state.running = False
    assert lib.stop(value.id, config).complete
    with runtime.configured(config):
        assert state.read(value.id).phase == "stopped"


def test_missing_twin_cannot_report_running() -> None:
    from digital_twin_universe.schemas import Service

    assert (
        compose.universe_state([Service(name="db", state="running", health="healthy", image="db")], ["twin", "db"])
        == "degraded"
    )


def test_exec_drains_large_output_with_bounded_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        execute,
        "_exec_argv",
        lambda *args, **kwargs: [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x'*200000); sys.stderr.write('y'*200000)",
        ],
    )
    monkeypatch.setattr(execute, "LOGIN_SHELL", [])
    result = execute.execute("dtu-own", "ignored", max_output_bytes=1024)
    assert result.exit_code == 0
    assert result.stdout == "x" * 1024
    assert result.stderr == "y" * 1024
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_random_identity_collision_never_adopts_an_existing_universe(
    launch_fixture: tuple[BackendConfig, Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, profile, effects = launch_fixture
    value = universe_record(config=config)
    monkeypatch.setattr(state, "new_id", lambda name, operation_id: value.id)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.launch(profile, config=config)
    assert caught.value.code == "operation-conflict"
    assert effects == []
    with runtime.configured(config):
        assert state.read(value.id).profile_path == value.profile_path


def test_failure_without_owner_does_not_clean_other_new_environments(tmp_path: Path) -> None:
    destroyed: list[str] = []

    def fail(profile: Path, timeout_seconds: int) -> Universe:
        raise DigitalTwinUniverseError("profile-invalid", "No allocation occurred.", "Fix profile.")

    import time

    run = SimpleNamespace(
        universes=SimpleNamespace(launch=fail, recorded=lambda: ["dtu-stranger"]),
        compose=tmp_path / "compose.yaml",
        deadline=time.monotonic() + 60,
        tool_ids=set(),
        notes=[],
        destroy_universe=lambda id: destroyed.append(id),
    )
    create.Run.verify_draft(cast(create.Run, run), [], False)
    assert destroyed == []


def test_cleanup_receipt_prevents_relaunch_of_removed_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_at(tmp_path)
    id = lib.launch_id("removed", config)
    value = universe_record(id, config=config)
    value.operation_id = "removed"
    with runtime.configured(config):
        state.write(value)
    inventory = SimpleNamespace(list=lambda **kwargs: [])
    client = SimpleNamespace(
        container=inventory, network=inventory, volume=inventory, compose=SimpleNamespace(down=lambda **kwargs: None)
    )
    monkeypatch.setattr(destroy_module, "compose_client", lambda id: client)
    assert lib.destroy(id, config).complete
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.launch("missing", operation_id="removed", config=config)
    assert caught.value.code == "operation-removed"


def test_stop_failed_inventory_retains_unknown_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from python_on_whales.exceptions import DockerException

    config = config_at(tmp_path)
    value = universe_record(config=config)

    def unavailable(**kwargs: Any) -> list[Any]:
        raise DockerException(["docker", "container", "ls"], 1, stderr=b"offline")

    monkeypatch.setattr(stop, "compose_client", lambda id: SimpleNamespace(container=SimpleNamespace(list=unavailable)))
    result = lib.stop(value.id, config)
    assert result.outcome == "unknown"
    assert result.running is None
    assert value.state_path.exists()


def test_context_endpoint_resolution_ignores_unrelated_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setenv("DOCKER_CONTEXT", "selected-context")

    def resolved(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='"unix:///selected.sock"\n')

    monkeypatch.setattr(runtime.subprocess, "run", resolved)
    assert runtime.docker_host() == "unix:///selected.sock"
    assert seen[0][-1] == "selected-context"
    assert "{{json .Endpoints.docker.Host}}" in seen[0]


@pytest.fixture
def legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[BackendConfig, state.UniverseRecord, bytes]:
    config = BackendConfig(state_root=tmp_path / "state")
    monkeypatch.setenv("DOCKER_HOST", "unix:///ambient-peer.sock")
    monkeypatch.setenv("DOCKER_CONTEXT", "ambient-peer")
    record = universe_record("dtu-legacy-a1b2", config=config)
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
