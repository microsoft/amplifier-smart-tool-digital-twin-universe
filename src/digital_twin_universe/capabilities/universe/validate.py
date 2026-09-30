"""Validate a profile: Compose's own validation, the `x-dtu` schema, the universe invariants, and realism warnings.

Errors say the profile is not a universe. Warnings say it is less realistic than it probably means to be, which is
sometimes exactly what its author wanted, so they never decide the verdict.
"""

import ipaddress
from pathlib import Path
import socket
from typing import Any, NamedTuple
from urllib.parse import urlparse

from pydantic import ValidationError

from digital_twin_universe.capabilities.universe import profile as profile_module
from digital_twin_universe.capabilities.universe.overlay import RESERVED_SERVICES
from digital_twin_universe.capabilities.universe.profile import ALL_PROFILES, X_DTU, Profile, XDtu
from digital_twin_universe.schemas import DigitalTwinUniverseError, Finding, ProfileReport

DEFAULT_NETWORK = "default"
ROOT_USERS = ("root", "0", "root:root", "0:0")
BYPASSES = ("network_mode", "dns", "extra_hosts")
LOCALHOST = "localhost"


class Validation(NamedTuple):
    """A report, plus what the rest of the tool needs when the profile is sound.

    `profile` is None when there are errors. `unreadable` carries the failure when Compose could not read the file
    at all, so `launch` can raise it as itself rather than flattening it into `profile-invalid`.
    """

    report: ProfileReport
    profile: Profile | None
    unreadable: DigitalTwinUniverseError | None = None


def validate_profile(profile: str | Path) -> ProfileReport:
    """Whether a profile can be launched, and what would be unrealistic about it if it were."""
    return validate(profile).report


def validate(profile: str | Path) -> Validation:
    path = profile_module.resolve_path(profile)
    try:
        config = profile_module.read_config(path)
    except DigitalTwinUniverseError as error:
        if error.code == "docker-unavailable":
            raise
        return _unreadable(path, error)
    return _validated(path, config)


def _unreadable(path: Path, error: DigitalTwinUniverseError) -> Validation:
    """Compose refused the file, so there is nothing to check: its own message is the whole report."""
    finding = Finding(code=error.code, location=None, message=error.message, remedy=error.remedy)
    report = ProfileReport(
        path=path, name=path.parent.name, twin_machine="", services=[], ok=False, errors=[finding], warnings=[]
    )
    return Validation(report, None, unreadable=error)


def _validated(path: Path, config: dict[str, Any]) -> Validation:
    services: dict[str, Any] = config.get("services") or {}
    x_dtu, schema_errors = _x_dtu(config)
    errors = list(schema_errors)
    twin = profile_module.twin_machine(list(services), x_dtu)
    errors += _twin(path, twin, services)
    errors += _reserved(services)
    errors += _platforms(services)
    errors += _repositories(path, x_dtu)
    if twin in services:
        errors += _urls(twin, services[twin], x_dtu)
    if x_dtu.needs_gateway:
        errors += _isolation(services)
    warnings = _realism(twin, services[twin]) if twin in services else []
    warnings += _url_hosts(x_dtu)
    report = ProfileReport(
        path=path,
        name=config.get("name") or path.parent.name,
        twin_machine=twin if twin in services else "",
        services=list(services),
        ok=not errors,
        errors=errors,
        warnings=warnings,
    )
    if errors:
        return Validation(report, None)
    profile = Profile(
        path=path,
        name=report.name,
        description=x_dtu.description,
        twin_machine=twin,
        services=list(services),
        x_dtu=x_dtu,
        config=config,
    )
    return Validation(report, profile)


def _x_dtu(config: dict[str, Any]) -> tuple[XDtu, list[Finding]]:
    """The `x-dtu` block against its schema. An unreadable block is replaced by an empty one so checking continues."""
    try:
        return XDtu.model_validate(config.get(X_DTU) or {}), []
    except ValidationError as error:
        findings = [
            Finding(
                code="x-dtu-invalid",
                location=".".join([X_DTU, *(str(part) for part in problem["loc"])]),
                message=f"`{X_DTU}` is not valid: {problem['msg']}.",
                remedy="Correct the key against the profile reference; `x-dtu` takes no keys beyond the documented.",
            )
            for problem in error.errors()
        ]
        return XDtu(), findings


