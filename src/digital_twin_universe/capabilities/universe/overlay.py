"""The overlay: the Compose file the tool renders beside a profile, holding everything Compose cannot say.

Render, then run. A universe is the profile plus this file, both readable, both runnable by hand with
`docker compose -f <profile> -f dtu.yaml`. A profile that serves nothing and rewrites nothing renders no overlay
at all: no git server, no gateway, no certificate authority, and no proxy in anyone's environment.
"""

from collections.abc import Iterable
import json
from pathlib import Path
import re
import secrets
import shutil
import socket
from typing import Any, NamedTuple
from urllib.parse import urlparse

import certifi
import yaml

from digital_twin_universe.capabilities.universe.compose import project_containers, service_name
from digital_twin_universe.capabilities.universe.profile import Profile, Repository, XDtu
from digital_twin_universe.capabilities.universe.state import UniverseRecord

TEMPLATES = Path(__file__).parent / "templates"
OVERLAY_FILE = "dtu.yaml"
ASSET_DIRECTORY = "overlay"
CA_FILE = "ca.crt"

GIT_SERVICE = "git"
GATEWAY_SERVICE = "gateway"
RESERVED_SERVICES = (GIT_SERVICE, GATEWAY_SERVICE)
GIT_PORT = 3000
GITEA_IMAGE = "gitea/gitea:1.26"
BOOTSTRAP = "serve-repositories.sh"
GATEWAY_PORT = 3128
SERVED_ROOT = "/dtu"
CA_MOUNT = "/etc/dtu/ca.crt"
CA_CONTEXT = "dtu-ca"
MITM_CA = "/home/mitmproxy/.mitmproxy/mitmproxy-ca-cert.pem"

# Each client reads its own variable and none reads another's, so trust is stated once in every dialect.
CA_ENVIRONMENT = (
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "GIT_SSL_CAINFO",
    "NODE_EXTRA_CA_CERTS",
    "PIP_CERT",
    "CURL_CA_BUNDLE",
)
NO_PROXY = f"localhost,127.0.0.1,::1,{GIT_SERVICE},{GATEWAY_SERVICE}"


class Overlay(NamedTuple):
    """What was rendered for a universe, and what `launch` has to do with it."""

    path: Path | None
    bootstrap: list[str]
    ca_path: Path | None

    @property
    def files(self) -> list[Path]:
        return [self.path] if self.path is not None else []


def render(record: UniverseRecord, profile: Profile) -> Overlay:
    """Write the overlay for a universe, or nothing when the profile asks for nothing the overlay provides."""
    x_dtu = profile.x_dtu
    if not x_dtu.repositories and not x_dtu.needs_gateway:
        return Overlay(path=None, bootstrap=[], ca_path=None)

    assets = record.state_path / ASSET_DIRECTORY
    assets.mkdir(parents=True, exist_ok=True)
    services: dict[str, Any] = {}
    bootstrap: list[str] = []
    ca_path: Path | None = None

    if x_dtu.repositories:
        services[GIT_SERVICE] = _git_service(assets, profile.path, x_dtu.repositories)
        bootstrap.append(GIT_SERVICE)
    build_proxy = ""
    if x_dtu.needs_gateway:
        ca_path = _ca_path(assets)
        published = _free_port()
        services[GATEWAY_SERVICE] = _gateway_service(assets, profile, published)
        bootstrap.append(GATEWAY_SERVICE)
        build_proxy = f"http://127.0.0.1:{published}"

    for name in profile.services:
        wiring = _wiring(name, profile, ca_path, bootstrap, build_proxy)
        if wiring:
            services[name] = wiring

    path = record.state_path / OVERLAY_FILE
    path.write_text(yaml.safe_dump({"services": services}, sort_keys=False), encoding="utf-8")
    return Overlay(path=path, bootstrap=bootstrap, ca_path=ca_path)


def export_ca(record: UniverseRecord, overlay: Overlay) -> None:
    """Take the certificate authority the gateway minted out to the host, where builds and mounts can reach it.

    Every variable that names a bundle replaces the trust store rather than adding to it, so the universe's own
    authority is written after the public roots: a rewritten host and the real internet both have to verify.
    """
    if overlay.ca_path is None:
        return
    gateway = next(
        (container for container in project_containers(record.id) if service_name(container) == GATEWAY_SERVICE), None
    )
    if gateway is None:
        return
    minted = overlay.ca_path.with_suffix(".minted")
    gateway.copy_from(MITM_CA, minted)
    overlay.ca_path.write_text(
        f"{Path(certifi.where()).read_text(encoding='utf-8')}\n{minted.read_text(encoding='utf-8')}", encoding="utf-8"
    )
    minted.unlink()


