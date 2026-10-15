"""Cleanup never treats a declared resource name as proof of ownership."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from python_on_whales.exceptions import DockerException, NoSuchNetwork, NoSuchVolume

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import destroy, runtime, state
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
        client = SimpleNamespace(
            compose=SimpleNamespace(down=self.down),
            volume=self.volumes,
            network=self.networks,
            container=SimpleNamespace(list=lambda **kwargs: []),
        )
        monkeypatch.setattr(destroy, "compose_client", lambda id: client)

    def down(self, **kwargs: Any) -> None:
        assert kwargs["volumes"] is False
        self.down_calls += 1
        self.after_down()

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