def _twin(path: Path, twin: str, services: dict[str, Any]) -> list[Finding]:
    if twin in services:
        return []
    if _gated_behind_a_compose_profile(path, twin):
        return [
            Finding(
                code="twin-excluded-by-compose-profile",
                location=f"services.{twin}.profiles",
                message=f"The twin {twin!r} is gated behind a Compose `profiles:` entry, so it would not start.",
                remedy=f"Remove `profiles:` from {twin!r}, or make a service that always starts the twin.",
            )
        ]
    return [
        Finding(
            code="twin-missing",
            location=f"{X_DTU}.twin_machine",
            message=f"No twin: `{X_DTU}.twin_machine` is {twin!r} and the services are {sorted(services)}.",
            remedy=f"Set `{X_DTU}.twin_machine` to the service the software under test runs in.",
        )
    ]


def _gated_behind_a_compose_profile(path: Path, twin: str) -> bool:
    """Whether the missing twin is in the file after all, left out only because a `profiles:` entry gates it."""
    try:
        with_every_profile = profile_module.read_config(path, profiles=ALL_PROFILES)
    except DigitalTwinUniverseError:
        return False
    service = (with_every_profile.get("services") or {}).get(twin)
    return service is not None and bool(service.get("profiles"))


def _reserved(services: dict[str, Any]) -> list[Finding]:
    return [
        Finding(
            code="reserved-service-name",
            location=f"services.{name}",
            message=f"{name!r} is the name the overlay gives its own service, so a profile cannot use it.",
            remedy=f"Rename the service; `{'`, `'.join(RESERVED_SERVICES)}` belong to the tool.",
        )
        for name in services
        if name in RESERVED_SERVICES
    ]


def _platforms(services: dict[str, Any]) -> list[Finding]:
    """Windows and Linux containers cannot share a daemon, so one file cannot ask for both."""
    declared = {name: str(service.get("platform") or "") for name, service in services.items()}
    windows = sorted(name for name, platform in declared.items() if platform.startswith("windows"))
    linux = sorted(name for name, platform in declared.items() if platform.startswith("linux"))
    if not (windows and linux):
        return []
    return [
        Finding(
            code="mixed-platforms",
            location=f"services.{windows[0]}.platform",
            message=f"Windows services ({', '.join(windows)}) and Linux services ({', '.join(linux)}) in one file.",
            remedy="Split them into two profiles; a universe runs on one daemon, which serves one of the two.",
        )
    ]


def _repositories(path: Path, x_dtu: XDtu) -> list[Finding]:
    findings: list[Finding] = []
    for index, repository in enumerate(x_dtu.repositories):
        location = f"{X_DTU}.repositories[{index}]"
        resolved = repository.path if repository.path.is_absolute() else path.parent / repository.path
        if not (resolved / ".git").exists():
            findings.append(
                Finding(
                    code="repository-not-git",
                    location=f"{location}.path",
                    message=f"{resolved} is not a git repository, so there is nothing to serve from it.",
                    remedy="Point `path` at a git repository, or drop the entry.",
                )
            )
        if repository.url is not None and not _servable(repository.url):
            findings.append(
                Finding(
                    code="repository-url-invalid",
                    location=f"{location}.url",
                    message=f"{repository.url!r} is not a URL with a host and a repository path.",
                    remedy="Write the URL the repository stands in for, such as `https://github.com/org/repo`.",
                )
            )
    return findings


