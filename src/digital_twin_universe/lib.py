"""Top level entry point for the Digital Twin Universe library."""

from pathlib import Path

from digital_twin_universe.capabilities.check import check as check_module
from digital_twin_universe.capabilities.create_profile import create as create_module
from digital_twin_universe.capabilities.dashboard import server as dashboard_module
from digital_twin_universe.capabilities.install import install as install_module
from digital_twin_universe.capabilities.universe import destroy as destroy_module
from digital_twin_universe.capabilities.universe import execute as execute_module
from digital_twin_universe.capabilities.universe import files as files_module
from digital_twin_universe.capabilities.universe import launch as launch_module
from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe import status as status_module
from digital_twin_universe.capabilities.universe import validate as validate_module
from digital_twin_universe.core import manifest
from digital_twin_universe.core import skill as skill_module
from digital_twin_universe.intelligence.interface import Intelligence
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    AgentProvider,
    CreatedProfile,
    Dashboard,
    Destroyed,
    DigitalTwinUniverseError,
    ExecResult,
    HostReport,
    InstallReport,
    Manifest,
    ProfileReport,
    ReasoningEffort,
    Transfer,
    Universe,
)


def load_manifest() -> Manifest:
    """The tool's manifest as structured data, read from the SMART_TOOL.md shipped inside the package."""
    return manifest.load_manifest()


def skill(capability: str | None = None) -> str:
    """The tool's skill, or one capability's skill when named, wrapped so a reader knows where its files are."""
    return skill_module.skill(capability)


def skill_directory() -> Path:
    """The installed package root, where the files the skill names can be read."""
    return skill_module.skill_directory()


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory. Every one ships inside the package."""
    return skill_module.skill_resources()


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    return skill_module.repository_url()


def check() -> HostReport:
    """Whether this host can run a universe: the Docker CLI, a reachable daemon, and the Compose plugin."""
    return check_module.check()


def install(
    apply: bool = False,
    accept_license: bool = False,
    agent_provider: AgentProvider | None = None,
    model: str | None = None,
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1200,
    intelligence: Intelligence | None = None,
) -> InstallReport:
    """Get Docker working from official docs. Model-backed unless check passes; only apply=True changes the host.

    `agent_provider` is the first installed one when omitted, and `model` that agent provider's default.
    """
    return install_module.install(
        check,
        _verify_universe,
        apply,
        accept_license,
        agent_provider,
        model,
        reasoning_effort,
        timeout_seconds,
        intelligence,
    )


def _verify_universe() -> None:
    """After an install changed the host: launch the smallest shipped universe, run a command in it, destroy it."""
    universe = launch_module.launch(install_module.run.VERIFY_PROFILE, timeout_seconds=300)
    try:
        result = execute_module.execute(universe.id, "echo dtu-ok")
    finally:
        destroy_module.destroy(universe.id)
    if "dtu-ok" not in result.stdout:
        raise DigitalTwinUniverseError(
            "verify-failed",
            f"`echo dtu-ok` in the twin exited {result.exit_code}: {result.stderr.strip() or 'no output'}",
            f"Run `digital-twin-universe launch --profile {install_module.run.VERIFY_PROFILE}` and `digital-twin-universe exec` by hand.",
        )


def create_profile(
    description: str,
    project: Path | None = None,
    name: str | None = None,
    verify: bool = True,
    keep: bool = False,
    overwrite: bool = False,
    max_attempts: int = 3,
    agent_provider: AgentProvider | None = None,
    model: str | None = None,
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1800,
    intelligence: Intelligence | None = None,
) -> CreatedProfile:
    """Write a profile from a description and prove it by launching it. Model-backed; needs Docker.

    `agent_provider` is the first installed one when omitted, and `model` that agent provider's default.
    """
    return create_module.create_profile(
        _universes(),
        check,
        description,
        project,
        name,
        verify,
        keep,
        overwrite,
        max_attempts,
        agent_provider,
        model,
        reasoning_effort,
        timeout_seconds,
        intelligence,
    )


def _universes() -> create_module.Universes:
    """The universe capabilities `create_profile` drives, bound late so the module functions here stay the source."""
    return create_module.Universes(
        validate=validate_profile,
        launch=launch,
        execute=lambda id, command, timeout_seconds: execute(id, command, timeout_seconds=timeout_seconds),
        destroy=destroy,
        list_universes=list_universes,
        recorded=_recorded_ids,
        relocate=_relocate,
    )


def _recorded_ids() -> list[str]:
    """Ids of every universe with a record on this machine, read without Docker and cheap enough to poll."""
    return [path.name for path in state.STATE_ROOT.iterdir()] if state.STATE_ROOT.is_dir() else []


def _relocate(id: str, profile_path: Path) -> None:
    """Point a universe's record at the profile's new location after a rename, so `list` tells the truth."""
    record = state.read(id)
    record.profile_path = profile_path
    state.write(record)


def validate_profile(profile: str | Path) -> ProfileReport:
    """Whether a profile can be launched, and what would be unrealistic about it if it were."""
    return validate_module.validate_profile(profile)


def launch(profile: str | Path, timeout_seconds: int = 600) -> Universe:
    """From a profile to a running, ready universe: validated, recorded, brought up, and waited on."""
    return launch_module.launch(profile, timeout_seconds)


def list_universes() -> list[Universe]:
    """Every universe launched from this machine, oldest first, each measured against Docker now."""
    return status_module.list_universes()


def status(id: str) -> Universe:
    """One universe, measured against Docker now."""
    return status_module.status(id)


def execute(
    id: str,
    command: str,
    user: str | None = None,
    workdir: str | None = None,
    timeout_seconds: int = 300,
) -> ExecResult:
    """Run one command in the twin through a login shell. A non-zero exit is a result, not a failure."""
    return execute_module.execute(id, command, user, workdir, timeout_seconds)


def shell(id: str, user: str | None = None, workdir: str | None = None) -> int:
    """An interactive shell in the twin, attached to this terminal; returns its exit code."""
    return execute_module.shell(id, user, workdir)


def push_files(id: str, source: Path, destination: str) -> Transfer:
    """Copy a host file or directory into the twin with `docker cp` semantics, owned by the twin's user."""
    return files_module.push_files(id, source, destination)


def pull_files(id: str, source: str, destination: Path) -> Transfer:
    """Copy a file or directory out of the twin onto the host with `docker cp` semantics."""
    return files_module.pull_files(id, source, destination)


def destroy(id: str) -> Destroyed:
    """Remove a universe: every container, network, and volume, and its state directory. Images stay."""
    return destroy_module.destroy(id)


def serve_dashboard(port: int | None = None, host: str = "127.0.0.1") -> Dashboard:
    """Serve the dashboard from background threads and return where it is; it lives until the process exits.

    The page is a minimal MCP Apps host, compiled and shipped with the package, that renders the MCP App view of
    `digital_twin_universe.adapters.mcp`, whose tools wrap `list_universes`, `status`, and `destroy`. It serves that MCP server at
    `/mcp` too, so the dashboard adds no capability of its own.

    Args:
        port: The port to bind; a free one is chosen when omitted.
        host: The interface to bind. The default keeps the server off the local network.
    """
    return dashboard_module.serve_dashboard(port, host)
