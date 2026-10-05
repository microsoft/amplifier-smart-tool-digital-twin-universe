"""Command line entry point for Digital Twin Universe."""

from collections.abc import Callable
import contextlib
import json
from pathlib import Path
import threading
from typing import Annotated, Any

import typer

# Typer 0.27 vendors Click, so a command that overrides Click's own hooks has to speak the vendored types.
from typer._click import Context, Parameter
from typer.core import TyperCommand, TyperOption
from typer.models import CommandFunctionType

from digital_twin_universe import lib
from digital_twin_universe.adapters import mcp as mcp_adapter
from digital_twin_universe.core.skill import DISTRIBUTION
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    AgentProvider,
    DigitalTwinUniverseError,
    ReasoningEffort,
)

AGENT_PROVIDER_HELP = (
    "What the model-backed work runs through: copilot (GitHub Copilot, signed in as the GitHub CLI's user), "
    "amplifier-agent (Amplifier Agent, with the model provider's credentials), or codex (OpenAI Codex, with the "
    "Codex sign-in). The first installed, in that order, when omitted."
)
REASONING_EFFORT_NOTE = "Applies to the copilot and codex agent providers."


def _model_help(defaults: dict[AgentProvider, str]) -> str:
    named = "; ".join(f"{agent_provider}: {model}" for agent_provider, model in defaults.items())
    return (
        "A Copilot model id for copilot, <provider>/<model> for amplifier-agent, a Codex model id for codex. "
        f"Defaults to {named}."
    )


class CapabilityCommand(TyperCommand):
    """A capability that answers `-h` with the generated summary and `--help` with its skill from the library."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["add_help_option"] = False
        super().__init__(*args, **kwargs)

    def get_params(self, ctx: Context) -> list[Parameter]:
        def short(ctx: Context, param: Parameter, value: bool) -> None:
            if value and not ctx.resilient_parsing:
                typer.echo(ctx.get_help())
                ctx.exit()

        def capability_skill(ctx: Context, param: Parameter, value: bool) -> None:
            if value and not ctx.resilient_parsing:
                typer.echo(lib.skill(self.name))
                ctx.exit()

        return [
            *super().get_params(ctx),
            TyperOption(
                param_decls=["-h"],
                is_flag=True,
                is_eager=True,
                expose_value=False,
                callback=short,
                help="Terse summary of this capability.",
            ),
            TyperOption(
                param_decls=["--help"],
                is_flag=True,
                is_eager=True,
                expose_value=False,
                callback=capability_skill,
                help="This capability's skill, for an agent about to call it.",
            ),
        ]


class SmartToolTyper(typer.Typer):
    """A Typer whose commands are CapabilityCommand by default, so a capability added later inherits the help split."""

    def command(
        self, *args: Any, cls: type[TyperCommand] = CapabilityCommand, **kwargs: Any
    ) -> Callable[[CommandFunctionType], CommandFunctionType]:
        return super().command(*args, cls=cls, **kwargs)


app = SmartToolTyper(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    # Every command answers both flags. The root callback claims `--help` for the skill below, and Click drops a
    # help option name already taken by a parameter, which leaves the root's generated summary on `-h`. A
    # CapabilityCommand splits the two itself.
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_skill(value: bool) -> None:
    """Answer the root `--help` with the skill the library composes, leaving `-h` to Typer."""
    if value:
        typer.echo(lib.skill())
        raise typer.Exit()


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"{DISTRIBUTION} {lib.version()}")
        raise typer.Exit()


@app.callback()
def cli(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", is_eager=True, callback=_print_version, help="Print the version and exit."),
    ] = False,
    help: Annotated[
        bool,
        typer.Option(
            "--help", is_eager=True, callback=_print_skill, help="This tool's skill, for an agent driving it."
        ),
    ] = False,
) -> None:
    """Stands up an isolated, realistic environment from a profile on Docker Compose so software can be tested as though actually deployed. Use when passing tests on your machine is not enough evidence and code must run against real dependencies, published local repositories, and rewritten URLs without touching the host"""


@app.command()
def manifest() -> None:
    """Print the tool's manifest as JSON. Deterministic."""
    typer.echo(lib.load_manifest().model_dump_json(indent=2))


