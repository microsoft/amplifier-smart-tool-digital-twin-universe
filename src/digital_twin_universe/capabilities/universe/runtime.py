"""Per-call backend configuration. Nested calls inherit it; other threads and tasks do not."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import subprocess

from python_on_whales import DockerClient

from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError

DEFAULT_CONFIG = BackendConfig()
CURRENT: ContextVar[BackendConfig] = ContextVar("dtu_backend", default=DEFAULT_CONFIG)


@contextmanager
def configured(config: BackendConfig | None = None) -> Iterator[None]:
    token = CURRENT.set(config or CURRENT.get())
    try:
        yield
    finally:
        CURRENT.reset(token)


def docker_host() -> str:
    """Pin the currently selected endpoint once, before allocating an environment."""
    explicit = CURRENT.get().docker_host
    if explicit:
        return explicit
    context = os.environ.get("DOCKER_CONTEXT")
    if not context and os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]
    command = [*DockerClient().docker_cmd, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]
    if context:
        command.append(context)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        host = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise DigitalTwinUniverseError(
            "docker-endpoint-unknown",
            "The selected Docker endpoint could not be resolved.",
            "Select a usable context or provide BackendConfig.docker_host.",
        ) from error
    if not isinstance(host, str) or not host:
        raise DigitalTwinUniverseError(
            "docker-endpoint-missing",
            "The selected Docker context has no endpoint.",
            "Select a usable context or provide BackendConfig.docker_host.",
        )
    return host
