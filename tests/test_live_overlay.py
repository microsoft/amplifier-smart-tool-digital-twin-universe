"""The overlay, end to end: served repositories, rewritten hosts, the universe CA, and the allowlist.

Every assertion is what a client inside the twin sees, since that is the only evidence the overlay works.
No test passes `-k`, `GIT_SSL_NO_VERIFY`, or any other way around verification: disabling it would stop testing
the CA, which is the part worth testing.
"""

from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
import socket
import subprocess

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe.compose import compose_client
from digital_twin_universe.schemas import DigitalTwinUniverseError, Universe

pytestmark = [pytest.mark.needs_docker, pytest.mark.live]

SERVED_URL = "https://github.com/fake-org/probe"
MOCKED_HOST = "api.example.com"
MARKER = "served-from-the-universe\n"
LAUNCH_TIMEOUT = 300
INSTALL_TIMEOUT = 300

# Every client that reads a different variable for its certificate authority, in one image.
TWIN_DOCKERFILE = """\
FROM node:24-alpine
RUN apk add --no-cache git curl ca-certificates python3 py3-requests
COPY --from=ghcr.io/astral-sh/uv:alpine /usr/local/bin/uv /usr/local/bin/uv
RUN adduser -D user
USER user
WORKDIR /home/user
"""

TWIN = """\
services:
  box:
    build: .
    command: sleep infinity
    healthcheck:
      test: [CMD, "true"]
      interval: 2s
"""

MOCK = """\
  mock:
    image: python:3.12-alpine
    command: sh -c "mkdir -p /srv && echo mock-answered > /srv/hello && cd /srv && python3 -m http.server 8080"
    healthcheck:
      test: [CMD, python3, -c, "import urllib.request; urllib.request.urlopen('http://localhost:8080/hello')"]
      interval: 2s
"""

SERVED = (
    """\
name: served
x-dtu:
  twin_machine: box
  repositories:
    - path: {repository}
      url: "{url}"
"""
    + TWIN
)

MOCKED = (
    """\
name: mocked
x-dtu:
  twin_machine: box
  rewrites:
    - match: {host}
      target: "http://mock:8080"
"""
    + TWIN
    + MOCK
)

TRUSTED = (
    """\
name: trusted
x-dtu:
  twin_machine: box
  repositories:
    - path: {repository}
      url: "{url}"
  rewrites:
    - match: {host}
      target: "http://mock:8080"
"""
    + TWIN
    + MOCK
)

FENCED = (
    """\
name: fenced
x-dtu:
  twin_machine: box
  repositories:
    - path: {repository}
      url: "{url}"
  allow: [pypi.org]
"""
    + TWIN
)

PLAIN = (
    """\
name: plain
x-dtu:
  twin_machine: box
"""
    + TWIN
)


@pytest.fixture
def needs_internet() -> None:
    """Skip a test that can only prove itself against the real internet when there is none."""
    with suppress(OSError), socket.create_connection(("github.com", 443), timeout=5):
        return
    pytest.skip("needs the real internet")


@pytest.fixture
def profile_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "profile"
    directory.mkdir()
    return directory


@pytest.fixture
def launch_universe(state_root: Path) -> Iterator[Callable[[Path], Universe]]:
    """Launch universes and take every one of them down afterwards, whether the test passed or failed.

    The records are what is swept, not what was returned: a launch that fails partway leaves containers running
    on purpose, for `doctor` and `docker compose logs`, and it has already written its record by then.
    """
    yield lambda profile: lib.launch(profile, timeout_seconds=LAUNCH_TIMEOUT)

    for record in sorted(state_root.iterdir()) if state_root.is_dir() else []:
        with suppress(DigitalTwinUniverseError):
            lib.destroy(record.name)


def _profile(directory: Path, compose: str, dockerfile: str = TWIN_DOCKERFILE) -> Path:
    """A profile directory: the Compose file, and the twin's Dockerfile beside it."""
    (directory / "Dockerfile").write_text(dockerfile)
    (directory / "compose.yaml").write_text(compose)
    return directory / "compose.yaml"


def _clone(universe: Universe, url: str = SERVED_URL, into: str = "repo") -> str:
    cloned = lib.execute(universe.id, f"git clone -q {url} {into} && cat {into}/MARKER.txt")
    assert cloned.exit_code == 0, cloned.stderr
    return cloned.stdout


Launch = Callable[[Path], Universe]


def test_a_served_repository_clones_inside_the_twin_from_its_github_url(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)

    assert _clone(universe) == MARKER