def _urls(twin: str, service: dict[str, Any], x_dtu: XDtu) -> list[Finding]:
    """A URL the profile promises must sit on a port the twin publishes, or the host could never open it."""
    published = sorted({int(port["target"]) for port in service.get("ports") or [] if port.get("protocol") == "tcp"})
    return [
        Finding(
            code="url-port-unpublished",
            location=f"{X_DTU}.urls[{index}].port",
            message=f"The twin {twin!r} publishes no TCP port {spec.port}; its published ports are {published}.",
            remedy=f"Set `port` to the container port the twin listens on and add it to `services.{twin}.ports`.",
        )
        for index, spec in enumerate(x_dtu.urls)
        if spec.port not in published
    ]


def _url_hosts(x_dtu: XDtu) -> list[Finding]:
    """A URL host other than loopback is a promise the tool cannot keep; it can only say whether this machine keeps it.

    `*.localhost` is exempt: browsers and curl resolve it to loopback themselves, so a failed system lookup here
    would warn about the case that works.
    """
    findings: list[Finding] = []
    for index, spec in enumerate(x_dtu.urls):
        if _is_localhost(spec.host) or _resolves_to_loopback(spec.host):
            continue
        findings.append(
            Finding(
                code="url-host-unresolved",
                location=f"{X_DTU}.urls[{index}].host",
                message=f"{spec.host!r} does not resolve to this machine, so the URL reported for it will not open here.",
                remedy=f"Use a `*.localhost` name, or add `127.0.0.1 {spec.host}` to the hosts file on each machine.",
            )
        )
    return findings


def _is_localhost(host: str) -> bool:
    lowered = host.lower()
    return lowered == LOCALHOST or lowered.endswith(f".{LOCALHOST}")


def _resolves_to_loopback(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(str(address)).is_loopback for address in addresses)


def _servable(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and parsed.path.strip("/") != ""


def _isolation(services: dict[str, Any]) -> list[Finding]:
    """With a gateway present, anything that gives a service its own route out defeats the point of having one."""
    findings: list[Finding] = []
    for name, service in services.items():
        for key in BYPASSES:
            if service.get(key):
                findings.append(_bypass(name, key))
        networks = list(service.get("networks") or {})
        if networks and networks != [DEFAULT_NETWORK]:
            findings.append(_bypass(name, "networks"))
    return findings


def _bypass(name: str, key: str) -> Finding:
    return Finding(
        code="routes-around-gateway",
        location=f"services.{name}.{key}",
        message=f"`{key}` on {name!r} gives it a route the gateway does not control, so rewrites and `allow` miss it.",
        remedy=f"Remove `{key}`; the overlay puts every service on one network behind the gateway.",
    )


def _realism(twin: str, service: dict[str, Any]) -> list[Finding]:
    """Each of these is legitimate somewhere, and each is usually a profile testing less than its author thinks."""
    findings: list[Finding] = []
    if service.get("command") is None and service.get("entrypoint") is None:
        findings.append(
            Finding(
                code="no-long-running-command",
                location=f"services.{twin}.command",
                message=f"The twin {twin!r} sets no `command` or `entrypoint`, so it runs whatever its image does.",
                remedy="Give the twin a command that keeps running, such as `sleep infinity`, or `exec` finds nothing.",
            )
        )
    for index, volume in enumerate(service.get("volumes") or []):
        if volume.get("type") == "bind":
            findings.append(
                Finding(
                    code="bind-mount",
                    location=f"services.{twin}.volumes[{index}]",
                    message=f"The twin mounts {volume.get('source')} from the host, which it can then change.",
                    remedy="Serve the repository and install it in the twin, the way a user would get it.",
                )
            )
    if not service.get("healthcheck"):
        findings.append(
            Finding(
                code="no-healthcheck",
                location=f"services.{twin}.healthcheck",
                message=f"The twin {twin!r} has no healthcheck, so `launch` cannot tell when it is ready.",
                remedy="Add a healthcheck that probes what the twin is for, so a following `exec` never races it.",
            )
        )
    if str(service.get("user") or "") in ROOT_USERS:
        findings.append(
            Finding(
                code="runs-as-root",
                location=f"services.{twin}.user",
                message=f"The twin {twin!r} runs as root, which a person's own machine usually does not.",
                remedy="Create a user in the Dockerfile and `USER` it, so installers behave as they would for a user.",
            )
        )
    return findings
