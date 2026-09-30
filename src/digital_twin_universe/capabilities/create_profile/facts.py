"""Deterministic facts about the project being profiled and the host it is profiled on."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from pydantic import BaseModel

from digital_twin_universe.capabilities.install.facts import PROBE_TIMEOUT, Runner, remaining
from digital_twin_universe.schemas import HostReport

MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "Makefile",
    "Dockerfile",
    "compose.yaml",
    "docker-compose.yaml",
    "docker-compose.yml",
    ".env.example",
)
README_PATTERN = re.compile(r"^readme(\.|$)", re.IGNORECASE)
# Names only, never values: what the profile may read with `${NAME:?...}` because the host has it set.
CREDENTIAL_PATTERN = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)
TOP_LEVEL_LIMIT = 60


class ProjectFacts(BaseModel):
    """What the agent is told before it reads anything itself."""

    root: Path
    project: Path | None
    top_level: list[str]
    manifests: list[str]
    readme: str | None
    credential_env_names: list[str]
    docker: HostReport


def project_facts(project: Path | None, docker: HostReport, runner: Runner, deadline: float) -> ProjectFacts:
    """Facts about the project, or about the working directory when there is no repository to read."""
    anchor = (project if project is not None else Path.cwd()).resolve()
    root = git_root(anchor, runner, deadline)
    if root is None or not anchor.is_relative_to(root):
        root = anchor
    listing = sorted(path.name for path in anchor.iterdir()) if project is not None else []
    return ProjectFacts(
        root=root,
        project=anchor.relative_to(root) if project is not None else None,
        top_level=listing[:TOP_LEVEL_LIMIT],
        manifests=[name for name in MANIFESTS if (anchor / name).is_file()] if project is not None else [],
        readme=next((name for name in listing if README_PATTERN.match(name)), None),
        credential_env_names=sorted(name for name in os.environ if CREDENTIAL_PATTERN.search(name)),
        docker=docker,
    )


def git_root(directory: Path, runner: Runner, deadline: float) -> Path | None:
    """The repository root above `directory`, or None when it is not in one or git is not installed."""
    try:
        result = runner.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"], min(PROBE_TIMEOUT, remaining(deadline))
        )
    except subprocess.TimeoutExpired:
        return None
    if result.exit_code != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def digital_twin_universe_command() -> str:
    """How the agent invokes this tool from `bash`: the installed executable, or this interpreter's module."""
    executable = shutil.which("digital-twin-universe")
    return executable if executable is not None else f"{sys.executable} -m digital_twin_universe.cli"
