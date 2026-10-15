"""Destroy: take a universe down and forget it."""

from collections.abc import Callable
from typing import Literal

from python_on_whales import ClientNotFoundError, DockerClient
from python_on_whales.exceptions import DockerException, NoSuchNetwork, NoSuchVolume
import yaml

from digital_twin_universe.capabilities.universe import cleanup_volumes, state
from digital_twin_universe.capabilities.universe.compose import (
    PROJECT_LABEL,
    compose_client,
    record_host,
    translate_docker_error,
)
from digital_twin_universe.schemas import Destroyed

# A process running as PID 1 gets no default SIGTERM handler, so `sleep infinity`, `python -m http.server`, and
# mitmdump all sit out Compose's ten second grace period. Destroy removes the volumes anyway, so there is nothing
# a graceful stop could preserve; a second is for the processes that do handle the signal to exit on their own.
STOP_TIMEOUT_SECONDS = 1


def destroy(id: str) -> Destroyed:
    with state.lifecycle_lock(id):
        receipt = state.cleanup_receipt(id)
        if receipt is not None:
            record, result = receipt
            record_host(record)
            if not record.state_path.exists():
                return result.model_copy(update={"state_removed": True})
        return _destroy_owned(id)


def _destroy_owned(id: str) -> Destroyed:
    """Take down the project and forget it only when every owned resource is observed absent."""
    record = state.read(id)
    client = compose_client(id)
    record.phase = "destroying"
    state.write(record)
    inventories = _inventories(
        client, id, _external_resources(record, "volumes"), _external_resources(record, "networks")
    )
    try:
        before = {kind: inventory() for kind, inventory in inventories.items()}
        try:
            anonymous = cleanup_volumes.capture(client, record, _external_resources(record, "volumes"))
        except (ValueError, OSError) as error:
            return Destroyed(
                id=id,
                removed=[],
                outcome="unknown",
                remaining=before,
                errors={"volumes": str(error)},
                state_removed=False,
            )
        before["volumes"] = sorted(set(before["volumes"]) | set(anonymous.candidates))
        volumes = {}
        for name in before["volumes"]:
            try:
                volumes[name] = cleanup_volumes.identity(client, name)
            except NoSuchVolume:
                continue
        networks = {}
        for name in before["networks"]:
            try:
                network = client.network.inspect(name)
                if (network.labels or {}).get(PROJECT_LABEL) == id:
                    networks[name] = network.id
            except NoSuchNetwork:
                continue
        # Compose removes declared volumes by name, even when another project owns them.
        client.compose.down(volumes=False, remove_orphans=True, quiet=True, timeout=STOP_TIMEOUT_SECONDS)
    except (ClientNotFoundError, DockerException) as error:
        raise translate_docker_error(error) or error from error

    remaining: dict[str, list[str] | None] = {}
    errors: dict[str, str] = {}
    for name, network_id in networks.items():
        try:
            network = client.network.inspect(network_id)
            if network.id != network_id or (network.labels or {}).get(PROJECT_LABEL) != id:
                errors[f"network:{name}"] = "Network ownership or identity is uncertain; it was preserved."
                continue
            client.network.remove(network_id)
        except NoSuchNetwork:
            continue
        except (ClientNotFoundError, DockerException) as error:
            errors[f"network:{name}"] = str(translate_docker_error(error) or error)
    for name, identity in volumes.items():
        try:
            current = cleanup_volumes.identity(client, name)
            candidate = anonymous.candidates.get(name)
            expected = candidate.identity if candidate is not None else identity
            if current != expected or (candidate is None and current.project != id) or current.created_at is None:
                errors[f"volume:{name}"] = "Volume ownership or identity is uncertain; it was preserved."
                continue
            if candidate is not None and client.container.list(all=True, filters=[("volume", name)]):
                errors[f"volume:{name}"] = "Image volume is still attached to a container; it was preserved."
                continue
            client.volume.remove(name)
        except NoSuchVolume:
            continue
        except (ClientNotFoundError, DockerException) as error:
            errors[f"volume:{name}"] = str(translate_docker_error(error) or error)
    removed: list[str] = []
    for kind, inventory in inventories.items():
        try:
            names = inventory()
            if kind == "volumes":
                names = sorted(set(names) | set(cleanup_volumes.remaining(client, anonymous)))
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
        state.remember_cleanup(
            record, Destroyed(id=id, removed=removed, outcome="removed", remaining=remaining, state_removed=False)
        )
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


def _inventories(
    client: DockerClient, id: str, external_volumes: set[str], external_networks: set[str]
) -> dict[str, Callable[[], list[str]]]:
    """Exact project labels include stopped containers and orphans without matching unrelated names."""
    filters: list[tuple[Literal["label"], str]] = [("label", f"{PROJECT_LABEL}={id}")]
    return {
        "containers": lambda: [container.name for container in client.container.list(all=True, filters=filters)],
        "networks": lambda: [
            network.name for network in client.network.list(filters=filters) if network.name not in external_networks
        ],
        "volumes": lambda: [
            volume.name for volume in client.volume.list(filters=filters) if volume.name not in external_volumes
        ],
    }


def _external_resources(record: state.UniverseRecord, kind: Literal["networks", "volumes"]) -> set[str]:
    path = record.state_path / state.LIFECYCLE_FILE
    if not path.is_file():
        return set()
    config = yaml.safe_load(path.read_text()) or {}
    return {
        str(definition.get("name", name)).replace("$$", "$")
        for name, definition in (config.get(kind) or {}).items()
        if definition and definition.get("external")
    }