def _git_service(assets: Path, profile_path: Path, repositories: Iterable[Repository]) -> dict[str, Any]:
    """Gitea, holding the repositories, at the address the gateway rewrites to.

    Gitea rather than a bare git server because a repository is reached in more ways than `git clone`: the archive
    endpoints an installer downloads, the API a tool queries, and the web pages a person opens are all there too.
    """
    context = assets / GIT_SERVICE
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATES / BOOTSTRAP, context / BOOTSTRAP)
    served = _served(profile_path, repositories)
    return {
        "image": GITEA_IMAGE,
        # The image's entrypoint hands to s6, which starts the server and nothing else; the universe has to
        # prepare the database, the owner, and the repositories first, so this stands in for both.
        "entrypoint": ["/bin/sh", f"/dtu/{BOOTSTRAP}"],
        "environment": {
            "DTU_PASSWORD": secrets.token_hex(16),
            "GITEA__security__INSTALL_LOCK": "true",
            # The password above is generated, never seen, and never leaves the universe.
            "GITEA__security__PASSWORD_COMPLEXITY": "off",
            "GITEA__server__ROOT_URL": f"http://{GIT_SERVICE}:{GIT_PORT}/",
            "GITEA__server__HTTP_PORT": str(GIT_PORT),
            "GITEA__server__DISABLE_SSH": "true",
            "GITEA__database__DB_TYPE": "sqlite3",
            "GITEA__database__PATH": "/data/gitea/gitea.db",
            # Pushing to a repository that does not exist is how the repositories come to exist, and what is
            # served has to be readable without an account, since nothing in the twin has one.
            "GITEA__repository__ENABLE_PUSH_CREATE_USER": "true",
            "GITEA__repository__DEFAULT_PRIVATE": "public",
            "GITEA__repository__DEFAULT_PUSH_CREATE_PRIVATE": "false",
            "GITEA__service__DISABLE_REGISTRATION": "true",
            "GITEA__cron__ENABLED": "false",
        },
        "volumes": [
            f"{context / BOOTSTRAP}:/dtu/{BOOTSTRAP}:ro",
            *(f"{path}:/repos/{name}:ro" for name, path in served.items()),
        ],
        "healthcheck": {
            # Ready means the repositories are there, not that the server answers: a clone arriving between the
            # two would find nothing. localhost resolves to IPv6 first here, where nothing is listening.
            "test": [
                "CMD",
                "wget",
                "-q",
                "-O",
                "/dev/null",
                f"http://127.0.0.1:{GIT_PORT}{SERVED_ROOT}/{next(iter(served))}/info/refs?service=git-upload-pack",
            ],
            "interval": "2s",
            "timeout": "5s",
            "retries": 5,
            "start_period": "180s",
        },
    }


def _gateway_service(assets: Path, profile: Profile, published: int) -> dict[str, Any]:
    """The only route out: it rewrites what the profile redirects and refuses what the profile does not allow."""
    context = assets / GATEWAY_SERVICE
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATES / "gateway.py", context / "gateway.py")
    x_dtu = profile.x_dtu
    rules = _rules(profile)
    allow = _allowed(x_dtu, rules)
    return {
        "image": "mitmproxy/mitmproxy:12.1.2",
        # Published for image builds alone: BuildKit's default builder joins no Compose network, so a build reaches
        # the gateway the only way it can, over the host. Everything at runtime uses the service name instead.
        "ports": [f"127.0.0.1:{published}:{GATEWAY_PORT}"],
        "volumes": [f"{context}:/dtu:ro"],
        "environment": {
            "DTU_RULES": json.dumps(rules),
            "DTU_ALLOW": json.dumps(allow),
        },
        "command": [
            "mitmdump",
            "--listen-port",
            str(GATEWAY_PORT),
            "--set",
            "block_global=false",
            # Without this mitmproxy dials the real host before the addon sees the request, so a rewritten name
            # that exists nowhere fails as a 502 before it can be redirected to the service standing in for it.
            "--set",
            "connection_strategy=lazy",
            "--set",
            "confdir=/home/mitmproxy/.mitmproxy",
            *_interception(rules, allow),
            "-s",
            "/dtu/gateway.py",
        ],
        "healthcheck": {
            "test": ["CMD", "test", "-f", MITM_CA],
            "interval": "2s",
            "timeout": "5s",
            "retries": 15,
            "start_period": "30s",
        },
    }


