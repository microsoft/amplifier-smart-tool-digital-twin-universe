"""The universe capabilities apart from Docker: state, measurement, failure naming, and the CLI over them."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from python_on_whales.exceptions import DockerException, NoSuchContainer
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import compose, state
from digital_twin_universe.capabilities.universe import launch as launch_module
from digital_twin_universe.capabilities.universe.compose import universe_state, urls
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.cli import app, main
from digital_twin_universe.schemas import (
    Destroyed,
    DigitalTwinUniverseError,
    ExecResult,
    Health,
    Service,
    Transfer,
    Universe,
    Url,
    UrlSpec,
)

runner = CliRunner()


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    return tmp_path


def _record(id: str = "dtu-demo-0000") -> UniverseRecord:
    return UniverseRecord(
        id=id,
        name="demo",
        description=None,
        profile_path=Path("/profiles/demo/compose.yaml"),
        twin_machine="app",
        created_at=state.now(),
    )


def _service(state_word: str, health: Health | None = None) -> Service:
    return Service(name="svc", state=state_word, health=health, image="img")


def test_ids_carry_the_profile_name_and_differ_between_launches() -> None:
    first, second = state.new_id("demo"), state.new_id("demo")

    assert first.startswith("dtu-demo-")
    assert len(first) == len("dtu-demo-") + 4
    assert first != second


def test_a_written_record_reads_back_and_is_gone_after_remove(state_root: Path) -> None:
    record = _record()
    state.write(record)

    assert state.read(record.id) == record
    assert record.state_path == state_root / record.id

    state.remove(record.id)

    with pytest.raises(DigitalTwinUniverseError) as raised:
        state.read(record.id)
    assert raised.value.code == "universe-not-found"
    assert record.id in raised.value.message


def test_read_all_is_every_record_oldest_first_and_empty_without_a_state_root(state_root: Path) -> None:
    assert state.read_all() == []

    newer, older = _record("dtu-demo-1111"), _record("dtu-demo-0000")
    older = older.model_copy(update={"created_at": older.created_at.replace(year=2000)})
    state.write(newer)
    state.write(older)
    (state_root / "stray-file").write_text("not a universe")

    assert [record.id for record in state.read_all()] == ["dtu-demo-0000", "dtu-demo-1111"]


def test_an_image_or_container_name_is_pointed_back_at_its_universe_id(state_root: Path) -> None:
    state.write(_record("dtu-demo-1234"))

    for lookalike in ("dtu-demo-1234-app", "dtu-demo-1234-app-1"):
        with pytest.raises(DigitalTwinUniverseError) as raised:
            state.read(lookalike)
        assert raised.value.code == "universe-not-found"
        assert "the universe id is 'dtu-demo-1234'" in raised.value.remedy

    with pytest.raises(DigitalTwinUniverseError) as raised:
        state.read("dtu-other-0000")
    assert "Universes launched from this machine: dtu-demo-1234" in raised.value.remedy


def test_error_string_is_message_then_remedy() -> None:
    error = DigitalTwinUniverseError("some-code", "It broke.", "Fix it.")

    assert str(error) == "It broke. Fix it."
    assert (error.code, error.message, error.remedy) == ("some-code", "It broke.", "Fix it.")


@pytest.mark.parametrize(
    ("services", "expected"),
    [
        ([], "stopped"),
        ([_service("exited")], "stopped"),
        ([_service("running", "healthy"), _service("running")], "running"),
        ([_service("running", "starting")], "starting"),
        ([_service("running", "unhealthy")], "degraded"),
        ([_service("running", "healthy"), _service("exited")], "degraded"),
    ],
)
def test_universe_state_from_its_services(services: list[Service], expected: str) -> None:
    assert universe_state(services) == expected


def test_urls_follow_the_profile_where_it_describes_a_port_and_default_elsewhere() -> None:
    both_families = [{"HostIp": "0.0.0.0", "HostPort": "8410"}, {"HostIp": "::", "HostPort": "8410"}]
    ports = {
        "8000/tcp": both_families,
        "9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "32768"}],
        "53/udp": [{"HostIp": "0.0.0.0", "HostPort": "53"}],
        "7000/tcp": None,
    }
    specs = [
        UrlSpec(port=8000, path="/api/health", label="Health"),
        UrlSpec(port=8000, host="app.localhost", label="Home"),
        UrlSpec(port=7000, path="/unbound"),
    ]

    assert urls(ports, specs) == [
        Url(url="http://localhost:8410/api/health", port=8410, path="/api/health", label="Health"),
        Url(url="http://app.localhost:8410/", port=8410, path="/", label="Home"),
        Url(url="http://localhost:32768/", port=32768, path="/", label=None),
    ]
    assert urls({}, specs) == []


class ListedContainer:
    """A listed container that reads its labels, or is gone by the time they are read."""

    def __init__(self, project: str | None) -> None:
        self._project = project

    @property
    def config(self) -> SimpleNamespace:
        if self._project is None:
            raise NoSuchContainer(["docker", "container", "inspect"], 1)
        return SimpleNamespace(labels={compose.PROJECT_LABEL: self._project})


def test_a_container_removed_after_the_list_is_left_out_of_the_grouping(monkeypatch: pytest.MonkeyPatch) -> None:
    first, gone, second = ListedContainer("dtu-a"), ListedContainer(None), ListedContainer("dtu-b")
    client = SimpleNamespace(container=SimpleNamespace(list=lambda **options: [first, gone, second]))
    monkeypatch.setattr(compose, "compose_client", lambda: client)

    assert compose.compose_containers() == {"dtu-a": [first], "dtu-b": [second]}


def test_a_record_written_before_urls_existed_still_reads(state_root: Path) -> None:
    record = _record()
    (state_root / record.id).mkdir()
    written = json.loads(record.model_dump_json())
    del written["urls"]
    (state_root / record.id / state.RECORD_FILE).write_text(json.dumps(written))

    assert state.read(record.id).urls == []


@pytest.mark.parametrize(
    ("stderr", "code", "fragment"),
    [
        (b"failed to bind host port 0.0.0.0:8410/tcp: address already in use", "port-in-use", "8410"),
        (b"Bind for 0.0.0.0:9000 failed: port is already allocated", "port-in-use", "9000"),
        (b"failed to solve: process did not complete successfully", "build-failed", "failed to solve"),
        (b"application not healthy after 12s", "timeout", "12s"),
        (b"something else entirely", "launch-failed", "something else"),
        (b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock", "docker-unavailable", "Docker"),
    ],
)
def test_launch_failures_are_named_from_compose_output(stderr: bytes, code: str, fragment: str) -> None:
    error = DockerException(["docker", "compose", "up"], 1, stderr=stderr)

    translated = launch_module._launch_error(_record(), 12, error)

    assert translated.code == code
    assert fragment in translated.message
    if code != "docker-unavailable":
        assert "digital-twin-universe destroy --id dtu-demo-0000" in translated.remedy


def test_an_unhealthy_container_is_named_with_its_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch_module, "_container_logs", lambda container: f"logs of {container}")
    error = DockerException(["docker", "compose", "up"], 1, stderr=b"container dtu-demo-0000-app-1 is unhealthy")

    translated = launch_module._launch_error(_record(), 12, error)

    assert translated.code == "unhealthy"
    assert "logs of dtu-demo-0000-app-1" in translated.message


def test_exec_prints_the_result_and_exits_with_the_commands_code(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_execute(id: str, command: str, user: str | None, workdir: str | None, timeout_seconds: int) -> ExecResult:
        seen.update(id=id, command=command, user=user, workdir=workdir, timeout_seconds=timeout_seconds)
        return ExecResult(exit_code=7, stdout="out\n", stderr="err\n")

    monkeypatch.setattr(lib, "execute", fake_execute)

    result = runner.invoke(app, ["exec", "--id", "dtu-demo-0000", "--command", "false", "--user", "root"])

    assert result.exit_code == 7
    assert json.loads(result.stdout) == {"exit_code": 7, "stdout": "out\n", "stderr": "err\n"}
    assert seen == {"id": "dtu-demo-0000", "command": "false", "user": "root", "workdir": None, "timeout_seconds": 300}


def test_exec_without_a_command_opens_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "shell", lambda id, user, workdir: 3)

    result = runner.invoke(app, ["exec", "--id", "dtu-demo-0000"])

    assert result.exit_code == 3
    assert result.stdout == ""


def _universe(id: str) -> Universe:
    record = _record(id)
    return Universe(
        id=record.id,
        name=record.name,
        description=record.description,
        profile_path=record.profile_path,
        twin_machine=record.twin_machine,
        state="stopped",
        services=[],
        urls=[],
        state_path=record.state_path,
        created_at=record.created_at,
    )


def test_list_prints_a_json_array_of_universes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "list_universes", lambda: [_universe("dtu-demo-0000"), _universe("dtu-demo-1111")])

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert [universe["id"] for universe in json.loads(result.stdout)] == ["dtu-demo-0000", "dtu-demo-1111"]


def test_list_of_nothing_is_an_empty_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "list_universes", list)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_status_prints_the_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "status", _universe)

    result = runner.invoke(app, ["status", "--id", "dtu-demo-0000"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["id"] == "dtu-demo-0000"
    assert json.loads(result.stdout)["state"] == "stopped"


def test_file_push_and_pull_pass_their_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_push(id: str, source: Path, destination: str) -> Transfer:
        seen.update(push=(id, source, destination))
        return Transfer(source=str(source), destination=destination, files=1)

    def fake_pull(id: str, source: str, destination: Path) -> Transfer:
        seen.update(pull=(id, source, destination))
        return Transfer(source=source, destination=str(destination), files=1)

    monkeypatch.setattr(lib, "push_files", fake_push)
    monkeypatch.setattr(lib, "pull_files", fake_pull)

    pushed = runner.invoke(app, ["file-push", "--id", "dtu-demo-0000", "--source", "./src", "--destination", "/w"])
    pulled = runner.invoke(app, ["file-pull", "--id", "dtu-demo-0000", "--source", "/var/log/a", "--destination", "."])

    assert (pushed.exit_code, pulled.exit_code) == (0, 0)
    assert seen == {"push": ("dtu-demo-0000", Path("src"), "/w"), "pull": ("dtu-demo-0000", "/var/log/a", Path())}
    assert json.loads(pushed.stdout) == {"source": "src", "destination": "/w", "files": 1}


def test_destroy_prints_what_was_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "destroy", lambda id: Destroyed(id=id, removed=[f"{id}-app-1"]))

    result = runner.invoke(app, ["destroy", "--id", "dtu-demo-0000"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["removed"] == ["dtu-demo-0000-app-1"]


def test_a_named_failure_prints_message_and_remedy_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(profile: str, timeout_seconds: int) -> None:
        raise DigitalTwinUniverseError("profile-not-found", "No profile 'x'.", "Pass a path.")

    monkeypatch.setattr(lib, "launch", fail)
    monkeypatch.setattr("sys.argv", ["digital-twin-universe", "launch", "--profile", "x"])

    exit_code = main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "No profile 'x'. Pass a path."
