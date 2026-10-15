"""A failed launch must not remove a peer volume named in its retained definition."""

from contextlib import suppress
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest
from python_on_whales import DockerClient
from python_on_whales.exceptions import NoSuchNetwork, NoSuchVolume
import yaml

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import runtime, state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL
from digital_twin_universe.schemas import BackendConfig

pytestmark = [pytest.mark.needs_docker, pytest.mark.live]


def test_failed_launch_cleanup_preserves_peer_data_and_removes_owned_resources(tmp_path: Path) -> None:
    token = uuid4().hex
    id = f"dtu-cleanup-test-{token}"
    endpoint = json.loads(
        subprocess.check_output(
            ["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            text=True,
            timeout=15,
        )
    )
    config = BackendConfig(state_root=tmp_path / "state", docker_host=endpoint)
    client = DockerClient(host=endpoint)
    volumes: list[str] = []
    networks: list[str] = []
    own, peer, external = f"{id}-own", f"{id}-peer", f"{id}-external"
    network_name = f"{id}-custom"
    fixture_label = "org.digital-twin-universe.cleanup-test"
    try:
        for name, project in [(own, id), (peer, f"{id}-peer"), (external, id)]:
            client.volume.create(name, labels={PROJECT_LABEL: project, fixture_label: token})
            volumes.append(name)
        network = client.network.create(network_name, labels={PROJECT_LABEL: id, fixture_label: token})
        networks.append(network.id)
        client.container.run(
            "alpine:3.20",
            ["sh", "-c", "printf sentinel > /data/marker"],
            volumes=[(peer, "/data")],
            networks=["none"],
            read_only=True,
            remove=True,
        )
        with runtime.configured(config):
            record = state.UniverseRecord(
                id=id,
                name="cleanup-test",
                description=None,
                profile_path=tmp_path / "removed.yaml",
                twin_machine="twin",
                created_at=state.now(),
                docker_host=endpoint,
                phase="failed",
            )
            state.write(record)
            state.atomic_text(
                record.state_path / state.LIFECYCLE_FILE,
                yaml.safe_dump(
                    {
                        "services": {"twin": {"image": "scratch"}},
                        "volumes": {"unused-peer": {"name": peer}, "external": {"name": external, "external": True}},
                        "networks": {"unused-custom": {"name": network_name}},
                    }
                ),
            )
        result = lib.destroy(id, config)
        assert result.complete
        assert set(result.removed) == {own, network_name}
        assert (
            client.container.run(
                "alpine:3.20",
                ["cat", "/data/marker"],
                volumes=[(peer, "/data", "ro")],
                networks=["none"],
                read_only=True,
                remove=True,
            )
            == "sentinel"
        )
        assert client.volume.inspect(external).labels[fixture_label] == token
        assert not record.state_path.exists()
        assert lib.destroy(id, config).complete
    finally:
        for network_id in networks:
            with suppress(NoSuchNetwork):
                assert client.network.inspect(network_id).labels[fixture_label] == token
                client.network.remove(network_id)
        for name in volumes:
            with suppress(NoSuchVolume):
                assert client.volume.inspect(name).labels[fixture_label] == token
                client.volume.remove(name)
