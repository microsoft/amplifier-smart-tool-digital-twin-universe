"""Destroy: take a universe down and forget it."""

from python_on_whales import ClientNotFoundError
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import (
    PROJECT_LABEL,
    compose_client,
    project_containers,
    translate_docker_error,
)
from digital_twin_universe.schemas import Destroyed

# A process running as PID 1 gets no default SIGTERM handler, so `sleep infinity`, `python -m http.server`, and
# mitmdump all sit out Compose's ten second grace period. Destroy removes the volumes anyway, so there is nothing
# a graceful stop could preserve; a second is for the processes that do handle the signal to exit on their own.
STOP_TIMEOUT_SECONDS = 1


def destroy(id: str) -> Destroyed:
    """Remove every container, network, and volume of the universe, then its state directory. Images stay."""
    state.read(id)
    client = compose_client(id)
    label = f"{PROJECT_LABEL}={id}"
    try:
        removed = [container.name for container in project_containers(id)]
        removed += [network.name for network in client.network.list(filters=[("label", label)])]
        removed += [volume.name for volume in client.volume.list(filters=[("label", label)])]
        client.compose.down(volumes=True, remove_orphans=True, quiet=True, timeout=STOP_TIMEOUT_SECONDS)
    except (ClientNotFoundError, DockerException) as error:
        raise translate_docker_error(error) or error from error
    state.remove(id)
    return Destroyed(id=id, removed=removed)
