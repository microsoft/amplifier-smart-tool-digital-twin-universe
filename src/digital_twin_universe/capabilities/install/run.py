"""Run documented steps, including daemon startup, then wait for Docker to become usable."""

from collections.abc import Callable
import os
from pathlib import Path
import sys
import time

from digital_twin_universe.capabilities.install.facts import Runner, remaining
from digital_twin_universe.core.skill import repository_url
from digital_twin_universe.schemas import DigitalTwinUniverseError, HostReport, InstallReport, InstallStep, Platform

LOG_TAIL = 20
POLL_SECONDS = 2
VERIFY_PROFILE = "hello"


def run_step(
    step: InstallStep, index: int, total: int, platform: Platform, runner: Runner, deadline: float, cwd: Path
) -> None:
    print(f"[{index}/{total}] {step.title} ...", file=sys.stderr, flush=True)
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none"}
    if platform == "windows":
        commands = "\n".join(
            command + "\nif (-not $?) { exit 1 }; if ($LASTEXITCODE) { exit $LASTEXITCODE }"
            for command in step.commands
        )
        argv = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference = 'Stop'\n" + commands,
        ]
    else:
        argv = ["sh", "-ec", "\n".join(step.commands)]
    result = runner.run(argv, remaining(deadline), env=env, cwd=cwd)
    if result.exit_code == 0:
        step.status = "done"
        step.reason = None
    else:
        step.status = "failed"
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-LOG_TAIL:])
        step.reason = f"Exit {result.exit_code}: {tail or 'no output'}"


def skip_remaining(steps: list[InstallStep], reason: str) -> None:
    for step in steps:
        if step.unattended and step.status == "pending":
            step.status = "skipped"
            step.reason = reason


def verify(verify_universe: Callable[[], None], report: InstallReport) -> None:
    """`check` passing says the daemon answers; only a universe that launches and runs a command says it works."""
    step = InstallStep(
        title="Launch the smallest universe and run a command in it",
        commands=[
            f"digital-twin-universe launch --profile {VERIFY_PROFILE}",
            "digital-twin-universe exec --id <id> --command 'echo dtu-ok'",
            "digital-twin-universe destroy --id <id>",
        ],
        source=repository_url() or "digital-twin-universe",
        unattended=True,
        status="pending",
        reason=None,
    )
    report.steps.append(step)
    print(f"[{len(report.steps)}/{len(report.steps)}] {step.title} ...", file=sys.stderr, flush=True)
    try:
        verify_universe()
    except DigitalTwinUniverseError as error:
        step.status = "failed"
        step.reason = str(error)
        report.outcome = "failed"
        report.summary = "Docker is installed, but a universe did not run on it."
        report.next = "read the failed step's reason; `digital-twin-universe check` passes, so the daemon answers but cannot run this"
    else:
        step.status = "done"
        report.summary = "Docker CLI, daemon, and Compose are ready, and a universe launched and ran on them."


def poll_check(check_host: Callable[[], HostReport], report: InstallReport, deadline: float) -> None:
    while True:
        remaining(deadline)
        report.docker = check_host()
        if report.docker.ok:
            report.outcome = "installed"
            report.summary = "Docker CLI, daemon, and Compose are now ready on this host."
            report.next = "nothing"
            return
        if any(step.status == "manual" for step in report.steps):
            report.outcome = "action-required"
            report.summary = "The unattended steps finished; Docker still needs the manual action below."
            return
        time.sleep(min(POLL_SECONDS, remaining(deadline)))
