"""`validate-profile`: the Compose side, the `x-dtu` schema, the universe invariants, and the realism warnings.

Every check runs `docker compose config`, so the whole file needs a daemon. Nothing here launches anything.
"""

from collections.abc import Callable
from pathlib import Path
import textwrap
from typing import Any

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe.profile import EXAMPLES_DIRECTORY
from digital_twin_universe.schemas import DigitalTwinUniverseError

pytestmark = pytest.mark.needs_docker

MakeRepository = Callable[[str, dict[str, str]], Path]

VALID = """\
name: valid
x-dtu:
  twin_machine: box
services:
  box:
    image: alpine:3.20
    user: app
    command: sleep infinity
    healthcheck:
      test: [CMD, "true"]
"""

# Each invariant, as the smallest profile that breaks it: the expected error code, then the profile.
INVALID_PROFILES: dict[str, tuple[str, str]] = {
    "a named twin that is not a service": (
        "twin-missing",
        """\
        name: named-twin-absent
        x-dtu:
          twin_machine: web
        services:
          box: {image: alpine:3.20}
          db: {image: alpine:3.20}
        """,
    ),
    "several services and no twin named": (
        "twin-missing",
        """\
        name: no-twin-named
        services:
          box: {image: alpine:3.20}
          db: {image: alpine:3.20}
        """,
    ),
    "a service using a reserved name": (
        "reserved-service-name",
        """\
        name: reserved
        x-dtu:
          twin_machine: box
        services:
          box: {image: alpine:3.20}
          gateway: {image: alpine:3.20}
        """,
    ),
    "a twin gated behind a compose profile": (
        "twin-excluded-by-compose-profile",
        """\
        name: gated-twin
        x-dtu:
          twin_machine: box
        services:
          box:
            image: alpine:3.20
            profiles: [manual]
        """,
    ),
    "a url on a port the twin does not publish": (
        "url-port-unpublished",
        """\
        name: unpublished-url
        x-dtu:
          twin_machine: box
          urls:
            - {port: 8000, path: /chat/}
        services:
          box:
            image: alpine:3.20
            ports: ["8410:80", "53:8000/udp"]
        """,
    ),
    "a url path that does not start at the root": (
        "x-dtu-invalid",
        """\
        name: relative-url
        x-dtu:
          twin_machine: box
          urls:
            - {port: 80, path: chat/}
        services:
          box:
            image: alpine:3.20
            ports: ["8410:80"]
        """,
    ),
    "windows and linux services together": (
        "mixed-platforms",
        """\
        name: mixed
        x-dtu:
          twin_machine: box
        services:
          box: {image: alpine:3.20, platform: linux/amd64}
          helper: {image: alpine:3.20, platform: windows/amd64}
        """,
    ),
}

# Each realism check, as a profile that is legitimate but less realistic than it probably means to be.
WARNING_PROFILES: dict[str, str] = {
    "no-long-running-command": """\
        name: exits
        services:
          box:
            image: alpine:3.20
            user: app
            healthcheck: {test: [CMD, "true"]}
        """,
    "bind-mount": """\
        name: mounted
        services:
          box:
            image: alpine:3.20
            user: app
            command: sleep infinity
            volumes: ["./src:/src"]
            healthcheck: {test: [CMD, "true"]}
        """,
    "no-healthcheck": """\
        name: unprobed
        services:
          box:
            image: alpine:3.20
            user: app
            command: sleep infinity
        """,
    "runs-as-root": """\
        name: rooted
        services:
          box:
            image: alpine:3.20
            user: root
            command: sleep infinity
            healthcheck: {test: [CMD, "true"]}
        """,
}

OWN_NETWORK = """\
name: own-network
x-dtu:
  twin_machine: box
{rewrites}
services:
  box:
    image: alpine:3.20
    user: app
    command: sleep infinity
    networks: [outside]
    healthcheck: {{test: [CMD, "true"]}}
networks:
  outside: {{}}
"""
REWRITES = "  rewrites:\n    - {match: api.example.com, target: 'http://mock:8080'}"