def test_the_working_tree_is_served_including_uncommitted_changes(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    (served_repository / "UNCOMMITTED.txt").write_text("never-committed\n")
    # A checkout people work in has hooks, and they call tools that exist on their machine and nowhere else.
    hook = served_repository / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)
    cloned = lib.execute(universe.id, f"git clone -q {SERVED_URL} repo && cat repo/UNCOMMITTED.txt")

    assert cloned.exit_code == 0, cloned.stderr
    assert cloned.stdout == "never-committed\n"


def test_every_local_branch_is_served_and_the_checked_out_one_is_the_default(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """A developer sits on a fix branch; a consumer pins `@main`. Both have to be there, and HEAD is the fix."""
    git = ["git", "-c", "user.email=t@dtu.invalid", "-c", "user.name=t", "-C", str(served_repository)]
    subprocess.run([*git, "checkout", "-q", "-b", "fix-thing"], check=True)
    (served_repository / "MARKER.txt").write_text("fixed-on-a-branch\n")
    subprocess.run([*git, "tag", "v0.1.0", "main"], check=True)
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)

    assert _clone(universe) == "fixed-on-a-branch\n"
    pinned = lib.execute(universe.id, f"git clone -q -b main {SERVED_URL} pinned && cat pinned/MARKER.txt")
    assert pinned.exit_code == 0, pinned.stderr
    assert pinned.stdout == MARKER
    tagged = lib.execute(universe.id, f"git ls-remote --tags {SERVED_URL}")
    assert "refs/tags/v0.1.0" in tagged.stdout


def test_a_served_repository_answers_the_other_ways_a_repository_is_reached(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """A repository is not only cloned: installers download archives, tools ask the API, people open pages."""
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)

    archive = lib.execute(universe.id, f"curl -sSL {SERVED_URL}/archive/refs/heads/main.tar.gz | tar tz")
    assert archive.exit_code == 0, archive.stderr
    assert "probe/MARKER.txt" in archive.stdout

    page = lib.execute(universe.id, f"curl -sS -o /dev/null -w '%{{http_code}}' {SERVED_URL}")
    assert page.stdout.strip() == "200"


def test_a_served_repository_installs_with_uv_from_its_github_url(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)
    installed = lib.execute(
        universe.id,
        f"uv tool install -q git+{SERVED_URL} && ~/.local/bin/dtu-probe",
        timeout_seconds=INSTALL_TIMEOUT,
    )

    assert installed.exit_code == 0, installed.stderr
    assert installed.stdout.strip() == "dtu-probe-installed"


def test_a_served_repository_clones_during_the_image_build(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """A build reaches the gateway on its own, but trusts the universe only where the Dockerfile says to.

    BuildKit predefines the proxy arguments, so `HTTPS_PROXY` arrives without being asked for. Nothing else does:
    a build argument no `ARG` declares is invisible to `RUN`. The CA has to be copied in, so the profile says so.
    """
    dockerfile = (
        TWIN_DOCKERFILE
        + "USER root\n"
        + "COPY --from=dtu-ca ca.crt /usr/local/share/ca-certificates/digital-twin-universe.crt\n"
        + "RUN update-ca-certificates\n"
        + "USER user\n"
        + f"RUN git clone -q {SERVED_URL} /home/user/at-build\n"
    )
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL), dockerfile)

    universe = launch_universe(profile)
    built = lib.execute(universe.id, "cat /home/user/at-build/MARKER.txt")

    assert built.exit_code == 0, built.stderr
    assert built.stdout == MARKER


def test_a_near_miss_url_is_not_rewritten(
    profile_directory: Path, launch_universe: Launch, served_repository: Path, needs_internet: None
) -> None:
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)

    for reached in (SERVED_URL, f"{SERVED_URL}.git", "https://GitHub.com/Fake-Org/Probe"):
        listed = lib.execute(universe.id, f"git ls-remote {reached} HEAD")
        assert listed.exit_code == 0, f"{reached} should be served: {listed.stderr}"

    for near_miss in (f"{SERVED_URL}-extra", f"{SERVED_URL}_old", "https://github.com/other-org/probe"):
        elsewhere = lib.execute(universe.id, f"git clone -q {near_miss} near && cat near/MARKER.txt")
        assert elsewhere.exit_code != 0, f"{near_miss} should not be served"
        assert MARKER.strip() not in elsewhere.stdout