@app.command()
def check() -> None:
    """Report whether this host can run a universe: Docker CLI, daemon, and Compose plugin. Deterministic.

    Prints the report as JSON. Exits 0 when everything is present, 1 when something is missing.
    """
    report = lib.check()
    typer.echo(report.model_dump_json(indent=2))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def install(
    yes: Annotated[
        bool, typer.Option("--yes", help="Run the plan's unattended steps; otherwise only show and save it.")
    ] = False,
    accept_license: Annotated[
        bool,
        typer.Option("--accept-license", help="Explicitly accept Docker Desktop's Subscription Service Agreement."),
    ] = False,
    agent_provider: Annotated[
        AgentProvider | None,
        typer.Option("--agent-provider", help=AGENT_PROVIDER_HELP),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help=f"The intelligence model used to plan and repair. {_model_help(DEFAULT_INTELLIGENCE_MODELS)}",
        ),
    ] = None,
    reasoning_effort: Annotated[
        ReasoningEffort,
        typer.Option("--reasoning-effort", help=f"The model's reasoning effort. {REASONING_EFFORT_NOTE}"),
    ] = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: Annotated[
        int, typer.Option(min=1, help="Deadline for the whole run, including downloads and startup.")
    ] = 1200,
) -> None:
    """Get Docker working on this host. Model-backed: reads the official Docker docs for this platform and plans
    the install; runs the unattended steps with --yes. Deterministic when Docker is already present. Model-backed
    work runs through GitHub Copilot or Amplifier Agent, whichever --agent-provider names.

    Prints InstallReport JSON on stdout, step progress on stderr. Exits 0 on ready or installed, 1 on planned,
    action-required or failed. Raises docs-unreachable, plan-rejected, install-timeout, or an agent provider
    preflight error with the cause and remedy. Never prompts on stdin.
    """
    report = lib.install(yes, accept_license, agent_provider, model, reasoning_effort, timeout_seconds)
    typer.echo(report.model_dump_json(indent=2))
    if report.outcome not in ("ready", "installed"):
        raise typer.Exit(code=1)


@app.command("create-profile")
def create_profile(
    description: Annotated[str, typer.Option(help="What the universe is for, in your own words.")],
    project: Annotated[
        Path | None, typer.Option(help="The repository to profile. Omit when the description is everything.")
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="The profile name; derived from the description when omitted.")
    ] = None,
    no_verify: Annotated[
        bool, typer.Option("--no-verify", help="Stop at validate-profile; neither the agent nor the tool launches.")
    ] = False,
    keep: Annotated[bool, typer.Option(help="Leave the tool's verified universe running and report it.")] = False,
    overwrite: Annotated[bool, typer.Option(help="Replace an existing profile of the same name.")] = False,
    max_attempts: Annotated[int, typer.Option(min=1, help="Submissions the tool will consider.")] = 3,
    agent_provider: Annotated[
        AgentProvider | None,
        typer.Option("--agent-provider", help=AGENT_PROVIDER_HELP),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help=f"The intelligence model that writes the profile. {_model_help(DEFAULT_INTELLIGENCE_MODELS)}",
        ),
    ] = None,
    reasoning_effort: Annotated[
        ReasoningEffort,
        typer.Option("--reasoning-effort", help=f"The model's reasoning effort. {REASONING_EFFORT_NOTE}"),
    ] = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: Annotated[
        int, typer.Option(min=1, help="Deadline for the whole run; a launch with builds takes minutes.")
    ] = 1800,
) -> None:
    """Write a profile under .agents/digital-twin-universe/<name>/ and prove it. Model-backed: runs through
    GitHub Copilot or Amplifier Agent, whichever --agent-provider names. An agent reads the project and Docker's
    docs, writes the profile, launches it, runs checks in it, and destroys it; the tool then launches it again and
    reruns the checks, and only a profile that passes is kept.

    Prints CreatedProfile JSON on stdout, one progress line per phase on stderr. Exits 0 on created or validated,
    1 on failed (the draft stays at <name>.draft/), 2 on --keep with --no-verify. Raises profile-exists,
    project-not-found, name-invalid, profile-rejected, create-timeout, docker-unavailable, or an agent provider
    preflight error with the cause and remedy.
    """
    if keep and no_verify:
        raise typer.BadParameter("--keep leaves a verified universe running, and --no-verify launches none.")
    result = lib.create_profile(
        description,
        project,
        name,
        not no_verify,
        keep,
        overwrite,
        max_attempts,
        agent_provider,
        model,
        reasoning_effort,
        timeout_seconds,
    )
    typer.echo(result.model_dump_json(indent=2))
    if result.outcome == "failed":
        raise typer.Exit(code=1)


@app.command("validate-profile")
def validate_profile(
    profile: Annotated[
        str, typer.Option(help="A profile name, a Compose file, or a directory holding one. See `--help` for names.")
    ],
) -> None:
    """Check a profile without launching it: Compose, `x-dtu`, the universe invariants, and realism. Deterministic.

    Prints the report as JSON. Exits 0 when it has no errors, 1 when it has any; warnings never change the code.
    Exits 1 with the cause and remedy when the profile cannot be found or Docker is unusable.
    """
    report = lib.validate_profile(profile)
    typer.echo(report.model_dump_json(indent=2))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def launch(
    profile: Annotated[
        str, typer.Option(help="A profile name, a Compose file, or a directory holding one. See `--help` for names.")
    ],
    timeout_seconds: Annotated[int, typer.Option(help="How long to wait for every healthcheck.")] = 600,
) -> None:
    """Launch a universe from a profile and wait until it is ready. Deterministic.

    Prints the universe as JSON, with its id for `exec` and `destroy`. Compose's progress goes to stderr.
    Exits 1 with the cause and remedy when the profile is invalid, a variable is unset, a port is taken,
    a build fails, or a service never becomes healthy.
    """
    typer.echo(lib.launch(profile, timeout_seconds).model_dump_json(indent=2))


