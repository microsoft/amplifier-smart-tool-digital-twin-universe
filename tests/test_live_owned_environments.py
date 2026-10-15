"""Owned lifecycle acceptance against real Docker, using only private synthetic inputs."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import tarfile

from conftest import GIT, MakeRepository
import pytest

from digital_twin_universe import lib
from digital_twin_universe.schemas import BackendConfig, DigitalTwinUniverseError

pytestmark = [pytest.mark.needs_docker, pytest.mark.live]
IMAGE = "docker.io/library/python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"


def test_parallel_owned_environments_retain_work_without_replaying(
    tmp_path: Path, make_repository: MakeRepository
) -> None:
    repository = make_repository("source", {"tracked.txt": "committed\n", "deleted.txt": "delete me\n"})
    source = tmp_path / "linked"
    subprocess.run([*GIT, "-C", str(repository), "worktree", "add", "-b", "test-input", str(source)], check=True)
    (source / "tracked.txt").write_text("dirty snapshot\n")
    (source / "deleted.txt").unlink()
    (source / "untracked.txt").write_text("selected untracked\n")
    snapshot = lib.export_repository_snapshot(source, tmp_path / "source.tar")
    with tarfile.open(snapshot.path) as archive:
        names = archive.getnames()
        assert "tracked.txt" in names
        assert "untracked.txt" in names
        assert "deleted.txt" not in names
        assert ".git" not in names
    profile = tmp_path / "compose.json"
    document = {
        "name": "owned-test",
        "services": {
            "twin": {
                "image": IMAGE,
                "command": ["sleep", "infinity"],
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 64,
                "mem_limit": "256m",
                "cpus": 1,
                "healthcheck": {"test": ["CMD", "true"], "interval": "1s"},
                "volumes": ["workspace:/workspace", "artifacts:/artifacts"],
                "networks": ["isolated"],
            }
        },
        "volumes": {"workspace": {}, "artifacts": {}},
        "networks": {"isolated": {"internal": True}},
        "x-dtu": {"twin_machine": "twin"},
    }
    profile.write_text(json.dumps(document))
    config = BackendConfig(state_root=tmp_path / "backend")
    ids = [lib.launch_id(operation, config=config) for operation in ("first", "peer", "failed")]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lib.launch, profile, 60, operation, config) for operation in ("first", "peer")]
            first, peer = [future.result() for future in futures]
        assert [first.id, peer.id] == ids[:2]
        assert first.id != peer.id
        assert lib.launch(profile, operation_id="first", config=config).id == first.id
        assert len(lib.recorded_ids(config=config)) == 2
        lib.push_files(first.id, snapshot.path, "/artifacts/source.tar", config=config)
        prepared = lib.execute(
            first.id,
            "python -c \"import tarfile; tarfile.open('/artifacts/source.tar').extractall('/workspace', filter='data')\""
            " && cat /workspace/tracked.txt /workspace/untracked.txt && echo retained-result > /artifacts/result.txt",
            config=config,
        )
        if prepared.exit_code:
            print(
                "SOURCE_COPY_DIAG",
                lib.execute(
                    first.id, "id; ls -ln /artifacts; stat /artifacts/source.tar", config=config
                ).model_dump_json(),
            )
        assert prepared.exit_code == 0, prepared.stderr
        assert prepared.stdout == "dirty snapshot\nselected untracked\n"
        with pytest.raises(DigitalTwinUniverseError) as mismatch:
            lib.status(first.id, config=config.model_copy(update={"docker_host": "unix:///unreachable-owned.sock"}))
        assert mismatch.value.code == "docker-endpoint-mismatch"
        failure_profile = tmp_path / "failed.json"
        document["services"]["twin"]["command"] = ["false"]
        failure_profile.write_text(json.dumps(document))
        with pytest.raises(DigitalTwinUniverseError) as failure:
            lib.launch(failure_profile, timeout_seconds=15, operation_id="failed", config=config)
        assert failure.value.universe_id == ids[2]
        assert lib.destroy(ids[2], config=config).complete
        assert lib.status(peer.id, config=config).state == "running"
        assert lib.stop(first.id, timeout_seconds=1, config=config).complete
        assert lib.status(first.id, config=config).state == "stopped"
        exported = lib.export_files(first.id, "/artifacts", tmp_path / "artifacts.tar", config=config)
        lib.export_files(first.id, "/workspace", tmp_path / "workspace.tar", config=config)
        with tarfile.open(exported.destination) as archive:
            member = next(item for item in archive.getmembers() if item.name.endswith("/result.txt"))
            stream = archive.extractfile(member)
            assert stream is not None
            assert stream.read() == b"retained-result\n"
        profile.unlink()
        assert lib.destroy(first.id, config=config).complete
        assert lib.destroy(first.id, config=config).complete
        with pytest.raises(DigitalTwinUniverseError) as removed:
            lib.launch(profile, operation_id="first", config=config)
        assert removed.value.code == "operation-removed"
        assert lib.status(peer.id, config=config).state == "running"
        with pytest.raises(DigitalTwinUniverseError) as timed_out:
            lib.execute(peer.id, "sleep 30", timeout_seconds=1, config=config)
        assert timed_out.value.code == "timeout"
        assert lib.stop(peer.id, timeout_seconds=1, config=config).complete
        assert lib.destroy(peer.id, config=config).complete
        assert (source / "tracked.txt").read_text() == "dirty snapshot\n"
        assert (source / ".git").is_file()
    finally:
        for identity in ids:
            if identity in lib.recorded_ids(config=config):
                lib.destroy(identity, config=config)
