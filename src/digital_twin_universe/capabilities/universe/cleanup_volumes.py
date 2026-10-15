"""Retain image-volume provenance before cleanup removes the owning containers."""

from datetime import datetime
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field
from python_on_whales import DockerClient
from python_on_whales.exceptions import NoSuchVolume

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL, record_host
from digital_twin_universe.schemas import DigitalTwinUniverseError

RECEIPT_FILE = "cleanup-volumes.json"


class VolumeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    created_at: datetime | None
    driver: str
    mountpoint: Path
    project: str | None
    labels: dict[str, str]
    options: dict[str, str]


class MountProof(BaseModel):
    container_id: str
    image_id: str
    destination: str


class Candidate(BaseModel):
    identity: VolumeIdentity
    mounts: list[MountProof]


class Receipt(BaseModel):
    universe_id: str
    docker_host: str | None
    candidates: dict[str, Candidate] = Field(default_factory=dict)


def identity(client: DockerClient, name: str) -> VolumeIdentity:
    volume = client.volume.inspect(name)
    labels = volume.labels or {}
    return VolumeIdentity(
        name=volume.name,
        created_at=volume.created_at,
        driver=volume.driver,
        mountpoint=volume.mountpoint,
        project=labels.get(PROJECT_LABEL),
        labels=labels,
        options=volume.options or {},
    )


def _anonymous(value: VolumeIdentity) -> bool:
    return bool(
        re.fullmatch(r"[0-9a-f]{64}", value.name)
        and value.created_at is not None
        and value.driver == "local"
        and not value.options
        and value.labels in ({}, {"com.docker.volume.anonymous": ""})
    )


def _read(record: state.UniverseRecord) -> Receipt:
    path = record.state_path / RECEIPT_FILE
    if not path.exists():
        return Receipt(universe_id=record.id, docker_host=record_host(record))
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Receipt is too large")
        receipt = Receipt.model_validate_json(path.read_text())
        if receipt.universe_id != record.id or receipt.docker_host != record_host(record):
            raise ValueError("Receipt owner differs")
        for name, candidate in receipt.candidates.items():
            if name != candidate.identity.name or not _anonymous(candidate.identity) or not candidate.mounts:
                raise ValueError("Invalid volume provenance")
            for mount in candidate.mounts:
                if not (
                    re.fullmatch(r"[0-9a-f]{64}", mount.container_id)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", mount.image_id)
                    and mount.destination.startswith("/")
                ):
                    raise ValueError("Invalid mount provenance")
        return receipt
    except (ValueError, OSError) as error:
        raise DigitalTwinUniverseError(
            "state-unreadable",
            "The retained cleanup volume receipt cannot be verified.",
            "Preserve the universe and inspect its cleanup receipt before retrying.",
            universe_id=record.id,
        ) from error


def capture(client: DockerClient, record: state.UniverseRecord, external: set[str]) -> Receipt:
    """Only image defaults with no supplied or inherited source establish anonymous ownership."""
    receipt = _read(record)
    containers = client.container.list(all=True, filters=[("label", f"{PROJECT_LABEL}={record.id}")])
    owned_ids = {container.id for container in containers}
    for observed in containers:
        container = client.container.inspect(observed.id)
        if (container.config.labels or {}).get(PROJECT_LABEL) != record.id:
            raise ValueError("Container ownership changed before cleanup")
        host = container.host_config
        if host.volumes_from:
            continue
        image = None
        for mount in container.mounts:
            if mount.type != "volume" or not mount.name or not mount.destination or mount.name in external:
                continue
            # Docker can inherit an image VOLUME destination while an explicit mount supplies peer data.
            if any(mount.destination in bind.split(":") for bind in (host.binds or [])) or any(
                str(spec.target) == mount.destination for spec in (host.mounts or [])
            ):
                continue
            if image is None:
                image = client.image.inspect(container.image)
            if image.config is None or mount.destination not in (image.config.volumes or {}):
                continue
            value = identity(client, mount.name)
            if value.project == record.id:
                continue  # The ordinary labeled-volume inventory owns this one.
            if not _anonymous(value):
                raise ValueError("Image volume provenance is uncertain; its container was preserved")
            refs = client.container.list(all=True, filters=[("volume", value.name)])
            if not refs or any(ref.id not in owned_ids for ref in refs):
                raise ValueError("Image volume has uncertain or foreign container references")
            proof = MountProof(container_id=container.id, image_id=container.image, destination=mount.destination)
            candidate = receipt.candidates.get(value.name)
            if candidate is not None and candidate.identity != value:
                raise ValueError("Image volume identity changed since its retained cleanup receipt")
            if candidate is None:
                receipt.candidates[value.name] = Candidate(identity=value, mounts=[proof])
            elif proof not in candidate.mounts:
                candidate.mounts.append(proof)
    # Losing this write must prevent container removal: after down, mount provenance is no longer observable.
    state.atomic_text(record.state_path / RECEIPT_FILE, receipt.model_dump_json(indent=2))
    return receipt


def remaining(client: DockerClient, receipt: Receipt) -> list[str]:
    names = []
    for name in receipt.candidates:
        try:
            client.volume.inspect(name)
        except NoSuchVolume:
            continue
        names.append(name)
    return names
