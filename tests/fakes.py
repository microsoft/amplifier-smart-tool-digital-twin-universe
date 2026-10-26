"""Plain helpers the tests import by name: git repositories, universe records, and the public MCP tool set."""

from collections.abc import Callable
from pathlib import Path
import subprocess

from digital_twin_universe.capabilities.universe import runtime, state
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.schemas import BackendConfig

# A committer the host's global git config cannot change, so a commit works on a machine with no identity set.
GIT = ("git", "-c", "user.email=test@dtu.invalid", "-c", "user.name=DTU Test", "-c", "commit.gpgsign=false")

MakeRepository = Callable[[str, dict[str, str]], Path]

MCP_TOOLS = {"open_dashboard", "list_universes", "universe_status", "destroy_universe"}

PROBE_FILES = {
    "MARKER.txt": "served-from-the-universe\n",
    "pyproject.toml": (
        "[project]\n"
        'name = "dtu-probe"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "[project.scripts]\n"
        'dtu-probe = "dtu_probe:main"\n'
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    ),
    "src/dtu_probe/__init__.py": 'def main() -> None:\n    print("dtu-probe-installed")\n',
}


def create_repository(root: Path, name: str, files: dict[str, str]) -> Path:
    """A git repository on the host with one commit of `files`, under any directory."""
    repository = root / name
    repository.mkdir(parents=True)
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=repository, check=True)
    subprocess.run([*GIT, "add", "."], cwd=repository, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "initial"], cwd=repository, check=True)
    return repository


def universe_record(
    id: str = "dtu-demo-0000",
    name: str = "demo",
    profile_path: Path = Path("/profiles/demo/compose.yaml"),
    twin_machine: str = "app",
    config: BackendConfig | None = None,
) -> UniverseRecord:
    """A record of a universe that never touched Docker.

    Given a config, the record is pinned to its Docker endpoint and written under its state root, the way launch
    leaves one behind.
    """
    record = UniverseRecord(
        id=id,
        name=name,
        description=None,
        profile_path=profile_path,
        twin_machine=twin_machine,
        created_at=state.now(),
        docker_host=config.docker_host if config is not None else None,
    )
    if config is not None:
        with runtime.configured(config):
            state.write(record)
    return record
