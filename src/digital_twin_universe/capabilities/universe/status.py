"""Status and list: what the tool knows about a universe, measured against Docker now."""

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import compose_containers, measure
from digital_twin_universe.schemas import Universe


def status(id: str) -> Universe:
    """One universe, measured now."""
    return measure(state.read(id))


def list_universes() -> list[Universe]:
    """Every universe launched from this machine, oldest first, each measured now."""
    records = state.read_all()
    if not records:
        return []
    containers = compose_containers()
    return [measure(record, containers.get(record.id, [])) for record in records]
