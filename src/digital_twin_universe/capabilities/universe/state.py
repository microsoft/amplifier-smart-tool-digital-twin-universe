"""The state directory: what the tool records about a universe so an id leads back to it."""

from datetime import UTC, datetime
from pathlib import Path
import secrets
import shutil

from pydantic import BaseModel, Field

from digital_twin_universe.schemas import DigitalTwinUniverseError, UrlSpec

STATE_ROOT = Path.home() / ".digital-twin-universe" / "universes"
RECORD_FILE = "universe.json"
ID_PREFIX = "dtu"


class UniverseRecord(BaseModel):
    """What launch writes to `universe.json`. Everything else about a universe is in Docker."""

    id: str
    name: str
    description: str | None
    profile_path: Path
    twin_machine: str
    created_at: datetime
    urls: list[UrlSpec] = Field(default_factory=list)

    @property
    def state_path(self) -> Path:
        return STATE_ROOT / self.id


def new_id(name: str) -> str:
    """`dtu-<profile name>-<4 hex>`: the Compose project name, so `docker compose -p <id>` reaches the same stack."""
    return f"{ID_PREFIX}-{name}-{secrets.token_hex(2)}"


def write(record: UniverseRecord) -> None:
    record.state_path.mkdir(parents=True, exist_ok=True)
    (record.state_path / RECORD_FILE).write_text(record.model_dump_json(indent=2), encoding="utf-8")


def read(id: str) -> UniverseRecord:
    """The record for an id, or `universe-not-found`."""
    path = STATE_ROOT / id / RECORD_FILE
    if not path.is_file():
        raise DigitalTwinUniverseError(
            "universe-not-found",
            f"No universe with id {id!r}: {path} does not exist.",
            _not_found_remedy(id),
        )
    return UniverseRecord.model_validate_json(path.read_text(encoding="utf-8"))


def read_all() -> list[UniverseRecord]:
    """Every record on this machine, oldest first. An empty or absent state root is an empty list."""
    if not STATE_ROOT.is_dir():
        return []
    records = [read(path.name) for path in STATE_ROOT.iterdir() if (path / RECORD_FILE).is_file()]
    return sorted(records, key=lambda record: record.created_at)


def _not_found_remedy(id: str) -> str:
    """Image and container names start with the universe id, so a longer id is usually one of those."""
    known = sorted(path.name for path in STATE_ROOT.iterdir()) if STATE_ROOT.is_dir() else []
    prefixes = [candidate for candidate in known if id.startswith(f"{candidate}-")]
    if prefixes:
        return (
            f"{id!r} looks like an image or container name; the universe id is {prefixes[-1]!r}, "
            'the "id" field `launch` printed.'
        )
    if known:
        return f'Use the "id" field `launch` printed. Universes launched from this machine: {", ".join(known)}.'
    return 'Use the "id" field `launch` printed. No universe has been launched from this machine.'


def remove(id: str) -> None:
    shutil.rmtree(STATE_ROOT / id, ignore_errors=True)


def now() -> datetime:
    return datetime.now(UTC)
