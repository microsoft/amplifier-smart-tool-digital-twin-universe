"""Execute and shell: run a command, or a person, in the twin."""

import subprocess
import sys

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import compose_client, docker_unavailable, running_twin
from digital_twin_universe.schemas import DigitalTwinUniverseError, ExecResult

# A login shell, so PATH changes an installer made in ~/.profile apply as they would in a person's terminal.
LOGIN_SHELL = ["sh", "-lc"]
INTERACTIVE_SHELL = "command -v bash >/dev/null 2>&1 && exec bash -l || exec sh -l"


def execute(
    id: str,
    command: str,
    user: str | None = None,
    workdir: str | None = None,
    timeout_seconds: int = 300,
) -> ExecResult:
    """Run one command in the twin through a login shell and return what it produced."""
    argv = [*_exec_argv(id, user, workdir, tty=False), *LOGIN_SHELL, command]
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout_seconds
        )
    except FileNotFoundError as error:
        raise docker_unavailable() from error
    except subprocess.TimeoutExpired as error:
        raise DigitalTwinUniverseError(
            "timeout",
            f"The command in universe {id} was still running after {timeout_seconds}s: {command!r}",
            "Raise `timeout_seconds`, or run the command in the background and poll it.",
        ) from error
    return ExecResult(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def shell(id: str, user: str | None = None, workdir: str | None = None) -> int:
    """Attach an interactive login shell in the twin to this terminal and return its exit code."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise DigitalTwinUniverseError(
            "no-tty",
            f"An interactive shell in universe {id} needs a terminal, and this process has none.",
            "Run from a terminal, or pass a command to `execute` instead.",
        )
    argv = [*_exec_argv(id, user, workdir, tty=True), *LOGIN_SHELL, INTERACTIVE_SHELL]
    try:
        return subprocess.run(argv).returncode
    except FileNotFoundError as error:
        raise docker_unavailable() from error


def _exec_argv(id: str, user: str | None, workdir: str | None, tty: bool) -> list[str]:
    """`docker compose -p <id> exec ... <twin>`, after confirming the twin is running."""
    record = state.read(id)
    running_twin(record)
    argv = [str(part) for part in compose_client(id).compose.docker_compose_cmd] + ["exec"]
    if not tty:
        argv.append("--no-TTY")
    if user is not None:
        argv += ["--user", user]
    if workdir is not None:
        argv += ["--workdir", workdir]
    return [*argv, record.twin_machine]
