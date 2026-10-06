"""Compose access shared by every universe capability: the client, error translation, and measuring a project."""

from pathlib import Path
from typing import Any

from python_on_whales import ClientNotFoundError, DockerClient
from python_on_whales.components.container.cli_wrapper import Container
from python_on_whales.exceptions import DockerException, NoSuchContainer

from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.schemas import (
    DigitalTwinUniverseError,
    Health,
    Service,
    Universe,
    UniverseState,
    Url,
    UrlSpec,
)

SERVICE_LABEL = "com.docker.compose.service"
PROJECT_LABEL = "com.docker.compose.project"
DAEMON_DOWN_MARKERS = ("Cannot connect to the Docker daemon", "docker daemon is not running", "error during connect")


def compose_client(
    project: str | None = None, compose_files: list[Path] | None = None, profiles: list[str] | None = None
) -> DockerClient:
    """A Docker client scoped to one Compose project. With only a project name, Compose finds the stack by label."""
    files: list[str | Path] = list(compose_files or [])
    return DockerClient(compose_project_name=project, compose_files=files, compose_profiles=list(profiles or []))


def docker_unavailable() -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        "docker-unavailable",
        "Docker is not usable from this process.",
        "Run `digital-twin-universe check` for the missing prerequisite and its remedy.",
    )


def translate_docker_error(error: Exception) -> DigitalTwinUniverseError | None:
    """The `docker-unavailable` failure when the error means Docker itself is missing or down, else None."""
    if isinstance(error, ClientNotFoundError):
        return docker_unavailable()
    if isinstance(error, DockerException):
        stderr = error.stderr or ""
        if any(marker in stderr for marker in DAEMON_DOWN_MARKERS):
            return docker_unavailable()
    return None


def project_containers(project: str) -> list[Container]:
    """Every container of a Compose project, running or not."""
    try:
        return compose_client(project).compose.ps(all=True)
    except (ClientNotFoundError, DockerException) as error:
        raise translate_docker_error(error) or error from error


def compose_containers() -> dict[str, list[Container]]:
    """Every Compose-managed container on the host, running or not, grouped by project name."""
    try:
        containers = compose_client().container.list(all=True, filters=[("label", PROJECT_LABEL)])
    except (ClientNotFoundError, DockerException) as error:
        raise translate_docker_error(error) or error from error
    grouped: dict[str, list[Container]] = {}
    for container in containers:
        # Each attribute read is a fresh inspect, and a container removed since the list is no longer there to read.
        try:
            project = (container.config.labels or {})[PROJECT_LABEL]
        except NoSuchContainer:
            continue
        grouped.setdefault(project, []).append(container)
    return grouped


def running_twin(record: UniverseRecord) -> Container:
    """The twin's container, or `twin-not-running` with the state it is in instead."""
    twin = next((c for c in project_containers(record.id) if service_name(c) == record.twin_machine), None)
    if twin is None or twin.state.status != "running":
        found = "not present" if twin is None else twin.state.status
        raise DigitalTwinUniverseError(
            "twin-not-running",
            f"The twin {record.twin_machine!r} of universe {record.id} is {found}.",
            f"Inspect it with `docker compose -p {record.id} ps` and `logs`, or destroy and launch again.",
        )
    return twin


def measure(record: UniverseRecord, containers: list[Container] | None = None) -> Universe:
    """The universe a record describes, as Docker reports it now. `containers` skips the lookup when already listed."""
    if containers is None:
        containers = project_containers(record.id)
    services = [_service(container) for container in containers]
    twin = next((container for container in containers if service_name(container) == record.twin_machine), None)
    return Universe(
        id=record.id,
        name=record.name,
        description=record.description,
        profile_path=record.profile_path,
        twin_machine=record.twin_machine,
        state=universe_state(services),
        services=services,
        urls=urls(twin.network_settings.ports or {}, record.urls) if twin is not None else [],
        state_path=record.state_path,
        created_at=record.created_at,
    )


def universe_state(services: list[Service]) -> UniverseState:
    """Running when everything is up and healthy, starting while any is becoming so, degraded when any has failed."""
    if not any(service.state == "running" for service in services):
        return "stopped"
    if any(service.state != "running" or service.health == "unhealthy" for service in services):
        return "degraded"
    if any(service.health == "starting" for service in services):
        return "starting"
    return "running"


def _service(container: Container) -> Service:
    health = container.state.health.status if container.state.health is not None else None
    return Service(
        name=service_name(container),
        state=container.state.status or "unknown",
        health=_health(health),
        image=container.config.image or "",
    )


def _health(status: str | None) -> Health | None:
    match status:
        case "starting" | "healthy" | "unhealthy":
            return status
        case _:
            return None


def service_name(container: Container) -> str:
    return (container.config.labels or {}).get(SERVICE_LABEL, container.name)


def urls(ports: dict[str, Any], specs: list[UrlSpec]) -> list[Url]:
    """The twin's published ports as the host reaches them, in host port order.

    `ports` is Docker's `NetworkSettings.Ports`: `"80/tcp"` to its host bindings, one per address family. A port
    the profile describes in `x-dtu.urls` gets one URL per entry, in the profile's order; any other gets `/`.
    """
    published: dict[int, int] = {}
    for exposed, bindings in ports.items():
        container_port, _, protocol = exposed.partition("/")
        for binding in bindings or []:
            if protocol == "tcp" and binding.get("HostPort"):
                published[int(container_port)] = int(binding["HostPort"])
    result: list[Url] = []
    for container_port, host_port in sorted(published.items(), key=lambda item: item[1]):
        described = [spec for spec in specs if spec.port == container_port] or [UrlSpec(port=container_port)]
        result += [
            Url(url=f"http://{spec.host}:{host_port}{spec.path}", port=host_port, path=spec.path, label=spec.label)
            for spec in described
        ]
    return result
