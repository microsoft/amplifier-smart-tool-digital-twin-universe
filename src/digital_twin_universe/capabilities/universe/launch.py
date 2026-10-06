"""Launch: from a profile to a running, ready universe."""

from pathlib import Path
import re
import sys
from typing import Any

from python_on_whales import ClientNotFoundError, DockerClient
from python_on_whales.exceptions import DockerException
from python_on_whales.utils import stream_stdout_and_stderr
import yaml

from digital_twin_universe.capabilities.universe import overlay, state
from digital_twin_universe.capabilities.universe import validate as validate_module
from digital_twin_universe.capabilities.universe.compose import compose_client, measure, translate_docker_error
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.schemas import DigitalTwinUniverseError, ProfileReport, Universe

LOG_TAIL = 20
PORT_IN_USE_PATTERN = re.compile(
    r"(?:failed to bind host port [^:]*:(?P<bound>\d+)/\w+: address already in use)"
    r"|(?:Bind for [^:]*:(?P<allocated>\d+) failed: port is already allocated)"
)
UNHEALTHY_PATTERN = re.compile(r"container (?P<container>\S+) is unhealthy")
TIMEOUT_MARKER = "application not healthy after"
BUILD_FAILED_MARKER = "failed to solve"
BAKE_FILE = "bake.yaml"
BUILDX_VERSION_PATTERN = re.compile(r"\bv(?P<major>\d+)\.(?P<minor>\d+)")
BAKE_ALLOW_VERSION = (0, 17)


def launch(profile: str | Path, timeout_seconds: int = 600) -> Universe:
    """Validate the profile, record the universe, bring the stack up, and return once every healthcheck passes."""
    validation = validate_module.validate(profile)
    if validation.unreadable is not None:
        raise validation.unreadable
    if validation.profile is None:
        raise _profile_invalid(validation.report)
    loaded = validation.profile
    record = UniverseRecord(
        id=state.new_id(loaded.name),
        name=loaded.name,
        description=loaded.description,
        profile_path=loaded.path,
        twin_machine=loaded.twin_machine,
        created_at=state.now(),
        urls=loaded.x_dtu.urls,
    )
    state.write(record)
    rendered = overlay.render(record, loaded)
    client = compose_client(record.id, [loaded.path, *rendered.files])
    try:
        # What the universe provides comes up first, so that the certificate authority exists before anything is
        # built with it and the git server answers before a build or a command clones from it.
        if rendered.bootstrap:
            _up(client, record, timeout_seconds, services=rendered.bootstrap)
            overlay.export_ca(record, rendered)
        buildx = _buildx_version(client) if rendered.gateway_builds else None
        if buildx is not None:
            _bake(client, record, rendered.gateway_builds, grant=buildx >= BAKE_ALLOW_VERSION)
        _up(client, record, timeout_seconds, build=buildx is None)
    except (ClientNotFoundError, DockerException) as error:
        raise _launch_error(record, timeout_seconds, error) from error
    return measure(record)


def _up(
    client: DockerClient,
    record: UniverseRecord,
    timeout_seconds: int,
    services: list[str] | None = None,
    build: bool = True,
) -> None:
    """One `compose up` pass. Compose reports progress on stderr, which is passed through so a person sees it."""
    for _, line in client.compose.up(
        services=services,
        build=build,
        no_build=not build,
        wait=True,
        wait_timeout=timeout_seconds,
        stream_logs=True,
    ):
        sys.stderr.write(line.decode(errors="replace"))


def _buildx_version(client: DockerClient) -> tuple[int, int] | None:
    """Buildx's major and minor version, or None when there is no Buildx or it does not say which it is."""
    try:
        reported = client.buildx.version()
    except DockerException:
        return None
    version = BUILDX_VERSION_PATTERN.search(reported)
    return (int(version["major"]), int(version["minor"])) if version is not None else None


