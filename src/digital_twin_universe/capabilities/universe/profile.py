"""Profile resolution: from a name or path to a Compose file, read through `docker compose config`."""

from pathlib import Path
import re
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe.compose import compose_client, translate_docker_error
from digital_twin_universe.schemas import DigitalTwinUniverseError, UrlSpec

PROFILE_DIRECTORY = Path(".agents") / "digital-twin-universe"
EXAMPLES_DIRECTORY = Path(__file__).parents[2] / "examples"
COMPOSE_FILE_NAMES = ("compose.yaml", "docker-compose.yaml")
X_DTU = "x-dtu"
IMPLICIT_TWIN = "twin"
ALL_PROFILES = ["*"]

ENV_MISSING_PATTERN = re.compile(r"required variable (?P<name>\w+) is missing a value: ?(?P<message>.*)")


class Repository(BaseModel):
    """A local git repository served inside the universe at the URL it stands in for."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    url: str | None = None


class Rewrite(BaseModel):
    """A `host/path` prefix routed to another URL, usually a service in the same file."""

    model_config = ConfigDict(extra="forbid")

    match: str
    target: str


class XDtu(BaseModel):
    """The `x-dtu` block: everything about a universe that Compose itself cannot say."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    twin_machine: str | None = None
    urls: list[UrlSpec] = Field(default_factory=list)
    repositories: list[Repository] = Field(default_factory=list)
    rewrites: list[Rewrite] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)

    @property
    def serves_repositories(self) -> bool:
        return bool(self.repositories)

    @property
    def needs_gateway(self) -> bool:
        """A gateway exists only to rewrite or to refuse; a profile that asks for neither gets none."""
        return bool(self.rewrites or self.allow or any(repository.url for repository in self.repositories))


class Profile(NamedTuple):
    """A profile after Compose has validated and interpolated it."""

    path: Path
    name: str
    description: str | None
    twin_machine: str
    services: list[str]
    x_dtu: XDtu
    config: dict[str, Any]


def resolve_path(profile: str | Path) -> Path:
    """The Compose file a profile argument names: a path to a file or directory, or a profile name."""
    candidate = Path(profile)
    if candidate.is_file():
        return candidate.resolve()
    if candidate.is_dir():
        return _compose_file_in(candidate)
    name = str(profile)
    if Path(name).name != name:
        raise DigitalTwinUniverseError(
            "profile-not-found",
            f"{name} does not exist.",
            "Pass a path to a Compose file, a directory holding one, or a profile name.",
        )
    searched = _profile_directories(name)
    for directory in searched:
        if directory.is_dir():
            return _compose_file_in(directory)
    raise DigitalTwinUniverseError(
        "profile-not-found",
        f"No profile named {name!r}. Looked for: {', '.join(str(directory) for directory in searched)}.",
        f"Create `{PROFILE_DIRECTORY / name}/compose.yaml` in this project, or pass a path to a Compose file.",
    )


def read_config(path: Path, profiles: list[str] | None = None) -> dict[str, Any]:
    """A profile as `docker compose config` reports it: validated, interpolated, and normalized.

    `profiles` activates Compose profiles, which is the only way to see services a `profiles:` entry would leave out.
    """
    try:
        return compose_client(compose_files=[path], profiles=profiles).compose.config(return_json=True)
    except DockerException as error:
        raise _profile_error(path, error) from error


def load(profile: str | Path) -> Profile:
    """Resolve a profile and read it back through `docker compose config`, which validates and interpolates it."""
    path = resolve_path(profile)
    return _profile_from_config(path, read_config(path))


def twin_machine(services: list[str], x_dtu: XDtu) -> str:
    """The service the software under test runs in: named, or the only one, or the one called `twin`."""
    if x_dtu.twin_machine is not None:
        return x_dtu.twin_machine
    return services[0] if len(services) == 1 else IMPLICIT_TWIN


def _profile_from_config(path: Path, config: dict[str, Any]) -> Profile:
    services = list(config.get("services") or {})
    x_dtu = XDtu.model_validate(config.get(X_DTU) or {})
    twin = twin_machine(services, x_dtu)
    if twin not in services:
        raise DigitalTwinUniverseError(
            "profile-invalid",
            f"{path} names no twin: `{X_DTU}.twin_machine` is {twin!r} and the services are {services}.",
            f"Set `{X_DTU}.twin_machine` to the service the software under test runs in.",
        )
    return Profile(
        path=path,
        name=config["name"],
        description=x_dtu.description,
        twin_machine=twin,
        services=services,
        x_dtu=x_dtu,
        config=config,
    )


def _profile_error(path: Path, error: DockerException) -> DigitalTwinUniverseError:
    translated = translate_docker_error(error)
    if translated is not None:
        return translated
    stderr = (error.stderr or "").strip()
    missing = ENV_MISSING_PATTERN.search(stderr)
    if missing is not None:
        name = missing.group("name")
        message = missing.group("message").strip() or f"the profile requires {name}"
        return DigitalTwinUniverseError(
            "env-missing",
            f"{path} needs the environment variable {name}, which is not set: {message}",
            f"Export {name} on the host before launching; it is read at launch and never written to a file.",
        )
    return DigitalTwinUniverseError(
        "profile-invalid",
        f"`docker compose config` rejected {path}: {stderr}",
        "Fix the Compose file; the message above is Compose's own.",
    )


def _compose_file_in(directory: Path) -> Path:
    for name in COMPOSE_FILE_NAMES:
        if (directory / name).is_file():
            return (directory / name).resolve()
    raise DigitalTwinUniverseError(
        "profile-not-found",
        f"{directory} holds none of {', '.join(COMPOSE_FILE_NAMES)}.",
        "Add a Compose file to the directory, or pass the path to one directly.",
    )


def _profile_directories(name: str) -> list[Path]:
    """Where a profile name is looked for: `.agents/digital-twin-universe/<name>` from here up to the git root, then the shipped examples."""
    directories: list[Path] = []
    current = Path.cwd().resolve()
    for directory in (current, *current.parents):
        directories.append(directory / PROFILE_DIRECTORY / name)
        if (directory / ".git").exists():
            break
    directories.append(EXAMPLES_DIRECTORY / name)
    return directories
