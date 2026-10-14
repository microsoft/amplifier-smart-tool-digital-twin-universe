"""Execute and shell: run a command, or a person, in the twin."""

import subprocess
import sys
import threading
from typing import BinaryIO

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
    max_output_bytes: int = 1048576,
) -> ExecResult:
    """Run one command in the twin through a login shell and return what it produced."""
    if not 1 <= max_output_bytes <= 16777216 or timeout_seconds < 1:
        raise DigitalTwinUniverseError(
            "execution-limit-invalid",
            "Execution needs a positive timeout and 1 to 16777216 output bytes per stream.",
            "Choose bounded execution limits.",
        )
    argv = [*_exec_argv(id, user, workdir, tty=False), *LOGIN_SHELL, command]
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    except FileNotFoundError as error:
        raise docker_unavailable() from error
    assert process.stdout is not None
    assert process.stderr is not None
    stdout, stderr = _Output(max_output_bytes), _Output(max_output_bytes)
    readers = [
        threading.Thread(target=output.read, args=(stream,), daemon=True)
        for output, stream in ((stdout, process.stdout), (stderr, process.stderr))
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise DigitalTwinUniverseError(
            "timeout",
            f"The command in universe {id} did not finish within {timeout_seconds}s; its outcome is uncertain.",
            "Inspect or stop this environment. The in-container command may still be running; do not automatically replay it.",
            universe_id=id,
        ) from error
    finally:
        for reader in readers:
            reader.join(timeout=2)
    return ExecResult(
        exit_code=process.returncode,
        stdout=stdout.text(),
        stderr=stderr.text(),
        stdout_truncated=stdout.truncated,
        stderr_truncated=stderr.truncated,
    )


class _Output:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def read(self, stream: BinaryIO) -> None:
        with stream:
            while chunk := stream.read(65536):
                left = self.limit - len(self.data)
                self.data.extend(chunk[:left])
                self.truncated |= len(chunk) > left

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


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
