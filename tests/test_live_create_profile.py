"""create-profile with the real intelligence: an agent profiles a small CLI repository, launches it, and proves it.

The agent drives `digital-twin-universe` through its own shell against the real state directory, which is the only place the
tool can see its universes appear, so this test does not isolate `~/.digital-twin-universe/universes`. It asserts that nothing
launched from its own repository is left afterwards, and touches nothing else: other universes on the machine belong
to whoever launched them. Slow and paid: it runs once per agent provider that is installed and configured, a Copilot
sign-in for `copilot`, OPENAI_API_KEY for the default `amplifier-agent` model, and a Codex sign-in for `codex`, and it
is marked `model` so `-m "not model"` deselects it.
"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from digital_twin_universe import lib
from digital_twin_universe.intelligence.interface import installed, resolve_intelligence
from digital_twin_universe.schemas import AgentProvider, DigitalTwinUniverseError

pytestmark = [pytest.mark.needs_docker, pytest.mark.live, pytest.mark.model]

NAME = "dtu-probe"
URL = "https://github.com/fake-org/probe"
TIMEOUT = 1500


def _signed_in() -> bool:
    return (
        shutil.which("gh") is not None and subprocess.run(["gh", "auth", "token"], capture_output=True).returncode == 0
    )


def _codex_signed_in() -> bool:
    try:
        resolve_intelligence("codex").preflight()
    except DigitalTwinUniverseError:
        return False
    return True


def _from(repository: Path) -> list[str]:
    return [u.id for u in lib.list_universes() if Path(u.profile_path).resolve().is_relative_to(repository.resolve())]


@pytest.mark.parametrize(
    "agent_provider",
    [
        pytest.param(
            "copilot",
            marks=pytest.mark.skipif(
                not installed("copilot") or not _signed_in(), reason="needs the copilot extra and `gh auth token`"
            ),
        ),
        pytest.param(
            "amplifier-agent",
            marks=pytest.mark.skipif(
                not installed("amplifier-agent") or not os.environ.get("OPENAI_API_KEY"),
                reason="needs the amplifier-agent extra and OPENAI_API_KEY",
            ),
        ),
        pytest.param(
            "codex",
            marks=pytest.mark.skipif(not _codex_signed_in(), reason="needs the codex extra and a Codex sign-in"),
        ),
    ],
)
def test_the_agent_profiles_a_cli_repository_and_the_tool_proves_it(
    served_repository: Path, agent_provider: AgentProvider
) -> None:
    # One profile name per agent provider, so parallel runs never share a universe id.
    name = f"{NAME}-{agent_provider}"
    result = lib.create_profile(
        f"The `dtu-probe` command line tool in this repository, installed the way a user would once it is published at "
        f"{URL}: `uv tool install git+{URL}`. Running `dtu-probe` prints `dtu-probe-installed`.",
        project=served_repository,
        name=name,
        agent_provider=agent_provider,
        timeout_seconds=TIMEOUT,
    )
    try:
        assert result.outcome == "created", result.model_dump_json(indent=2)
        profile = served_repository / ".agents" / "digital-twin-universe" / name
        assert result.path == profile
        assert (profile / "compose.yaml").is_file()
        assert not profile.with_name(f"{name}.draft").exists()
        assert result.checks
        assert all(check.status == "passed" for check in result.checks), result.checks
        assert result.validation.ok
        assert result.universe_id is None
        assert result.cleanup is not None
        assert "dtu-probe-installed" in " ".join(check.expect_stdout or "" for check in result.checks)
        assert f"launch --profile {name}" in result.next
    finally:
        left = _from(served_repository)
        for id in left:
            lib.destroy(id)
    assert left == [], f"universes left behind: {left}"
