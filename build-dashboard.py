"""
Check and compile the dashboard into the package, when the tools to do so are present.

The compiled assets under `src/digital_twin_universe/capabilities/dashboard/static/` and the MCP App view at
`src/digital_twin_universe/adapters/static/mcp_app.html` are committed, so the tool installs and runs with no Node anywhere.
Node and pnpm are needed only to change the frontend; without them this script says so and exits 0, so
`prek run --all-files` passes for a Python-only contributor.
"""

from pathlib import Path
import shutil
import subprocess

DASHBOARD = Path(__file__).parent / "dashboard"


def main() -> None:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print(
            "pnpm not found; using the committed dashboard build. "
            "Install Node 22+ and pnpm only to change the frontend (see docs/DEVELOPMENT.md)."
        )
        return
    for script in ("install --frozen-lockfile", "check", "build", "build:view"):
        subprocess.run([pnpm, "--dir", str(DASHBOARD), *script.split()], check=True)


if __name__ == "__main__":
    main()
