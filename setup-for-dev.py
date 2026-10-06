"""
Cross-platform script to set up a development environment for the project.
It assumes that you have installed all the prerequisites listed in docs/DEVELOPMENT.md.
"""

from pathlib import Path
import shlex
import shutil
import subprocess

REFERENCE_ROOT = Path(__file__).parent / "reference"
REFERENCES = {
    "amplifier-smart-tools": "https://github.com/microsoft/amplifier-smart-tools",
    "copilot-sdk": "https://github.com/github/copilot-sdk",
    "amplifier-agent": "https://github.com/microsoft/amplifier-agent",
    "codex": "https://github.com/openai/codex",
    "claude-agent-sdk-python": "https://github.com/anthropics/claude-agent-sdk-python",
    "agentskills": "https://github.com/agentskills/agentskills",
    "amplifier-bundle-digital-twin-universe": "https://github.com/microsoft/amplifier-bundle-digital-twin-universe",
    "amplifier-bundle-gitea": "https://github.com/microsoft/amplifier-bundle-gitea",
    "amplifier-smart-tool-digital-twin-universe": "https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe",
    "docker-docs": "https://github.com/docker/docs",
    "mybench-smart-tool": "https://github.com/DavidKoleczek/mybench-smart-tool",
    "amplifier-smart-tool-creator": "https://github.com/DavidKoleczek/amplifier-smart-tool-creator",
    "python-on-whales": "https://github.com/gabrieldemarmiesse/python-on-whales",
}


def run(command: str) -> None:
    """Run a command, forwarding its output to this process's stdout and stderr."""
    subprocess.run(shlex.split(command), check=True)


def clone_missing_references() -> None:
    """Clone every reference repository that is absent; they are gitignored, so a fresh clone has none."""
    for directory_name, repository in REFERENCES.items():
        destination = REFERENCE_ROOT / directory_name
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", repository, str(destination)], check=True)


def install_dashboard_dependencies() -> None:
    """Only when pnpm is present; the compiled dashboard is committed, so Node is needed only to change it."""
    if shutil.which("pnpm") is None:
        print("pnpm not found; skipping dashboard dependencies. Install Node 22+ and pnpm to change the frontend.")
        return
    run("pnpm --dir dashboard install --frozen-lockfile")


def main() -> None:
    run("uv --version")
    run("prek --version")
    run("uv sync --frozen --all-extras --all-groups")
    run("prek install")
    clone_missing_references()
    install_dashboard_dependencies()


if __name__ == "__main__":
    main()
