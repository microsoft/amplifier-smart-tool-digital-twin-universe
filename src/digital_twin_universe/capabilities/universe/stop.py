"""Stop an owned stack without removing its containers, volumes, or recovery record."""

from python_on_whales import ClientNotFoundError
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL, compose_client
from digital_twin_universe.schemas import DigitalTwinUniverseError, Stopped


def stop(id: str, timeout_seconds: int = 10) -> Stopped:
    if not 1 <= timeout_seconds <= 300:
        raise DigitalTwinUniverseError(
            "timeout-invalid",
            "Stop timeout must be between 1 and 300 seconds.",
            "Choose a bounded shutdown grace period.",
        )
    with state.lifecycle_lock(id):
        record = state.read(id)
        client = compose_client(id)
        record.phase = "stopping"
        state.write(record)
        errors: list[str] = []
        try:
            owned = client.container.list(all=True, filters=[("label", f"{PROJECT_LABEL}={id}")])
            client.container.stop(owned, time=timeout_seconds)
        except (ClientNotFoundError, DockerException) as error:
            errors.append(str(error))
        try:
            containers = client.container.list(all=True, filters=[("label", f"{PROJECT_LABEL}={id}")])
            running = [
                container.name for container in containers if container.state.running or container.state.restarting
            ]
        except (ClientNotFoundError, DockerException) as error:
            return Stopped(id=id, outcome="unknown", running=None, errors=[*errors, str(error)])
        if not running and not errors:
            record.phase = "stopped"
            state.write(record)
            return Stopped(id=id, outcome="stopped", running=[])
        return Stopped(id=id, outcome="incomplete", running=running, errors=errors)
