"""Launch apart from Docker: the overlay's image names, when builds go through Bake, and the Bake command itself."""

from collections.abc import Iterator
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

from fakes import universe_record
import pytest
from python_on_whales.exceptions import DockerException
import yaml

from digital_twin_universe.capabilities.universe import launch as launch_module
from digital_twin_universe.capabilities.universe import overlay, state
from digital_twin_universe.capabilities.universe import validate as validate_module
from digital_twin_universe.capabilities.universe.compose import compose_client
from digital_twin_universe.capabilities.universe.profile import Profile, Rewrite, XDtu
from digital_twin_universe.schemas import DigitalTwinUniverseError, ProfileReport, Universe

PROFILE_PATH = Path("/profiles/demo/compose.yaml")
REWRITE = Rewrite(match="api.example.com", target="http://mock:8080")


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(state, "STATE_ROOT", tmp_path)
    return tmp_path


def _profile(x_dtu: XDtu) -> Profile:
    services: dict[str, Any] = {
        "app": {"build": {"context": "/profiles/demo", "dockerfile": "Dockerfile"}},
        "named": {"build": {"context": "/profiles/demo/named"}, "image": "example/named:dev"},
        "mock": {"image": "python:3.12-alpine"},
    }
    return Profile(
        path=PROFILE_PATH,
        name="demo",
        description=None,
        twin_machine="app",
        services=list(services),
        x_dtu=x_dtu,
        config={"services": services},
    )


def test_a_build_through_the_gateway_tags_the_image_compose_would_name_and_keeps_a_profiles_own(
    state_root: Path,
) -> None:
    rendered = overlay.render(universe_record(), _profile(XDtu(rewrites=[REWRITE])))

    assert rendered.path is not None
    services = yaml.safe_load(rendered.path.read_text())["services"]
    assert rendered.gateway_builds == ["app", "named"]
    assert services["app"]["image"] == "dtu-demo-0000-app"
    assert services["app"]["build"]["network"] == "host"
    assert "image" not in services["named"]
    assert "image" not in services["mock"]


def test_without_a_gateway_nothing_is_built_through_it(state_root: Path) -> None:
    rendered = overlay.render(universe_record(), _profile(XDtu()))

    assert rendered.path is None
    assert rendered.gateway_builds == []


