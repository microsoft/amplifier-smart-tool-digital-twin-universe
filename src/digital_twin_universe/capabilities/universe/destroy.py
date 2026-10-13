"""Destroy: take a universe down and forget it."""

from collections.abc import Callable
from typing import Literal

from python_on_whales import ClientNotFoundError, DockerClient
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL, compose_client, translate_docker_error
from digital_twin_universe.schemas import Destroyed

# A process running as PID 1 gets no default SIGTERM handler, so `sleep infinity`, `python -m http.server`, and
# mitmdump all sit out Compose's ten second grace period. Destroy removes the volumes anyway, so there is nothing
# a graceful stop could preserve; a second is for the processes that do handle the signal to exit on their own.
STOP_TIMEOUT_SECONDS = 1


def destroy(id: str) -> Destroyed:
    """Take down the project and forget it only when every owned resource is observed absent."""
    record = state.read(id)
    client = compose_client(id)
    inventories = _inventories(client, id)
    try:
        before = {kind: inventory() for kind, inventory in inventories.items()}
        client.compose.down(volumes=True, remove_orphans=True, quiet=True, timeout=STOP_TIMEOUT_SECONDS)
    except (ClientNotFoundError, DockerException) as error:
        raise translate_docker_error(error) or error from error

    remaining: dict[str, list[str] | None] = {}
    errors: dict[str, str] = {}
    removed: list[str] = []
    for kind, inventory in inventories.items():
        try:
            names = inventory()
        except (ClientNotFoundError, DockerException) as error:
            remaining[kind] = None
            errors[kind] = str(translate_docker_error(error) or error)
        else:
            remaining[kind] = names
            removed.extend(name for name in before[kind] if name not in names)

    outcome: Literal["removed", "residue", "unknown"] = (
        "residue" if any(remaining.values()) else "unknown" if errors else "removed"
    )
    state_removed = False
    if outcome == "removed":
        state.remove(id)
        state_removed = not record.state_path.exists()
        if not state_removed:
            outcome = "residue"
            try:
                state.write(record)
            except OSError as error:
                errors["state"] = (
                    f"Could not remove {record.state_path} or restore its record: {error}. "
                    "Docker resources were observed absent; inspect and remove the remaining state directory manually."
                )
            else:
                errors["state"] = (
                    f"Could not remove {record.state_path}. Its record was restored. Check directory permissions "
                    f"and retry `digital-twin-universe destroy --id {id}`."
                )
    return Destroyed(
        id=id,
        removed=removed,
        outcome=outcome,
        remaining=remaining,
        errors=errors,
        state_removed=state_removed,
    )


def _inventories(client: DockerClient, id: str) -> dict[str, Callable[[], list[str]]]:
    """Exact project labels include stopped containers and orphans without matching unrelated names."""
    filters: list[tuple[Literal["label"], str]] = [("label", f"{PROJECT_LABEL}={id}")]
    return {
        "containers": lambda: [container.name for container in client.container.list(all=True, filters=filters)],
        "networks": lambda: [network.name for network in client.network.list(filters=filters)],
        "volumes": lambda: [volume.name for volume in client.volume.list(filters=filters)],
    }