@app.command("list")
def list_() -> None:
    """List every universe launched from this machine, oldest first, measured now. Deterministic.

    Prints a JSON array of universes; an empty machine prints `[]`.
    """
    universes = lib.list_universes()
    typer.echo(json.dumps([universe.model_dump(mode="json") for universe in universes], indent=2))


@app.command()
def status(id: Annotated[str, typer.Option(help="The universe id `launch` printed.")]) -> None:
    """Measure one universe now: its state, services, and the URLs the host can open. Deterministic.

    Prints the universe as JSON, the same shape `launch` printed.
    """
    typer.echo(lib.status(id).model_dump_json(indent=2))


@app.command("exec")
def exec_(
    id: Annotated[str, typer.Option(help="The universe id `launch` printed.")],
    command: Annotated[
        str | None, typer.Option(help="Run this through a login shell in the twin. Omit for an interactive shell.")
    ] = None,
    user: Annotated[str | None, typer.Option(help="Run as this user instead of the twin's own.")] = None,
    workdir: Annotated[str | None, typer.Option(help="Run here instead of the twin's own working directory.")] = None,
    timeout_seconds: Annotated[int, typer.Option(help="How long a command may run; ignored for a shell.")] = 300,
) -> None:
    """Run a command in the twin, or open a shell in it. Deterministic.

    With --command, prints the result as JSON (exit_code, stdout, stderr) and exits with the command's own exit
    code. Without it, attaches an interactive login shell to this terminal and exits with the shell's exit code.
    """
    if command is None:
        raise typer.Exit(code=lib.shell(id, user, workdir))
    result = lib.execute(id, command, user, workdir, timeout_seconds)
    typer.echo(result.model_dump_json(indent=2))
    raise typer.Exit(code=result.exit_code)


@app.command("file-push")
def file_push(
    id: Annotated[str, typer.Option(help="The universe id `launch` printed.")],
    source: Annotated[Path, typer.Option(help="A file or directory on the host.")],
    destination: Annotated[
        str, typer.Option(help="A path in the twin; `docker cp` rules decide where the copy lands.")
    ],
) -> None:
    """Copy a file or directory from the host into the twin, owned by the twin's user. Deterministic.

    Prints the transfer as JSON: where the copy landed and how many files it holds.
    """
    typer.echo(lib.push_files(id, source, destination).model_dump_json(indent=2))


@app.command("file-pull")
def file_pull(
    id: Annotated[str, typer.Option(help="The universe id `launch` printed.")],
    source: Annotated[str, typer.Option(help="A file or directory in the twin.")],
    destination: Annotated[
        Path, typer.Option(help="A path on the host; `docker cp` rules decide where the copy lands.")
    ],
) -> None:
    """Copy a file or directory from the twin onto the host. Deterministic.

    Prints the transfer as JSON: where the copy landed and how many files it holds.
    """
    typer.echo(lib.pull_files(id, source, destination).model_dump_json(indent=2))


@app.command()
def destroy(id: Annotated[str, typer.Option(help="The universe id `launch` printed.")]) -> None:
    """Remove a universe: its containers, networks, volumes, and state. Built images stay. Deterministic.

    Prints what was removed as JSON.
    """
    typer.echo(lib.destroy(id).model_dump_json(indent=2))


@app.command()
def dashboard(
    port: Annotated[int | None, typer.Option(help="The port to bind; a free one is chosen when omitted.")] = None,
    host: Annotated[str, typer.Option(help="The interface to bind; 0.0.0.0 exposes it to the local network.")] = (
        "127.0.0.1"
    ),
) -> None:
    """Serve the dashboard, a web page showing every universe on this machine, and print its URL. Deterministic.

    The page renders the MCP App that `digital-twin-universe mcp` offers, from the same MCP server, which it also serves at
    `mcp_url` for other MCP clients. Prints where it is served as JSON, then keeps serving until Ctrl+C. Exits 1
    with the cause and remedy when the port is held or the dashboard is not compiled.
    """
    typer.echo(lib.serve_dashboard(port, host).model_dump_json(indent=2))
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()


@app.command()
def mcp() -> None:
    """Serve universe list, status, and destroy, plus the dashboard as an MCP App, over MCP on stdio. Deterministic.

    For MCP clients that launch the server themselves, such as VS Code. Reads and writes MCP messages on stdin
    and stdout until the client disconnects; prints nothing else to stdout. Exits 1 with the cause and remedy
    when the dashboard is not compiled.
    """
    mcp_adapter.run_stdio()


def main() -> int:
    try:
        app()
    except DigitalTwinUniverseError as error:
        typer.echo(error, err=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
