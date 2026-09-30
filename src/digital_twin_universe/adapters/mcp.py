"""MCP adapter: the universe tools and the dashboard view as an MCP server, a thin surface over the library."""

from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.apps import Apps, ResourcePermissions
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from digital_twin_universe.capabilities.universe import destroy as destroy_module
from digital_twin_universe.capabilities.universe import status as status_module
from digital_twin_universe.core.skill import DISTRIBUTION
from digital_twin_universe.schemas import Destroyed, DigitalTwinUniverseError, Universe

VIEW_URI = "ui://digital-twin-universe/dashboard"
VIEW_PATH = Path(__file__).parent / "static" / "mcp_app.html"
BUILD_COMMAND = "uv run build-dashboard.py"
INSTRUCTIONS = """\
Digital Twin Universe runs universes: isolated, realistic environments on Docker Compose, each launched from a profile and \
identified by an id such as `dtu-hello-1a2b`.

Call `open_dashboard` only when the user wants to see their universes; it opens an interactive view that keeps \
itself up to date. Otherwise call `list_universes` or `universe_status`, which return data and open nothing.
Closing the view does not destroy anything; only `destroy_universe` does."""


def create_server(view: Path = VIEW_PATH) -> MCPServer:
    """An MCP server exposing universe list, status, and destroy, plus the dashboard as an MCP App view.

    Args:
        view: The compiled single-file view served at `ui://digital-twin-universe/dashboard`.
    """
    if not view.is_file():
        raise DigitalTwinUniverseError(
            "dashboard-not-compiled",
            f"The dashboard view is not compiled: {view} does not exist.",
            f"Run `{BUILD_COMMAND}` from the repository root.",
        )
    apps = Apps()
    apps.add_html_resource(
        VIEW_URI,
        view.read_text(encoding="utf-8"),
        name="Digital Twin Universe dashboard",
        permissions=ResourcePermissions(clipboard_write={}),
    )

    @apps.tool(resource_uri=VIEW_URI, visibility=["model"], annotations=ToolAnnotations(read_only_hint=True))
    def open_dashboard(id: str | None = None) -> list[Universe]:
        """Show the user an interactive dashboard of every universe on this machine, and return them.

        Pass `id` to open with that universe selected. Only for when the user wants to see universes; use
        `list_universes` to read them.
        """
        with _as_tool_error():
            if id is not None:
                status_module.status(id)
            return status_module.list_universes()

    server = MCPServer(
        "Digital Twin Universe", version=version(DISTRIBUTION), instructions=INSTRUCTIONS, extensions=[apps]
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_universes() -> list[Universe]:
        """List every universe launched from this machine, oldest first, measured now."""
        with _as_tool_error():
            return status_module.list_universes()

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def universe_status(id: str) -> Universe:
        """Measure one universe now: its state, services, and the URLs the host can open."""
        with _as_tool_error():
            return status_module.status(id)

    @server.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
    def destroy_universe(id: str) -> Destroyed:
        """Remove a universe: its containers, networks, volumes, and state. Built images stay."""
        with _as_tool_error():
            return destroy_module.destroy(id)

    return server


def run_stdio() -> None:
    """Serve the MCP server over stdin and stdout until the client disconnects."""
    create_server().run("stdio")


@contextmanager
def _as_tool_error() -> Iterator[None]:
    # Anything else the SDK reports only as "Error executing tool", hiding the message and remedy from the model.
    try:
        yield
    except DigitalTwinUniverseError as error:
        raise ToolError(f"{error.message} {error.remedy}") from error