def _bake(client: DockerClient, record: UniverseRecord, services: list[str], grant: bool) -> None:
    """Build the images that reach the gateway over the host network, before Compose starts them.

    Compose cannot build these itself: it hands Bake a definition without `--allow=network.host`, which Buildx
    0.37.2 and later refuse, and whose `network` Buildx before 0.17 ignores. Bake reads the stack here as Compose
    resolved it, so paths, `.env`, and interpolation mean what they mean to Compose, and tags the images the overlay
    names. `grant` passes the entitlements, which Buildx before 0.17 neither checks nor accepts.
    """
    config = client.compose.config(return_json=True)
    definition = record.state_path / BAKE_FILE
    definition.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    entitlements = ["network.host", *(f"fs.read={path}" for path in _local_contexts(config, services))]
    command = [
        *client.docker_cmd,
        "buildx",
        "bake",
        "--progress=plain",
        "--load",
        *([f"--allow={entitlement}" for entitlement in entitlements] if grant else []),
        "--file",
        definition,
    ]
    for _, line in stream_stdout_and_stderr(command):
        sys.stderr.write(line.decode(errors="replace"))


def _local_contexts(config: dict[str, Any], services: list[str]) -> list[str]:
    """Every directory on the host the builds read, each of which Bake reads only when granted."""
    paths: set[str] = set()
    for name in services:
        build = config["services"][name]["build"]
        for context in [build.get("context"), *(build.get("additional_contexts") or {}).values()]:
            if context and Path(context).is_absolute():
                paths.add(context)
    return sorted(paths)


def _profile_invalid(report: ProfileReport) -> DigitalTwinUniverseError:
    """Nothing is recorded or started for a profile that is not a universe; the report's errors are the reason."""
    return DigitalTwinUniverseError(
        "profile-invalid",
        f"{report.path} cannot be launched:\n"
        + "\n".join(f"  - [{finding.code}] {finding.message}" for finding in report.errors),
        f"Fix each one; `digital-twin-universe validate-profile --profile {report.path}` lists them again, with a remedy for each.",
    )


def _launch_error(record: UniverseRecord, timeout_seconds: int, error: Exception) -> DigitalTwinUniverseError:
    translated = translate_docker_error(error)
    if translated is not None:
        return translated
    stderr = (getattr(error, "stderr", None) or "").strip()
    left_running = (
        f"Whatever started is left as is under `docker compose -p {record.id}` for inspection; "
        f"`digital-twin-universe destroy --id {record.id}` clears it."
    )
    port = PORT_IN_USE_PATTERN.search(stderr)
    if port is not None:
        number = port.group("bound") or port.group("allocated")
        return DigitalTwinUniverseError(
            "port-in-use",
            f"Universe {record.id} could not publish port {number}: something on the host already holds it.",
            f"Free port {number} or change the host side of the `ports` entry in {record.profile_path}. {left_running}",
        )
    if BUILD_FAILED_MARKER in stderr:
        return DigitalTwinUniverseError(
            "build-failed",
            f"An image build for universe {record.id} failed:\n{_tail(stderr)}",
            f"Fix the Dockerfile the profile at {record.profile_path} builds and launch again. {left_running}",
        )
    unhealthy = UNHEALTHY_PATTERN.search(stderr)
    if unhealthy is not None:
        container = unhealthy.group("container")
        return DigitalTwinUniverseError(
            "unhealthy",
            f"Container {container} of universe {record.id} failed its healthcheck. Its last log lines:\n"
            f"{_container_logs(container)}",
            f"Fix what the healthcheck in {record.profile_path} probes, or the healthcheck itself. {left_running}",
        )
    if TIMEOUT_MARKER in stderr:
        return DigitalTwinUniverseError(
            "timeout",
            f"Universe {record.id} was still starting after {timeout_seconds}s.",
            f"Raise `timeout_seconds`, or inspect it with `docker compose -p {record.id} ps` and `logs`. {left_running}",
        )
    command = "docker buildx bake" if "bake" in (getattr(error, "docker_command", None) or []) else "docker compose up"
    return DigitalTwinUniverseError(
        "launch-failed",
        f"`{command}` failed for universe {record.id}:\n{_tail(stderr)}",
        f"Read Docker's message above. {left_running}",
    )


def _container_logs(container: str) -> str:
    try:
        return str(compose_client().container.logs(container, tail=LOG_TAIL)).strip()
    except DockerException as error:
        return f"(logs unavailable: {(error.stderr or '').strip()})"


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-LOG_TAIL:])