def _profile(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "compose.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def _codes(findings: list[Any]) -> list[str]:
    return [finding.code for finding in findings]


def test_a_sound_profile_reports_what_it_resolved_to(tmp_path: Path) -> None:
    report = lib.validate_profile(_profile(tmp_path, VALID))

    assert report.ok is True
    assert report.errors == []
    assert report.path == (tmp_path / "compose.yaml").resolve()
    assert report.name == "valid"
    assert report.twin_machine == "box"
    assert report.services == ["box"]


def _with_url(host: str) -> str:
    urls = f"  twin_machine: box\n  urls:\n    - {{port: 80, path: /chat/, host: {host}}}\n"
    body = VALID.replace("  twin_machine: box\n", urls)
    return body.replace("    user: app\n", '    user: app\n    ports: ["8410:80"]\n')


@pytest.mark.parametrize("host", ["localhost", "Site.LocalHost", "127.0.0.1"])
def test_a_url_on_a_published_port_at_a_loopback_name_is_sound_without_warning(tmp_path: Path, host: str) -> None:
    report = lib.validate_profile(_profile(tmp_path, _with_url(host)))

    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []


def test_a_url_host_this_machine_cannot_resolve_is_a_warning(tmp_path: Path) -> None:
    # `.invalid` never resolves, by RFC 6761.
    report = lib.validate_profile(_profile(tmp_path, _with_url("site.invalid")))

    assert report.ok is True
    assert _codes(report.warnings) == ["url-host-unresolved"]
    assert report.warnings[0].location == "x-dtu.urls[0].host"
    assert "127.0.0.1 site.invalid" in report.warnings[0].remedy


@pytest.mark.parametrize("case", list(INVALID_PROFILES))
def test_each_invariant_is_an_error(tmp_path: Path, case: str) -> None:
    expected, body = INVALID_PROFILES[case]

    report = lib.validate_profile(_profile(tmp_path, body))

    assert report.ok is False
    assert expected in _codes(report.errors)


@pytest.mark.parametrize("expected", list(WARNING_PROFILES))
def test_each_realism_check_is_a_warning_and_leaves_the_profile_launchable(tmp_path: Path, expected: str) -> None:
    report = lib.validate_profile(_profile(tmp_path, WARNING_PROFILES[expected]))

    assert expected in _codes(report.warnings)
    assert report.ok is True
    assert report.errors == []


def test_every_problem_is_reported_not_only_the_first(tmp_path: Path) -> None:
    report = lib.validate_profile(
        _profile(
            tmp_path,
            """\
            name: several
            x-dtu:
              twin_machine: web
              rewrites:
                - {match: api.example.com, target: "http://mock:8080"}
            services:
              gateway: {image: alpine:3.20}
              box:
                image: alpine:3.20
                networks: [outside]
            networks:
              outside: {}
            """,
        )
    )

    assert report.ok is False
    assert set(_codes(report.errors)) >= {"twin-missing", "reserved-service-name", "routes-around-gateway"}


def test_a_finding_points_at_the_key_it_is_about(tmp_path: Path) -> None:
    report = lib.validate_profile(_profile(tmp_path, WARNING_PROFILES["bind-mount"]))

    bind_mount = next(finding for finding in report.warnings if finding.code == "bind-mount")
    assert bind_mount.location == "services.box.volumes[0]"
    assert bind_mount.remedy


def test_routing_around_the_gateway_is_an_error_only_when_a_gateway_would_exist(tmp_path: Path) -> None:
    with_gateway = lib.validate_profile(_profile(tmp_path, OWN_NETWORK.format(rewrites=REWRITES)))
    without_gateway = lib.validate_profile(_profile(tmp_path, OWN_NETWORK.format(rewrites="")))

    assert with_gateway.ok is False
    assert "routes-around-gateway" in _codes(with_gateway.errors)
    assert without_gateway.ok is True


def test_a_repository_path_that_is_not_a_git_repository_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()

    report = lib.validate_profile(
        _profile(
            tmp_path,
            """\
            name: unserved
            x-dtu:
              twin_machine: box
              repositories:
                - {path: ./plain, url: "https://github.com/org/repo"}
            services:
              box: {image: alpine:3.20, command: sleep infinity}
            """,
        )
    )

    assert report.ok is False
    assert "repository-not-git" in _codes(report.errors)


def test_a_repository_is_resolved_relative_to_the_profile(tmp_path: Path, make_repository: MakeRepository) -> None:
    repository = make_repository("thing", {"README.md": "hi\n"})

    report = lib.validate_profile(
        _profile(
            tmp_path,
            f"""\
            name: served
            x-dtu:
              twin_machine: box
              repositories:
                - path: {repository}
                  url: "https://github.com/org/thing"
            services:
              box:
                image: alpine:3.20
                user: app
                command: sleep infinity
                healthcheck: {{test: [CMD, "true"]}}
            """,
        )
    )

    assert report.ok is True


def test_a_repository_url_without_a_host_and_path_is_an_error(tmp_path: Path, make_repository: MakeRepository) -> None:
    repository = make_repository("thing", {"README.md": "hi\n"})

    report = lib.validate_profile(
        _profile(
            tmp_path,
            f"""\
            name: bad-url
            x-dtu:
              twin_machine: box
              repositories:
                - path: {repository}
                  url: "github.com"
            services:
              box: {{image: alpine:3.20, command: sleep infinity}}
            """,
        )
    )

    assert report.ok is False
    assert "repository-url-invalid" in _codes(report.errors)


def test_an_unset_variable_is_an_error_naming_it_rather_than_a_raise(tmp_path: Path) -> None:
    report = lib.validate_profile(
        _profile(
            tmp_path,
            """\
            name: needs-a-token
            services:
              box:
                image: alpine:3.20
                command: sleep infinity
                environment:
                  TOKEN: ${DTU_TEST_ABSENT_TOKEN:?set DTU_TEST_ABSENT_TOKEN first}
            """,
        )
    )

    assert report.ok is False
    assert "env-missing" in _codes(report.errors)
    assert "DTU_TEST_ABSENT_TOKEN" in report.errors[0].message


def test_composes_own_rejection_is_an_error_in_composes_words(tmp_path: Path) -> None:
    report = lib.validate_profile(_profile(tmp_path, "name: broken\nservices:\n  box: not-a-mapping\n"))

    assert report.ok is False
    assert "profile-invalid" in _codes(report.errors)


def test_an_unknown_profile_raises_rather_than_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.validate_profile("no-such-profile")

    assert raised.value.code == "profile-not-found"


def test_launch_refuses_what_validate_rejects(tmp_path: Path, state_root: Path) -> None:
    _, body = INVALID_PROFILES["a service using a reserved name"]
    profile = _profile(tmp_path, body)

    assert lib.validate_profile(profile).ok is False
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.launch(profile, timeout_seconds=60)

    assert raised.value.code == "profile-invalid"
    assert not state_root.exists() or list(state_root.iterdir()) == []


def test_the_shipped_example_validates_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "interpolation-only")

    report = lib.validate_profile("copilot-cli")

    assert report.path == (EXAMPLES_DIRECTORY / "copilot-cli" / "compose.yaml").resolve()
    assert report.ok is True
    assert report.errors == []
    assert report.warnings == []
