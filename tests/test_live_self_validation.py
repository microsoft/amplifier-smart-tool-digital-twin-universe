"""Digital Twin Universe inside Digital Twin Universe: the tool installed and run in a universe the way a user would, from this checkout.

The slowest test in the suite, and the one the vision holds the tool to: a universe is launched from the profile
at `.agents/digital-twin-universe/self-validation/`, the `digital-twin-universe` inside it launches a universe of its own on a
nested daemon, and the host reaches what that inner universe serves.
"""

from pathlib import Path
import urllib.request

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe.profile import PROFILE_DIRECTORY

pytestmark = [pytest.mark.needs_docker, pytest.mark.live]

REPOSITORY = Path(__file__).parents[1]
PROFILE = REPOSITORY / PROFILE_DIRECTORY / "self-validation"
LAUNCH_TIMEOUT = 900
INNER_TIMEOUT = 600


def test_the_installed_tool_launches_a_universe_inside_the_universe(
    state_root: Path, free_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DTU_SELF_VALIDATION_PORT", str(free_port))
    monkeypatch.chdir(REPOSITORY)

    universe = lib.launch("self-validation", timeout_seconds=LAUNCH_TIMEOUT)
    try:
        assert universe.profile_path == (PROFILE / "compose.yaml").resolve()
        assert [url.port for url in universe.urls] == [free_port]

        installed = lib.execute(universe.id, "digital-twin-universe check")
        assert installed.exit_code == 0, installed.stderr

        launched = lib.execute(
            universe.id, "digital-twin-universe launch --profile sample", timeout_seconds=INNER_TIMEOUT
        )
        assert launched.exit_code == 0, launched.stderr
        assert '"state": "running"' in launched.stdout

        inner = lib.execute(universe.id, "digital-twin-universe list")
        assert inner.stdout.count('"id": "dtu-sample-') == 1

        with urllib.request.urlopen(f"http://localhost:{free_port}/", timeout=10) as response:
            assert response.read() == b"hello-from-the-inner-universe\n"

        through_inner_exec = lib.execute(
            universe.id,
            "digital-twin-universe exec --id $(digital-twin-universe list | grep '\"id\"' | cut -d'\"' -f4) --command 'id -u'",
        )
        assert '"stdout": "1000\\n"' in through_inner_exec.stdout

        destroyed = lib.execute(
            universe.id,
            "digital-twin-universe destroy --id $(digital-twin-universe list | grep '\"id\"' | cut -d'\"' -f4)",
        )
        assert destroyed.exit_code == 0, destroyed.stderr
        assert lib.execute(universe.id, "digital-twin-universe list").stdout.strip() == "[]"
    finally:
        lib.destroy(universe.id)
