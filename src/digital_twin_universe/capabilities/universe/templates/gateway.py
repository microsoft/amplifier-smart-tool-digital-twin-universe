"""The universe's gateway: rewrite what the profile redirects, refuse what it does not allow, pass the rest through.

A mitmproxy addon. The rules arrive as JSON in the environment so this file is the same in every universe, and
every decision it makes is printed, so `docker compose logs gateway` says why a request went where it did.
"""

import json
import os
from urllib.parse import urlparse

from mitmproxy import http

RULES = json.loads(os.environ.get("DTU_RULES", "[]"))
ALLOW = [host.lower() for host in json.loads(os.environ.get("DTU_ALLOW", "[]"))]
GIT_SUFFIX = ".git"
DEFAULT_PORTS = {"http": 80, "https": 443}


def _remainder(path: str, prefix: str) -> str | None:
    """What follows `prefix` in `path` when it ends at a boundary, else None.

    A repository is a unit: `/org/repo` matches `/org/repo`, `/org/repo/...`, and `/org/repo.git/...`, and never
    `/org/repo-extra`, however close the name. Case is ignored, as GitHub and a git server both do.
    """
    lowered, wanted = path.lower(), prefix.lower().rstrip("/")
    if not lowered.startswith(wanted):
        return None
    rest = path[len(wanted) :]
    if rest == "" or rest.startswith("/"):
        return rest
    if lowered[len(wanted) :].startswith(GIT_SUFFIX):
        after = rest[len(GIT_SUFFIX) :]
        if after == "" or after.startswith("/"):
            return after
    return None


def request(flow: http.HTTPFlow) -> None:
    host = flow.request.pretty_host.lower()
    path, _, query = flow.request.path.partition("?")
    for rule in RULES:
        if host != rule["host"]:
            continue
        rest = _remainder(path, rule["path"])
        if rest is None:
            continue
        target = urlparse(rule["target"])
        flow.request.scheme = target.scheme
        flow.request.host = target.hostname or host
        flow.request.port = target.port or DEFAULT_PORTS[target.scheme]
        flow.request.path = target.path.rstrip("/") + rest + (f"?{query}" if query else "")
        print(f"digital-twin-universe: {host}{path} -> {flow.request.url}", flush=True)
        return
    if ALLOW and host not in ALLOW:
        print(f"digital-twin-universe: refused {host}{path}: not in x-dtu.allow", flush=True)
        flow.response = http.Response.make(
            403,
            f"digital-twin-universe refused this request: {host} is not in x-dtu.allow.\n".encode(),
            {"Content-Type": "text/plain"},
        )
