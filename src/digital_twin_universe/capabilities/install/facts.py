"""Deterministic host facts and the command boundary shared by probes and installation."""

import contextlib
import locale
import os
from pathlib import Path
import platform
import signal
import subprocess
import time
from typing import Protocol

from pydantic import BaseModel

from digital_twin_universe.schemas import ExecResult, HostReport, Platform

PROBE_TIMEOUT = 10
OS_RELEASE_KEYS = ("ID", "ID_LIKE", "VERSION_ID", "VERSION_CODENAME", "PRETTY_NAME", "UBUNTU_CODENAME")


class Runner(Protocol):
    """Run with stdin closed; a nonzero exit is data, a timeout is not completion."""

    def run(
        self, argv: list[str], timeout_seconds: float, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> ExecResult: ...


class SubprocessRunner:
    def run(
        self, argv: list[str], timeout_seconds: float, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> ExecResult:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                start_new_session=os.name != "nt",
            )
        except OSError as error:
            return ExecResult(exit_code=127, stdout="", stderr=str(error))
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            # Killing only the shell would leave its package manager modifying the host after we return.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=PROBE_TIMEOUT,
                    check=True,
                )
            else:
                # The process group can exit between the timeout and its cleanup.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        return ExecResult(exit_code=process.returncode, stdout=_decode(stdout), stderr=_decode(stderr))


def _decode(output: bytes) -> str:
    # wsl.exe emits UTF-16 even when redirected, unlike the other host commands.
    encoding = (
        "utf-16"
        if output.startswith((b"\xff\xfe", b"\xfe\xff"))
        else ("utf-16-le" if b"\x00" in output else locale.getencoding())
    )
    return output.decode(encoding, errors="replace")


class HostFacts(BaseModel):
    platform: Platform
    machine: str
    os_release: dict[str, str]
    wsl: bool
    systemd: bool
    sudo_noninteractive: bool
    euid_root: bool
    wsl_version: str | None
    elevated: bool
    windows_build: str | None
    docker_partial: HostReport
    accept_license: bool


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError
    return seconds


def host_facts(docker: HostReport, accept_license: bool, runner: Runner, deadline: float) -> HostFacts:
    def probe(argv: list[str]) -> ExecResult:
        return runner.run(argv, min(PROBE_TIMEOUT, remaining(deadline)))

    root = os.geteuid() == 0 if docker.platform != "windows" else False
    release = (
        platform.freedesktop_os_release() if docker.platform == "linux" and Path("/etc/os-release").is_file() else {}
    )
    wsl_version = None
    elevated = False
    sudo_ok = False
    if docker.platform == "windows":
        wsl = probe(["wsl", "--version"])
        wsl_version = wsl.stdout.strip() if wsl.exit_code == 0 else None
        elevation = probe(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
                    ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"
                ),
            ]
        )
        elevated = elevation.exit_code == 0 and elevation.stdout.strip().lower() == "true"
    elif not root:
        sudo_ok = probe(["sudo", "-n", "true"]).exit_code == 0
    return HostFacts(
        platform=docker.platform,
        machine=platform.machine(),
        os_release={key: release[key] for key in OS_RELEASE_KEYS if key in release},
        wsl=docker.platform == "linux" and "microsoft" in platform.release().lower(),
        systemd=docker.platform == "linux" and Path("/run/systemd/system").is_dir(),
        sudo_noninteractive=sudo_ok,
        euid_root=root,
        wsl_version=wsl_version,
        elevated=elevated,
        windows_build=platform.version() if docker.platform == "windows" else None,
        docker_partial=docker,
        accept_license=accept_license,
    )