@pytest.mark.parametrize(
    ("reported", "version"),
    [
        ("github.com/docker/buildx v0.37.2 7b6b6309", (0, 37)),
        ("github.com/docker/buildx v0.29.1-desktop.1 3b3a0c2", (0, 29)),
        ("github.com/docker/buildx v0.12.1 30feaa1", (0, 12)),
        ("github.com/docker/buildx v1.0.0 0000000", (1, 0)),
        ("buildx unknown", None),
    ],
)
def test_the_buildx_version_is_read_from_what_it_reports(
    reported: str, version: tuple[int, int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = compose_client("dtu-demo-0000")
    monkeypatch.setattr(client.buildx, "version", lambda: reported)

    assert launch_module._buildx_version(client) == version


def test_without_buildx_there_is_no_version(monkeypatch: pytest.MonkeyPatch) -> None:
    client = compose_client("dtu-demo-0000")

    def missing() -> str:
        raise DockerException(["docker", "buildx", "version"], 1, stderr=b"docker: unknown command: docker buildx")

    monkeypatch.setattr(client.buildx, "version", missing)

    assert launch_module._buildx_version(client) is None


@pytest.mark.parametrize(
    ("grant", "allowed"),
    [
        (True, ["--allow=network.host", "--allow=fs.read=/profiles/demo", "--allow=fs.read=/state/overlay/dtu-ca"]),
        (False, []),
    ],
    ids=["granted", "before-entitlements"],
)
def test_bake_grants_the_host_network_and_reads_only_local_contexts(
    grant: bool,
    allowed: list[str],
    state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = universe_record()
    record.state_path.mkdir(parents=True)
    config = {
        "name": record.id,
        "services": {
            "app": {
                "build": {
                    "context": "/profiles/demo",
                    "additional_contexts": {"dtu-ca": "/state/overlay/dtu-ca", "base": "docker-image://alpine:3"},
                    "network": "host",
                },
                "image": "dtu-demo-0000-app",
            },
            "remote": {"build": {"context": "https://github.com/example/remote.git"}},
            "gateway": {"image": "mitmproxy/mitmproxy:12.1.2"},
        },
    }
    client = compose_client(record.id, [PROFILE_PATH])
    monkeypatch.setattr(client.compose, "config", lambda return_json: config)
    commands: list[list[str]] = []

    def stream(command: list[Any]) -> Iterator[tuple[str, bytes]]:
        commands.append([str(part) for part in command])
        yield "stderr", b"#1 [internal] load local bake definitions\n"

    monkeypatch.setattr(launch_module, "stream_stdout_and_stderr", stream)

    launch_module._bake(client, record, ["app", "remote"], grant=grant)

    [command] = commands
    definition = record.state_path / launch_module.BAKE_FILE
    assert command[command.index("buildx") :] == [
        "buildx",
        "bake",
        "--progress=plain",
        "--load",
        *allowed,
        "--file",
        str(definition),
    ]
    assert yaml.safe_load(definition.read_text()) == {
        "name": record.id,
        "services": {name: config["services"][name] for name in ("app", "remote")},
    }
    assert "load local bake definitions" in capsys.readouterr().err


def test_bake_excludes_runtime_and_unrelated_secrets_and_replaces_public_file_privately(
    state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = universe_record()
    record.state_path.mkdir()
    definition = record.state_path / launch_module.BAKE_FILE
    definition.write_text("previous content")
    definition.chmod(0o644)
    client = compose_client(record.id)
    sentinel = "RUNTIME_SECRET_MUST_NOT_BE_SAVED"
    config = {
        "name": record.id,
        "services": {
            "app": {
                "build": {"context": "/fixture", "args": {"INTENDED": "build-value"}, "secrets": ["build-token"]},
                "image": "fixture:built",
                "platform": "linux/amd64",
                "environment": {"TOKEN": sentinel},
                "labels": {"private": sentinel},
                "command": sentinel,
            },
            "unrelated": {"environment": {"TOKEN": sentinel}, "build": {"args": {"TOKEN": sentinel}}},
        },
        "secrets": {"build-token": {"environment": "BUILD_TOKEN"}, "runtime": {"content": sentinel}},
        "x-private": sentinel,
    }
    monkeypatch.setattr(client.compose, "config", lambda return_json: config)
    monkeypatch.setattr(launch_module, "stream_stdout_and_stderr", lambda command: iter(()))

    launch_module._bake(client, record, ["app"], grant=True)

    assert sentinel not in definition.read_text()
    assert definition.stat().st_mode & 0o777 == 0o600
    assert yaml.safe_load(definition.read_text())["secrets"] == {"build-token": {"environment": "BUILD_TOKEN"}}
    assert "build-value" in definition.read_text()
    assert config["services"]["app"]["environment"]["TOKEN"] == sentinel


def test_bake_rejects_inline_build_secret_before_persistence() -> None:
    config = {
        "services": {"app": {"build": {"secrets": ["token"]}}},
        "secrets": {"token": {"content": "SECRET"}},
    }
    with pytest.raises(DigitalTwinUniverseError, match="inline content"):
        launch_module._build_config(config, ["app"])


@pytest.mark.needs_docker
@pytest.mark.lifecycle
def test_bake_does_not_expand_resolved_literals_again(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = universe_record()
    record.state_path.mkdir()
    client = compose_client(record.id)
    intended = {"SIMPLE": "$DTU_BAKE_HOST_VALUE", "BRACED": "${DTU_BAKE_HOST_VALUE}", "DOUBLE": "cost$$value"}
    config = {"services": {"app": {"build": {"context": str(state_root), "args": intended}}}}
    monkeypatch.setenv("DTU_BAKE_HOST_VALUE", "HOST_VALUE_MUST_NOT_ENTER_BUILD")
    monkeypatch.setattr(client.compose, "config", lambda return_json: config)
    monkeypatch.setattr(launch_module, "stream_stdout_and_stderr", lambda command: iter(()))
    assertions = ";".join(f"assert os.environ[{key!r}]=={value!r}" for key, value in intended.items())
    (state_root / "Dockerfile").write_text(
        "FROM docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f\n"
        + "".join(f"ARG {key}\n" for key in intended)
        + "RUN "
        + json.dumps(["python", "-c", "import os;" + assertions])
        + "\n"
    )

    launch_module._bake(client, record, ["app"], grant=False)
    result = subprocess.run(
        [
            "docker",
            "buildx",
            "bake",
            f"--allow=fs.read={state_root}",
            "--file",
            str(record.state_path / launch_module.BAKE_FILE),
            "--set",
            "app.output=type=cacheonly",
            "--progress=plain",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "HOST_VALUE_MUST_NOT_ENTER_BUILD" not in result.stdout


def test_a_failed_bake_is_a_failed_build() -> None:
    error = DockerException(
        ["docker", "buildx", "bake"],
        1,
        stderr=b'ERROR: failed to solve: process "/bin/sh -c false" did not complete successfully: exit code: 1\n',
    )

    translated = launch_module._launch_error(universe_record(), 12, error)

    assert translated.code == "build-failed"
    assert "digital-twin-universe destroy --id dtu-demo-0000" in translated.remedy


@pytest.mark.parametrize(
    ("command", "named"),
    [
        (["docker", "buildx", "bake"], "`docker buildx bake` failed"),
        (["docker", "compose", "up"], "`docker compose up` failed"),
    ],
)
def test_an_unrecognized_failure_names_the_command_that_failed(command: list[str], named: str) -> None:
    error = DockerException(command, 1, stderr=b"something else entirely")

    translated = launch_module._launch_error(universe_record(), 12, error)

    assert translated.code == "launch-failed"
    assert named in translated.message


@pytest.mark.parametrize(
    ("x_dtu", "buildx", "bakes", "compose_builds"),
    [
        (XDtu(rewrites=[REWRITE]), (0, 37), [(["app", "named"], True)], False),
        (XDtu(rewrites=[REWRITE]), (0, 16), [(["app", "named"], False)], False),
        (XDtu(rewrites=[REWRITE]), None, [], True),
        (XDtu(), (0, 37), [], True),
    ],
    ids=["gateway-with-entitlements", "gateway-before-entitlements", "gateway-without-buildx", "no-gateway"],
)
def test_only_a_gateway_universe_with_buildx_builds_before_compose(
    x_dtu: XDtu,
    buildx: tuple[int, int] | None,
    bakes: list[tuple[list[str], bool]],
    compose_builds: bool,
    state_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(x_dtu)
    entry = state_root / "profile.yaml"
    entry.write_text("name: demo\nservices: {}\n")
    profile = profile._replace(path=entry)
    monkeypatch.setattr(launch_module.runtime, "docker_host", lambda: "unix:///fixture.sock")
    monkeypatch.setattr(
        launch_module,
        "compose_client",
        lambda *args, **kwargs: SimpleNamespace(compose=SimpleNamespace(config=lambda **kwargs: profile.config)),
    )
    monkeypatch.setattr(validate_module, "_validated", lambda path, config: validate_module.Validation(report, profile))
    report = ProfileReport(
        path=PROFILE_PATH, name="demo", twin_machine="app", services=profile.services, ok=True, errors=[], warnings=[]
    )
    monkeypatch.setattr(validate_module, "validate", lambda path: validate_module.Validation(report, profile))
    monkeypatch.setattr(overlay, "export_ca", lambda record, rendered: None)
    monkeypatch.setattr(launch_module, "_buildx_version", lambda client: buildx)
    monkeypatch.setattr(launch_module, "measure", lambda record: Universe.model_construct(id=record.id))
    passes: list[tuple[list[str] | None, bool]] = []
    baked: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(
        launch_module,
        "_up",
        lambda client, record, timeout_seconds, services=None, build=True: passes.append((services, build)),
    )
    monkeypatch.setattr(launch_module, "_bake", lambda client, record, services, grant: baked.append((services, grant)))

    launch_module.launch(PROFILE_PATH)

    assert baked == bakes
    assert passes[-1] == (None, compose_builds)