def test_a_rewrite_answers_the_real_url_with_tls_from_a_mock_service(
    profile_directory: Path, launch_universe: Launch
) -> None:
    profile = _profile(profile_directory, MOCKED.format(host=MOCKED_HOST))

    universe = launch_universe(profile)
    answered = lib.execute(universe.id, f"curl -sS https://{MOCKED_HOST}/hello")

    assert answered.exit_code == 0, answered.stderr
    assert answered.stdout.strip() == "mock-answered"


def test_every_client_trusts_the_universe_ca(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """The variables exist because each client reads a different one; curl alone passes with the rest wrong."""
    profile = _profile(
        profile_directory, TRUSTED.format(repository=served_repository, url=SERVED_URL, host=MOCKED_HOST)
    )
    url = f"https://{MOCKED_HOST}/hello"
    over_https = {
        "curl": f"curl -sS {url}",
        "python": f"python3 -c 'import requests; print(requests.get(\"{url}\").text)'",
        "node": (
            f'node -e \'require("https").get("{url}", r => r.pipe(process.stdout))'
            '.on("error", e => { console.error(e.message); process.exit(1); })\''
        ),
    }

    universe = launch_universe(profile)

    for client, command in over_https.items():
        answered = lib.execute(universe.id, command)

        assert answered.exit_code == 0, f"{client} did not trust the universe CA: {answered.stderr}"
        assert answered.stdout.strip() == "mock-answered", client

    listed = lib.execute(universe.id, f"git ls-remote {SERVED_URL} HEAD")
    assert listed.exit_code == 0, f"git did not trust the universe CA: {listed.stderr}"

    installed = lib.execute(universe.id, f"uv tool install -q git+{SERVED_URL}", timeout_seconds=INSTALL_TIMEOUT)
    assert installed.exit_code == 0, f"uv did not trust the universe CA: {installed.stderr}"


def test_allow_refuses_everything_else_and_says_so_in_the_gateway_logs(
    profile_directory: Path, launch_universe: Launch, served_repository: Path, needs_internet: None
) -> None:
    profile = _profile(profile_directory, FENCED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)

    allowed = lib.execute(universe.id, "curl -sS -o /dev/null -w '%{http_code}' https://pypi.org/simple/")
    assert allowed.exit_code == 0, allowed.stderr
    assert allowed.stdout.strip() == "200"

    always_allowed = lib.execute(universe.id, f"git ls-remote {SERVED_URL} HEAD")
    assert always_allowed.exit_code == 0, f"a served repository is always allowed: {always_allowed.stderr}"

    # A refusal is an answer, not a broken connection: the gateway says who refused and why, in the body.
    refused = lib.execute(universe.id, "curl -sS -o /dev/null -w '%{http_code}' https://example.com/")
    assert refused.stdout.strip() == "403"
    assert "not in x-dtu.allow" in lib.execute(universe.id, "curl -sS https://example.com/").stdout

    assert "example.com" in compose_client(universe.id).compose.logs(services=["gateway"])


def test_a_profile_that_asks_for_nothing_renders_no_gateway_and_no_proxy_environment(
    profile_directory: Path, launch_universe: Launch
) -> None:
    profile = _profile(profile_directory, PLAIN)

    universe = launch_universe(profile)

    assert [service.name for service in universe.services] == ["box"]
    assert not (universe.state_path / "dtu.yaml").exists()

    environment = lib.execute(universe.id, "env")
    for absent in ("HTTP_PROXY", "HTTPS_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "UV_NATIVE_TLS"):
        assert absent not in environment.stdout


def test_the_rendered_overlay_is_a_compose_file_that_runs_by_hand(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """Nothing is hidden: the profile and the overlay together are exactly what `docker compose` would run."""
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    universe = launch_universe(profile)
    overlay = universe.state_path / "dtu.yaml"
    assert overlay.is_file()

    config = compose_client(universe.id, [profile, overlay]).compose.config(return_json=True)

    assert set(config["services"]) == {"box", "git", "gateway"}


def test_two_universes_serving_the_same_repository_do_not_collide(
    profile_directory: Path, launch_universe: Launch, served_repository: Path
) -> None:
    """Reserved names, networks, and volumes belong to a universe, so a second launch is not a second claim."""
    profile = _profile(profile_directory, SERVED.format(repository=served_repository, url=SERVED_URL))

    first = launch_universe(profile)
    second = launch_universe(profile)

    assert first.id != second.id
    assert _clone(first) == MARKER
    assert _clone(second, into="repo-two") == MARKER
