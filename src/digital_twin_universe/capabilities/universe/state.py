"""Atomic management records and process-safe lifecycle ownership."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

from filelock import FileLock, Timeout
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from digital_twin_universe.capabilities.universe.runtime import CURRENT
from digital_twin_universe.schemas import Destroyed, DigitalTwinUniverseError, LifecyclePhase, UrlSpec

STATE_ROOT = Path.home() / ".digital-twin-universe" / "universes"
RECORD_FILE = "universe.json"
LIFECYCLE_FILE = "lifecycle.yaml"
ID_PREFIX = "dtu"


def root() -> Path:
    return (CURRENT.get().state_root or STATE_ROOT).expanduser().resolve()


class UniverseRecord(BaseModel):
    """Durable ownership receipt; Docker remains authoritative for actual container state."""

    id: str
    name: str
    description: str | None
    profile_path: Path
    twin_machine: str
    created_at: datetime
    urls: list[UrlSpec] = Field(default_factory=list)
    operation_id: str | None = None
    phase: LifecyclePhase = "ready"
    docker_host: str | None = None
    expected_services: list[str] = Field(default_factory=list)
    profile_digest: str | None = None
    request_profile: str | None = None
    _root: Path | None = PrivateAttr(default=None)

    @property
    def state_path(self) -> Path:
        return (self._root or root()) / self.id


def checked_id(id: str) -> str:
    if not re.fullmatch(r"dtu-[a-zA-Z0-9][a-zA-Z0-9_-]*", id):
        raise DigitalTwinUniverseError("universe-id-invalid", "Invalid universe id.", "Use the id returned by launch.")
    return id


def new_id(name: str, operation_id: str | None = None) -> str:
    if operation_id is not None:
        if not operation_id or len(operation_id) > 512:
            raise DigitalTwinUniverseError(
                "operation-id-invalid", "Operation id must contain 1 to 512 characters.", "Use a durable request id."
            )
        digest = hashlib.sha256(f"{root()}\0{operation_id}".encode()).hexdigest()[:32]
        return f"{ID_PREFIX}-op-{digest}"
    return f"{ID_PREFIX}-{name}-{secrets.token_hex(16)}"


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def write(record: UniverseRecord) -> None:
    checked_id(record.id)
    record._root = record._root or root()
    atomic_text(record.state_path / RECORD_FILE, record.model_dump_json(indent=2))


def allocate(record: UniverseRecord) -> None:
    checked_id(record.id)
    record._root = root()
    record.state_path.mkdir(parents=True, exist_ok=False, mode=0o700)
    write(record)


def read(id: str) -> UniverseRecord:
    path = root() / checked_id(id) / RECORD_FILE
    if not path.is_file():
        raise DigitalTwinUniverseError(
            "universe-not-found", f"No universe with id {id!r}: {path} does not exist.", _not_found_remedy(id)
        )
    try:
        record = UniverseRecord.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError) as error:
        raise DigitalTwinUniverseError(
            "state-unreadable",
            f"The record for {id} cannot be read.",
            "Preserve the state directory and inspect the named Docker project before cleanup.",
            universe_id=id,
        ) from error
    if record.id != id:
        raise DigitalTwinUniverseError(
            "state-identity-mismatch",
            f"The record does not belong to {id}.",
            "Preserve both records and inspect their ownership before cleanup.",
            universe_id=id,
        )
    record._root = root()
    return record


def read_all() -> list[UniverseRecord]:
    records: list[UniverseRecord] = []
    for id in recorded_ids():
        try:
            records.append(read(id))
        except DigitalTwinUniverseError:
            # A damaged receipt must remain discoverable through recorded_ids without hiding healthy environments.
            continue
    return sorted(records, key=lambda record: record.created_at)


def recorded_ids() -> list[str]:
    if not root().is_dir():
        return []
    return sorted(path.name for path in root().iterdir() if (path / RECORD_FILE).is_file())


def _not_found_remedy(id: str) -> str:
    known = recorded_ids()
    prefixes = [candidate for candidate in known if id.startswith(f"{candidate}-")]
    if prefixes:
        return (
            f"{id!r} looks like an image or container name; the universe id is {prefixes[-1]!r}, the id launch printed."
        )
    if known:
        return f"Use the id launch printed. Universes launched from this machine: {', '.join(known)}."
    return "Use the id launch printed. No universe has been launched from this machine."


@contextmanager
def lifecycle_lock(id: str) -> Iterator[None]:
    checked_id(id)
    with path_lock(root() / ".locks" / f"{id}.lock", id):
        yield


@contextmanager
def path_lock(path: Path, identity: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with FileLock(path, timeout=0):
            yield
    except Timeout as error:
        raise DigitalTwinUniverseError(
            "operation-busy",
            f"Another operation owns {identity}.",
            "Inspect its status and retry after that operation settles; do not launch a replacement.",
            universe_id=identity if identity.startswith("dtu-") else None,
        ) from error


def cleanup_receipt(id: str) -> tuple[UniverseRecord, Destroyed] | None:
    path = root() / ".destroyed" / f"{checked_id(id)}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        record = UniverseRecord.model_validate(data["record"])
        result = Destroyed.model_validate(data["result"])
    except (ValueError, KeyError, OSError) as error:
        raise DigitalTwinUniverseError(
            "state-unreadable",
            f"Cleanup receipt for {id} cannot be read.",
            "Inspect the retained receipt before repeating cleanup.",
            universe_id=id,
        ) from error
    if record.id != id or result.id != id:
        raise DigitalTwinUniverseError(
            "state-identity-mismatch",
            f"Cleanup receipt does not belong to {id}.",
            "Preserve the receipt for inspection.",
            universe_id=id,
        )
    record._root = root()
    return record, result


def remember_cleanup(record: UniverseRecord, result: Destroyed) -> None:
    data = {"record": record.model_dump(mode="json"), "result": result.model_dump(mode="json")}
    atomic_text(root() / ".destroyed" / f"{record.id}.json", json.dumps(data))


def remove(id: str) -> None:
    shutil.rmtree(root() / checked_id(id), ignore_errors=True)


def now() -> datetime:
    return datetime.now(UTC)
