"""The MCP adapter in process and over stdio: tools, the view resource, errors, and a live destroy."""

from contextlib import suppress
from importlib.metadata import version
from pathlib import Path
import re
import shutil

from mcp import Client, StdioServerParameters
from mcp.types import TextContent
import pytest

from digital_twin_universe import lib
from digital_twin_universe.adapters import mcp as mcp_adapter
from digital_twin_universe.schemas import DigitalTwinUniverseError

TOOLS = {"open_dashboard", "list_universes", "universe_status", "destroy_universe"}


async def test_tools_are_listed_with_their_input_schemas(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == TOOLS
    assert tools["list_universes"].input_schema["properties"] == {}
    assert tools["universe_status"].input_schema["required"] == ["id"]
    assert tools["destroy_universe"].input_schema["required"] == ["id"]
    assert "required" not in tools["open_dashboard"].input_schema
    assert "id" in tools["open_dashboard"].input_schema["properties"]
    for tool in tools.values():
        assert tool.output_schema is not None


async def test_only_open_dashboard_renders_the_view_and_only_for_the_model(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert tools["open_dashboard"].meta == {"ui": {"resourceUri": mcp_adapter.VIEW_URI, "visibility": ["model"]}}
    for name in TOOLS - {"open_dashboard"}:
        assert not (tools[name].meta or {}).get("ui")


async def test_annotations_mark_reads_and_the_destructive_tool(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in ("open_dashboard", "list_universes", "universe_status"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
    destroy = tools["destroy_universe"].annotations
    assert destroy is not None
    assert (destroy.read_only_hint, destroy.destructive_hint, destroy.idempotent_hint) == (None, True, True)


async def test_the_view_is_one_self_contained_html_document_allowed_the_clipboard(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        result = await client.read_resource(mcp_adapter.VIEW_URI)

    [content] = result.contents
    assert content.mime_type == "text/html;profile=mcp-app"
    assert content.meta == {"ui": {"permissions": {"clipboardWrite": {}}}}
    html = getattr(content, "text", "")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<script" in html
    assert not re.search(r"""\ssrc=["']?(https?:)?//""", html)


async def test_an_empty_machine_lists_nothing(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        listed = await client.call_tool("list_universes", {})

    assert not listed.is_error
    assert listed.structured_content == {"result": []}


async def test_open_dashboard_returns_the_list(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        result = await client.call_tool("open_dashboard", {})

    assert not result.is_error
    assert result.structured_content == {"result": []}


async def test_the_server_reports_the_package_version(state_root: Path) -> None:
    async with Client(mcp_adapter.create_server()) as client:
        server_info = client.server_info

    assert server_info is not None
    assert server_info.version == version("digital-twin-universe")


async def test_an_unknown_id_is_a_tool_error_carrying_the_message_and_remedy(state_root: Path) -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.status("dtu-nope-0000")
    expected = f"{raised.value.message} {raised.value.remedy}"

    async with Client(mcp_adapter.create_server()) as client:
        for name in ("universe_status", "destroy_universe", "open_dashboard"):
            result = await client.call_tool(name, {"id": "dtu-nope-0000"})

            assert result.is_error
            [content] = result.content
            assert isinstance(content, TextContent)
            assert content.text.endswith(expected)


def test_an_uncompiled_view_says_how_to_build_it(tmp_path: Path) -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        mcp_adapter.create_server(tmp_path / "missing.html")

    assert raised.value.code == "dashboard-not-compiled"
    assert mcp_adapter.BUILD_COMMAND in raised.value.remedy


async def test_the_cli_serves_the_tools_over_stdio(tmp_path: Path) -> None:
    command = shutil.which("digital-twin-universe")
    assert command is not None
    # HOME points the state root at an empty directory, so the subprocess never measures real universes.
    server = StdioServerParameters(command=command, args=["mcp"], env={"HOME": str(tmp_path)})

    async with Client(server) as client:
        tools = await client.list_tools()
        listed = await client.call_tool("list_universes", {})

    assert {tool.name for tool in tools.tools} == TOOLS
    assert listed.structured_content == {"result": []}


@pytest.mark.needs_docker
@pytest.mark.live
async def test_a_launched_universe_is_listed_and_destroyed_through_mcp(state_root: Path) -> None:
    universe = lib.launch("hello", timeout_seconds=120)
    try:
        async with Client(mcp_adapter.create_server()) as client:
            listed = await client.call_tool("list_universes", {})
            destroyed = await client.call_tool("destroy_universe", {"id": universe.id})
    finally:
        # Already gone when `destroy_universe` worked; this only cleans up after a failure before it.
        with suppress(DigitalTwinUniverseError):
            lib.destroy(universe.id)

    assert listed.structured_content is not None
    assert [entry["id"] for entry in listed.structured_content["result"]] == [universe.id]
    assert not destroyed.is_error
    assert destroyed.structured_content is not None
    assert destroyed.structured_content["id"] == universe.id
    assert lib.list_universes() == []
