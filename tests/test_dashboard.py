"""The dashboard apart from Docker: the MCP server at /mcp, the host page, its sandbox origin, and serving."""

from contextlib import suppress
from pathlib import Path
import re
import socket

from fastapi.testclient import TestClient
import httpx
from mcp import Client
import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.dashboard import server
from digital_twin_universe.schemas import Dashboard, DigitalTwinUniverseError

TOOLS = {"open_dashboard", "list_universes", "universe_status", "destroy_universe"}


def _static(tmp_path: Path) -> Path:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text('<meta content="{{sandbox_port}}">', encoding="utf-8")
    (static / "sandbox.html").write_text('<meta content="{{host_port}}">', encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return static


def _sandbox_url(dashboard: Dashboard) -> str:
    page = httpx.get(dashboard.url).text
    match = re.search(r'name="digital-twin-universe-sandbox-port"\s+content="(\d+)"', page)
    assert match, page
    return f"http://127.0.0.1:{match[1]}"


async def test_serve_picks_a_free_port_and_serves_the_mcp_server_on_it(state_root: Path) -> None:
    dashboard = lib.serve_dashboard()

    assert dashboard.url == f"http://127.0.0.1:{dashboard.port}"
    assert dashboard.mcp_url == f"{dashboard.url}/mcp"
    assert dashboard.reachable == "only this machine"
    # The server runs in this process, so the state_root fixture's empty state is what it lists.
    async with Client(dashboard.mcp_url) as client:
        tools = await client.list_tools()
        listed = await client.call_tool("list_universes", {})

    assert {tool.name for tool in tools.tools} == TOOLS
    assert listed.structured_content == {"result": []}


def test_the_host_page_names_its_sandbox_on_another_origin_that_names_it_back(state_root: Path) -> None:
    dashboard = lib.serve_dashboard()
    sandbox = _sandbox_url(dashboard)

    assert sandbox != dashboard.url
    response = httpx.get(f"{sandbox}/sandbox.html")
    assert response.status_code == 200
    assert f'name="digital-twin-universe-host-port" content="{dashboard.port}"' in response.text
    assert response.headers["content-security-policy"] == server.VIEW_CSP
    assert "font-src 'self' data:" in server.VIEW_CSP
    script = re.search(r'src="(/assets/[^"]+\.js)"', response.text)
    assert script, response.text
    assert httpx.get(f"{sandbox}{script[1]}").status_code == 200
    assert httpx.get(f"{sandbox}/").status_code == 404
    assert "{{" not in httpx.get(f"{dashboard.url}/sandbox.html").text


def test_mcp_answers_browser_hosts_on_this_machine_and_refuses_other_origins(state_root: Path) -> None:
    dashboard = lib.serve_dashboard()
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    headers = {"accept": "application/json, text/event-stream"}

    local = httpx.post(dashboard.mcp_url, json=initialize, headers={**headers, "origin": "http://localhost:8080"})
    foreign = httpx.post(dashboard.mcp_url, json=initialize, headers={**headers, "origin": "http://evil.example"})

    assert local.status_code == 200
    assert local.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert "mcp-session-id" in local.headers["access-control-expose-headers"]
    assert foreign.status_code == 403
    assert "access-control-allow-origin" not in foreign.headers


def test_pages_serve_the_host_page_and_assets_serve_themselves(tmp_path: Path) -> None:
    client = TestClient(server.create_app(4321, static_dir=_static(tmp_path)))

    assert client.get("/").text == '<meta content="4321">'
    assert client.get("/some/deep/route").text == '<meta content="4321">'
    assert client.get("/index.html").text == '<meta content="4321">'
    assert client.get("/sandbox.html").text == '<meta content="4321">'
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/assets/../../secret").text == '<meta content="4321">'


def test_the_sandbox_serves_only_its_page_and_the_assets(tmp_path: Path) -> None:
    client = TestClient(server.create_sandbox_app(1234, _static(tmp_path)))

    assert client.get("/sandbox.html").text == '<meta content="1234">'
    assert client.get("/assets/app.js").text == "console.log(1)"
    for path in ("/", "/index.html", "/assets/..%2F..%2Fsecret"):
        assert client.get(path).status_code == 404


def test_neither_app_publishes_api_docs(tmp_path: Path) -> None:
    static = _static(tmp_path)
    host = TestClient(server.create_app(4321, static_dir=static))
    sandbox = TestClient(server.create_sandbox_app(1234, static))

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert host.get(path).text == '<meta content="4321">'
        assert sandbox.get(path).status_code == 404


def test_an_uncompiled_dashboard_says_how_to_build_it(tmp_path: Path) -> None:
    host = TestClient(server.create_app(1, static_dir=tmp_path)).get("/")
    sandbox = TestClient(server.create_sandbox_app(1, tmp_path)).get("/sandbox.html")

    for response in (host, sandbox):
        assert response.status_code == 503
        assert server.BUILD_COMMAND in response.text


def test_serve_names_a_held_port() -> None:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]

        with pytest.raises(DigitalTwinUniverseError) as raised:
            lib.serve_dashboard(port)

    assert raised.value.code == "port-in-use"
    assert str(port) in raised.value.message


def test_the_compiled_dashboard_ships_in_the_package() -> None:
    for page in ("index.html", "sandbox.html"):
        assert (server.STATIC_DIR / page).is_file(), f"Run `{server.BUILD_COMMAND}` and commit the result."


@pytest.mark.needs_docker
@pytest.mark.live
async def test_mcp_over_http_lists_and_destroys_a_real_universe(tmp_path: Path, state_root: Path) -> None:
    (tmp_path / "compose.yaml").write_text(
        "name: dash\nx-dtu:\n  twin_machine: box\nservices:\n  box:\n    image: alpine:3.20\n    command: sleep infinity\n"
        "    healthcheck:\n      test: [CMD, 'true']\n      interval: 1s\n",
        encoding="utf-8",
    )
    universe = lib.launch(tmp_path / "compose.yaml", timeout_seconds=120)
    try:
        async with Client(lib.serve_dashboard().mcp_url) as client:
            listed = await client.call_tool("list_universes", {})
            destroyed = await client.call_tool("destroy_universe", {"id": universe.id})
    finally:
        # Already gone when `destroy_universe` worked; this only cleans up after a failure before it.
        with suppress(DigitalTwinUniverseError):
            lib.destroy(universe.id)

    assert listed.structured_content is not None
    [entry] = listed.structured_content["result"]
    assert (entry["id"], entry["state"]) == (universe.id, "running")
    assert destroyed.structured_content is not None
    assert f"{universe.id}-box-1" in destroyed.structured_content["removed"]
    assert lib.list_universes() == []
