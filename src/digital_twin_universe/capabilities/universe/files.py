"""Push and pull: copy between the host and the twin with `docker cp` semantics."""

from pathlib import Path, PurePosixPath

from python_on_whales import ClientNotFoundError
from python_on_whales.components.container.cli_wrapper import Container
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import compose_client, running_twin, translate_docker_error
from digital_twin_universe.schemas import DigitalTwinUniverseError, Transfer

# `docker cp` resolves container paths against `/`, so every probe and chown in the twin does the same.
TWIN_ROOT = "/"


def push_files(id: str, source: Path, destination: str) -> Transfer:
    """Copy a host file or directory into the twin, owned by the twin's user."""
    twin = running_twin(state.read(id))
    source = Path(source)
    if not source.exists():
        raise DigitalTwinUniverseError(
            "source-not-found",
            f"{source} does not exist on the host.",
            "Pass a file or directory that exists.",
        )
    landing = destination
    if _in_twin(twin, f"test -d {_quote(destination)} && echo yes") == "yes":
        landing = str(PurePosixPath(destination) / source.name)
    _copy(id, source, (twin.name, destination))
    owner = _in_twin(twin, "echo $(id -u):$(id -g)")
    if not owner.startswith("0:"):
        twin.execute(["chown", "-R", owner, landing], user="0", workdir=TWIN_ROOT)
    return Transfer(source=str(source), destination=landing, files=_count_files(source))


def pull_files(id: str, source: str, destination: Path) -> Transfer:
    """Copy a file or directory out of the twin onto the host."""
    twin = running_twin(state.read(id))
    destination = Path(destination)
    if _in_twin(twin, f"test -e {_quote(source)} && echo yes") != "yes":
        raise DigitalTwinUniverseError(
            "source-not-found",
            f"{source} does not exist in the twin of universe {id}.",
            f"Pass a path that exists in the twin; `digital-twin-universe exec --id {id} --command 'ls -la {source}'` shows it.",
        )
    landing = destination / PurePosixPath(source).name if destination.is_dir() else destination
    _copy(id, (twin.name, source), destination)
    return Transfer(source=source, destination=str(landing), files=_count_files(landing))


def _copy(id: str, source: Path | tuple[str, str], destination: Path | tuple[str, str]) -> None:
    try:
        compose_client().container.copy(source, destination)
    except (ClientNotFoundError, DockerException) as error:
        translated = translate_docker_error(error)
        if translated is not None:
            raise translated from error
        raise DigitalTwinUniverseError(
            "transfer-failed",
            f"`docker cp` refused the transfer for universe {id}: {(getattr(error, 'stderr', None) or '').strip()}",
            "Check both paths; a destination's parent directory must exist, and a file cannot land on a directory.",
        ) from error


def _in_twin(twin: Container, script: str) -> str:
    """Run a shell snippet in the twin as its own user and return its stdout; a non-zero exit is an empty string."""
    output = twin.execute(["sh", "-c", f"{script} 2>/dev/null || true"], workdir=TWIN_ROOT)
    return str(output).strip() if output is not None else ""


def _quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


def _count_files(path: Path) -> int:
    if path.is_dir():
        return sum(1 for entry in path.rglob("*") if entry.is_file())
    return 1 if path.exists() else 0
