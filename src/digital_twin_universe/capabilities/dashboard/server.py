"""Dashboard: a minimal MCP Apps host page, its sandbox proxy, and the MCP server it renders the view from."""

import logging
from pathlib import Path
import socket
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from mcp.server.transport_security import TransportSecuritySettings
import uvicorn

from digital_twin_universe.adapters import mcp as mcp_adapter
from digital_twin_universe.schemas import Dashboard, DigitalTwinUniverseError

STATIC_DIR = Path(__file__).parent / "static"
BUILD_COMMAND = "uv run build-dashboard.py"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
EVERY_INTERFACE = ("0.0.0.0", "::")
# Browser MCP Apps hosts on this machine, such as the ext-apps basic-host, call /mcp from their own port.
LOCAL_ORIGINS = r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?"
# The spec's restrictive default for a view that declares no `ui.csp`, plus `data:` fonts so the view's inlined
# Outfit renders. The view is written into a frame of the sandbox page, so it inherits this policy.
VIEW_CSP = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self' data:; media-src 'self' data:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'self'"
)


def create_app(sandbox_port: int, host: str = "127.0.0.1", static_dir: Path = STATIC_DIR) -> FastAPI:
    """The host page, its assets, and the MCP server at `/mcp`.

    Args:
        sandbox_port: Where `create_sandbox_app` is served, on the same host; the page loads the view from there.
        host: The interface the dashboard is bound to, which requests to `/mcp` must name.
        static_dir: The compiled host page and its assets.
    """
    server = mcp_adapter.create_server()
    mcp_app = server.streamable_http_app(transport_security=_transport_security(host))
    # FastAPI runs only its own lifespan, never a mounted app's, and the session manager must be running to serve.
    app = FastAPI(
        title="Digital Twin Universe Dashboard",
        lifespan=lambda _: server.session_manager.run(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.router.routes.extend(mcp_app.routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOCAL_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )

    @app.get("/{page_path:path}", include_in_schema=False)
    def page(page_path: str) -> Response:
        index = static_dir / "index.html"
        if not index.is_file():
            return _not_compiled()
        candidate = (static_dir / page_path).resolve()
        if candidate.is_relative_to(static_dir.resolve()) and candidate.is_file() and candidate.suffix != ".html":
            return FileResponse(candidate)
        return HTMLResponse(index.read_text(encoding="utf-8").replace("{{sandbox_port}}", str(sandbox_port)))

    return app


def create_sandbox_app(host_port: int, static_dir: Path = STATIC_DIR) -> FastAPI:
    """The sandbox proxy page, served on its own origin as the MCP Apps spec requires, with the view's CSP.

    Args:
        host_port: Where `create_app` is served, on the same host; the only page the sandbox runs inside.
        static_dir: The compiled sandbox page and its assets.
    """
    app = FastAPI(title="Digital Twin Universe Dashboard Sandbox", openapi_url=None)

    @app.get("/sandbox.html")
    def sandbox() -> Response:
        page = static_dir / "sandbox.html"
        if not page.is_file():
            return _not_compiled()
        return HTMLResponse(
            page.read_text(encoding="utf-8").replace("{{host_port}}", str(host_port)),
            headers={"Content-Security-Policy": VIEW_CSP, "Cache-Control": "no-store"},
        )

    @app.get("/assets/{asset}")
    def asset(asset: str) -> Response:
        assets = (static_dir / "assets").resolve()
        candidate = (assets / asset).resolve()
        if candidate.is_relative_to(assets) and candidate.is_file():
            return FileResponse(candidate)
        return PlainTextResponse("Not found.", status_code=404)

    return app


def serve_dashboard(port: int | None = None, host: str = "127.0.0.1") -> Dashboard:
    """Serve the dashboard from daemon threads, so it lives until the process exits, and say where it is."""
    # Bound and listening before this returns, so the URL it reports answers at once (connections queue until the
    # server thread accepts) and a held port is a named failure rather than uvicorn's own exit.
    sock = _bind(host, port)
    try:
        sandbox_sock = _bind(host, None)
    except DigitalTwinUniverseError:
        sock.close()
        raise
    bound_port = int(sock.getsockname()[1])
    sandbox_port = int(sandbox_sock.getsockname()[1])
    try:
        apps = (create_app(sandbox_port, host), create_sandbox_app(bound_port))
    except DigitalTwinUniverseError:
        sock.close()
        sandbox_sock.close()
        raise
    # Uvicorn logs at warning; the MCP SDK's session manager would otherwise announce itself at info on stderr.
    logging.getLogger("mcp.server.streamable_http_manager").setLevel(logging.WARNING)
    for app, listening in zip(apps, (sock, sandbox_sock), strict=True):
        config = uvicorn.Config(app, host=host, port=int(listening.getsockname()[1]), log_level="warning")
        threading.Thread(target=uvicorn.Server(config).run, kwargs={"sockets": [listening]}, daemon=True).start()
    display_host = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    reachable = "only this machine" if host in ("127.0.0.1", "localhost") else f"the local network (bound to {host})"
    url = f"http://{display_host}:{bound_port}"
    return Dashboard(url=url, mcp_url=f"{url}/mcp", host=host, port=bound_port, reachable=reachable)


def _bind(host: str, port: int | None) -> socket.socket:
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port or 0))
    except OSError as error:
        sock.close()
        raise DigitalTwinUniverseError(
            "port-in-use",
            f"The dashboard could not bind {host}:{port}: {error.strerror or error}.",
            "Omit --port to have a free one chosen, or pass a port nothing else holds.",
        ) from error
    sock.listen()
    return sock


def _transport_security(host: str) -> TransportSecuritySettings:
    """Which Host and Origin headers `/mcp` accepts, so a DNS-rebound page cannot drive it."""
    if host in EVERY_INTERFACE:
        # Peers name whichever address of this machine they reached, so there is no one host to pin to, and the port is
        # already open to the network, which is what rebinding would otherwise gain an attacker.
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = ["127.0.0.1", "localhost", "[::1]"]
    if host not in LOOPBACK_HOSTS:
        hosts.append(f"[{host}]" if ":" in host else host)
    return TransportSecuritySettings(
        allowed_hosts=[f"{name}:*" for name in hosts], allowed_origins=[f"http://{name}:*" for name in hosts]
    )


def _not_compiled() -> Response:
    return PlainTextResponse(
        f"The dashboard is not compiled. Run `{BUILD_COMMAND}` from the repository root.", status_code=503
    )