def _wiring(
    name: str, profile: Profile, ca_path: Path | None, bootstrap: list[str], build_proxy: str
) -> dict[str, Any]:
    """What a profile's own service gains from the universe: the CA, the proxy, and an order to start in."""
    wiring: dict[str, Any] = {}
    if bootstrap:
        wiring["depends_on"] = {service: {"condition": "service_healthy"} for service in bootstrap}
    if ca_path is None:
        return wiring
    environment = dict.fromkeys(CA_ENVIRONMENT, CA_MOUNT) | {
        # uv ignores SSL_CERT_FILE unless told to use the system store. The newer name is read first and quiets
        # the deprecation warning the older one now draws; the older name is what a uv before 0.9 understands.
        "UV_SYSTEM_CERTS": "true",
        "UV_NATIVE_TLS": "true",
        # Node makes no use of a proxy variable unless it is told to, and has read this one since Node 24.
        "NODE_USE_ENV_PROXY": "1",
        "HTTP_PROXY": _gateway_url(),
        "HTTPS_PROXY": _gateway_url(),
        "NO_PROXY": NO_PROXY,
    }
    if _rewrites_github(profile.x_dtu):
        # uv reaches a GitHub repository through api.github.com, which is a different host and not rewritten.
        environment["UV_NO_GITHUB_FAST_PATH"] = "true"
    wiring["environment"] = environment
    wiring["volumes"] = [f"{ca_path}:{CA_MOUNT}:ro"]
    if _builds(name, profile):
        # A build sees the proxy without asking, since BuildKit predefines it, but reaches the CA only through a
        # build context the Dockerfile copies from: trust cannot be handed to a build any other way.
        wiring["build"] = {
            "args": {"HTTP_PROXY": build_proxy, "HTTPS_PROXY": build_proxy, "NO_PROXY": "localhost,127.0.0.1,::1"},
            "additional_contexts": {CA_CONTEXT: str(ca_path.parent)},
            "network": "host",
        }
    return wiring


def _interception(rules: list[dict[str, str]], allow: list[str]) -> list[str]:
    """What to terminate TLS for. Everything else is tunnelled to the real internet, with its own certificate.

    Only a rewritten host has to be opened, and opening more would make every unrelated host depend on the CA
    reaching whatever talks to it. An allowlist is the exception: refusing a request means reading it first.
    """
    if allow:
        return []
    hosts = "|".join(sorted({re.escape(rule["host"]) for rule in rules}))
    return ["--ignore-hosts", rf"^(?!(?:{hosts})(?::\d+)?$).+$"]


def _rules(profile: Profile) -> list[dict[str, str]]:
    """Every host and path prefix the gateway answers for, longest path first, as the profile reference specifies."""
    rules: list[dict[str, str]] = []
    x_dtu = profile.x_dtu
    for repository in x_dtu.repositories:
        if repository.url is None:
            continue
        name = _resolved(profile.path, repository).name
        parsed = urlparse(repository.url)
        rules.append(
            {
                "host": (parsed.hostname or "").lower(),
                "path": "/" + parsed.path.strip("/").removesuffix(".git"),
                "target": f"http://{GIT_SERVICE}:{GIT_PORT}{SERVED_ROOT}/{name}",
            }
        )
    for rewrite in x_dtu.rewrites:
        host, _, path = rewrite.match.partition("/")
        rules.append({"host": host.lower(), "path": "/" + path.strip("/"), "target": rewrite.target})
    return sorted(rules, key=lambda rule: len(rule["path"]), reverse=True)


def _allowed(x_dtu: XDtu, rules: list[dict[str, str]]) -> list[str]:
    """An allowlist always covers what the profile itself serves and rewrites; refusing those would be a trap."""
    if not x_dtu.allow:
        return []
    return sorted({*(host.lower() for host in x_dtu.allow), *(rule["host"] for rule in rules)})


def _served(profile_path: Path, repositories: Iterable[Repository]) -> dict[str, Path]:
    """Each repository's name in the universe and its place on the host.

    The name is the resolved directory's, so `../..` serves the directory it points at, not a repository called `..`.
    """
    resolved = [_resolved(profile_path, repository) for repository in repositories]
    return {path.name: path for path in resolved}


def _resolved(profile_path: Path, repository: Repository) -> Path:
    path = repository.path
    return path if path.is_absolute() else (profile_path.parent / path).resolve()


def _rewrites_github(x_dtu: XDtu) -> bool:
    return any(
        (urlparse(repository.url or "").hostname or "").endswith("github.com") for repository in x_dtu.repositories
    )


def _builds(name: str, profile: Profile) -> bool:
    return bool((profile.config.get("services") or {}).get(name, {}).get("build"))


def _ca_path(assets: Path) -> Path:
    """The certificate authority's place on the host, created empty so a mount of it cannot become a directory."""
    path = assets / CA_CONTEXT / CA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def _gateway_url() -> str:
    return f"http://{GATEWAY_SERVICE}:{GATEWAY_PORT}"


def _free_port() -> int:
    """A host port nothing holds now, for the gateway to publish so that image builds can reach it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
