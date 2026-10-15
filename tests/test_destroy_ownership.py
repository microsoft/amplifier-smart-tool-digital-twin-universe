"""Cleanup never treats a declared resource name as proof of ownership."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from python_on_whales.exceptions import DockerException, NoSuchNetwork, NoSuchVolume

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import cleanup_volumes, destroy, runtime, state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL
from digital_twin_universe.schemas import BackendConfig


class Resources:
    def __init__(self, kind: str, resources: list[SimpleNamespace]) -> None:
        self.kind = kind
        self.resources = {resource.name: resource for resource in resources}
        self.removed: list[str] = []
        self.inspected: list[str] = []
        self.fail_remove = False

    def list(self, filters: list[tuple[str, str]]) -> list[SimpleNamespace]:
        label, value = filters[0][1].split("=", 1)
        return [resource for resource in self.resources.values() if resource.labels.get(label) == value]

    def inspect(self, name: str) -> SimpleNamespace:
        self.inspected.append(name)
        for resource in self.resources.values():
            if resource.name == name or getattr(resource, "id", None) == name:
                return SimpleNamespace(**vars(resource))
        error = NoSuchVolume if self.kind == "volume" else NoSuchNetwork
        raise error(["docker", self.kind, "inspect", name], 1)

    def remove(self, name: str) -> None:
        if self.fail_remove:
            raise DockerException(["docker", self.kind, "rm", name], 1, stderr=b"resource in use")
        resource = self.inspect(name)
        self.removed.append(name)
        del self.resources[resource.name]


class Harness:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.config = BackendConfig(state_root=tmp_path / "state", docker_host="unix:///owned-test.sock")
        self.id = "dtu-cleanup-owned"
        with runtime.configured(self.config):
            self.record = state.UniverseRecord(
                id=self.id,
                name="owned",
                description=None,
                profile_path=tmp_path / "missing.yaml",
                twin_machine="twin",
                created_at=state.now(),
                docker_host=self.config.docker_host,
            )
            state.write(self.record)
        self.volumes = Resources("volume", [])
        self.networks = Resources("network", [])
        self.after_down: Callable[[], None] = lambda: None
        self.down_calls = 0
        self.containers: dict[str, SimpleNamespace] = {}
        self.client = SimpleNamespace(
            compose=SimpleNamespace(down=self.down),
            volume=self.volumes,
            network=self.networks,
            container=SimpleNamespace(list=self.list_containers, inspect=lambda id: self.containers[id]),
            image=SimpleNamespace(inspect=lambda id: SimpleNamespace(config=SimpleNamespace(volumes={"/data": {}}))),
        )
        monkeypatch.setattr(destroy, "compose_client", lambda id: self.client)

    def list_containers(self, all: bool, filters: list[tuple[str, str]]) -> list[SimpleNamespace]:
        kind, value = filters[0]
        if kind == "volume":
            return [c for c in self.containers.values() if any(m.name == value for m in c.mounts)]
        assert kind == "label"
        key, owner = value.split("=", 1)
        return [c for c in self.containers.values() if c.config.labels.get(key) == owner]

    def down(self, **kwargs: Any) -> None:
        assert kwargs["volumes"] is False
        self.down_calls += 1
        self.containers = {k: c for k, c in self.containers.items() if c.config.labels[PROJECT_LABEL] != self.id}
        self.after_down()

    def anonymous(self) -> tuple[SimpleNamespace, SimpleNamespace]:
        volume = self.volume("a" * 64)
        volume.labels = {}
        container = SimpleNamespace(
            id="b" * 64,
            name="owned-twin",
            image="sha256:" + "c" * 64,
            config=SimpleNamespace(labels={PROJECT_LABEL: self.id}),
            host_config=SimpleNamespace(binds=[], mounts=[], volumes_from=[]),
            mounts=[SimpleNamespace(type="volume", name=volume.name, destination="/data")],
        )
        self.containers[container.id] = container
        return volume, container

    def volume(self, name: str, owner: str | None = None) -> SimpleNamespace:
        result = SimpleNamespace(
            name=name,
            created_at=state.now(),
            driver="local",
            mountpoint=Path("/volumes") / name,
            labels={PROJECT_LABEL: owner or self.id},
            options={},
        )
        self.volumes.resources[name] = result
        return result

    def network(self, name: str, owner: str | None = None) -> SimpleNamespace:
        result = SimpleNamespace(name=name, id=f"immutable-{name}", labels={PROJECT_LABEL: owner or self.id})
        self.networks.resources[name] = result
        return result


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


def test_destroy_removes_only_observed_owned_resources_and_preserves_external(harness: Harness) -> None:
    harness.volume("owned-data")
    harness.volume("peer-data", owner="other-project")
    harness.volume("external-data")
    harness.network("owned-custom")
    harness.network("external-network")
    (harness.record.state_path / state.LIFECYCLE_FILE).write_text(
        "services: {twin: {image: scratch}}\n"
        "volumes:\n  declared: {name: peer-data}\n  keep: {name: external-data, external: true}\n"
        "networks:\n  keep: {name: external-network, external: true}\n"
    )
    result = lib.destroy(harness.id, harness.config)
    assert result.complete
    assert set(result.removed) == {"owned-data", "owned-custom"}
    assert harness.volumes.removed == ["owned-data"]
    assert harness.networks.removed == ["immutable-owned-custom"]
    assert "peer-data" not in harness.volumes.inspected
    assert set(harness.volumes.resources) == {"peer-data", "external-data"}
    assert set(harness.networks.resources) == {"external-network"}
    assert not harness.record.state_path.exists()


@pytest.mark.parametrize("change", ["replacement", "owner", "unknown-creation"])
def test_destroy_preserves_volume_when_fresh_identity_is_uncertain(harness: Harness, change: str) -> None:
    volume = harness.volume("data")

    def change_identity() -> None:
        if change == "replacement":
            volume.created_at += timedelta(seconds=1)
        elif change == "owner":
            volume.labels = {PROJECT_LABEL: "other-project"}
        else:
            volume.created_at = None

    harness.after_down = change_identity
    result = lib.destroy(harness.id, harness.config)
    assert not result.complete
    assert result.outcome == ("unknown" if change == "owner" else "residue")
    assert "volume:data" in result.errors
    assert harness.volumes.removed == []
    assert harness.record.state_path.is_dir()


def test_destroy_retains_record_when_volume_is_still_in_use(harness: Harness) -> None:
    harness.volume("data")
    harness.volumes.fail_remove = True
    result = lib.destroy(harness.id, harness.config)
    assert result.outcome == "residue"
    assert result.remaining["volumes"] == ["data"]
    assert "volume:data" in result.errors
    assert harness.record.state_path.is_dir()


def test_destroy_uses_original_network_id_after_name_replacement(harness: Harness) -> None:
    network = harness.network("custom")
    harness.after_down = lambda: setattr(network, "id", "replacement-id")
    result = lib.destroy(harness.id, harness.config)
    assert result.outcome == "residue"
    assert result.remaining["networks"] == ["custom"]
    assert harness.networks.removed == []
    assert harness.record.state_path.is_dir()


def test_disappeared_volume_is_already_removed(harness: Harness) -> None:
    harness.volume("data")
    harness.after_down = lambda: harness.volumes.resources.clear()
    assert lib.destroy(harness.id, harness.config).complete
    assert harness.volumes.removed == []


def test_image_volume_provenance_is_durable_before_container_removal(harness: Harness) -> None:
    volume, _ = harness.anonymous()

    def inspect_receipt() -> None:
        receipt = cleanup_volumes.Receipt.model_validate_json(
            (harness.record.state_path / cleanup_volumes.RECEIPT_FILE).read_text()
        )
        assert receipt.candidates[volume.name].identity.created_at == volume.created_at
        assert receipt.candidates[volume.name].mounts[0].container_id == "b" * 64

    harness.after_down = inspect_receipt
    assert lib.destroy(harness.id, harness.config).complete
    assert harness.volumes.removed == [volume.name]


@pytest.mark.parametrize("source", ["bind", "mount", "inherited", "external"])
def test_explicit_or_inherited_image_destination_does_not_authorize_volume_removal(
    harness: Harness, source: str
) -> None:
    volume, container = harness.anonymous()
    if source == "bind":
        container.host_config.binds = [f"{volume.name}:/data:rw"]
    elif source == "mount":
        container.host_config.mounts = [SimpleNamespace(source=volume.name, target=Path("/data"))]
    elif source == "inherited":
        container.host_config.volumes_from = ["peer-container"]
    else:
        (harness.record.state_path / state.LIFECYCLE_FILE).write_text(
            f"services: {{twin: {{image: scratch}}}}\nvolumes: {{keep: {{external: true, name: {volume.name}}}}}\n"
        )
    assert lib.destroy(harness.id, harness.config).complete
    assert harness.volumes.removed == []
    assert volume.name in harness.volumes.resources


def test_foreign_reference_preserves_container_and_receipt_for_inspection(harness: Harness) -> None:
    _, container = harness.anonymous()
    peer = SimpleNamespace(**vars(container))
    peer.id = "d" * 64
    peer.config = SimpleNamespace(labels={PROJECT_LABEL: "peer"})
    harness.containers[peer.id] = peer
    result = lib.destroy(harness.id, harness.config)
    assert result.outcome == "unknown"
    assert not result.complete
    assert harness.down_calls == 0
    assert harness.volumes.removed == []
    assert harness.record.state_path.is_dir()


@pytest.mark.parametrize("config", [None, SimpleNamespace(volumes={})])
def test_unclassified_implicit_volume_preserves_container_and_state(harness: Harness, config: Any) -> None:
    harness.anonymous()
    harness.client.image.inspect = lambda id: SimpleNamespace(config=config)
    result = lib.destroy(harness.id, harness.config)
    assert result.outcome == "unknown"
    assert not result.complete
    assert harness.down_calls == 0
    assert harness.containers
    assert harness.volumes.removed == []
    assert harness.record.state_path.is_dir()


@pytest.mark.parametrize("change", ["replacement", "foreign-attachment", "inspection-fails"])
def test_changed_or_uninspectable_anonymous_volume_is_retained(harness: Harness, change: str) -> None:
    volume, container = harness.anonymous()

    def change_after_down() -> None:
        if change == "replacement":
            volume.created_at += timedelta(seconds=1)
        elif change == "foreign-attachment":
            container.config.labels[PROJECT_LABEL] = "peer"
            harness.containers[container.id] = container
        else:

            def fail(**kwargs: Any) -> list[SimpleNamespace]:
                raise DockerException(["docker", "ps"], 1, stderr=b"cannot inspect references")

            harness.client.container.list = fail

    harness.after_down = change_after_down
    result = lib.destroy(harness.id, harness.config)
    assert not result.complete
    assert result.remaining["volumes"] == [volume.name]
    assert f"volume:{volume.name}" in result.errors
    assert harness.volumes.removed == []
    assert harness.record.state_path.is_dir()


def test_lost_process_after_down_recovers_only_durable_volume_identity(harness: Harness) -> None:
    volume, _ = harness.anonymous()

    def crash() -> None:
        raise RuntimeError("process lost after down")

    harness.after_down = crash
    with pytest.raises(RuntimeError, match="process lost"):
        lib.destroy(harness.id, harness.config)
    assert not harness.containers
    assert volume.name in harness.volumes.resources
    harness.after_down = lambda: None
    assert lib.destroy(harness.id, harness.config).complete
    assert harness.volumes.removed == [volume.name]


def test_failed_provenance_write_prevents_container_removal(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.anonymous()
    atomic = state.atomic_text

    def fail(path: Path, text: str) -> None:
        if path.name == cleanup_volumes.RECEIPT_FILE:
            raise OSError("disk full")
        atomic(path, text)

    monkeypatch.setattr(state, "atomic_text", fail)
    result = lib.destroy(harness.id, harness.config)
    assert result.outcome == "unknown"
    assert harness.down_calls == 0
    assert harness.containers
    assert harness.volumes.removed == []
