`mcp` runs the tool's MCP server over stdio: four tools that list, measure, and destroy the
universes on this machine, and the dashboard as an MCP App view that hosts supporting MCP Apps
render in the chat. It reads and writes MCP messages on stdin and stdout until the client
disconnects.

Use it to give an MCP client, such as VS Code, the universe tools, or to show a person their
universes inside that client. Configure the client to launch the command
`digital-twin-universe` with the single argument `mcp`; the client owns the process. Do not run
it yourself in a terminal: it waits for an MCP client on stdin. For a client that connects by
URL, use the `mcp_url` that `digital-twin-universe dashboard` prints, which serves the same
server over streamable HTTP. Launching, `exec`, and file transfer are not exposed; use the CLI
or library for those.

```bash
# what an MCP client runs; stdin and stdout are the MCP channel
digital-twin-universe mcp

# check it starts: with stdin closed it exits 0 at once, or 1 with the cause
digital-twin-universe mcp < /dev/null
```

The library has no `mcp` function. The command calls `run_stdio()` in
`digital_twin_universe.adapters.mcp`, which builds the server with `create_server()` and runs
it on stdio:

```python
from digital_twin_universe.adapters.mcp import create_server

server = create_server()
server.run("stdio")  # or server.streamable_http_app() to mount it in an ASGI app
```

## Arguments

None. The command takes no options beyond `-h` and `--help`.

- `view`, library only: the compiled view `create_server` serves as
  `ui://digital-twin-universe/dashboard`. Defaults to `adapters/static/mcp_app.html` in the
  package, read once when the server is created.

## Result

Nothing but MCP messages on stdout. The MCP SDK logs to stderr, including a line per failed
tool call. The process runs until the client closes stdin.

The server is named `Digital Twin Universe`, at the tool's version, and its instructions tell
the model to call `open_dashboard` only when the user wants to see universes. Its tools return
the same JSON as the CLI command each wraps:

- `open_dashboard(id?)`: every universe, as `list` returns them, rendered as the dashboard view
  in hosts that support MCP Apps. `id` opens the view with that universe selected and must
  name an existing universe. Read-only, and visible to the model only.
- `list_universes()`: every universe launched from this machine, oldest first, measured now,
  as `list` returns them; `[]` when there are none. Read-only.
- `universe_status(id)`: one universe measured now, as `status` returns it: `id`, `name`,
  `description`, `profile_path`, `twin_machine`, `state` (`starting`, `running`,
  `degraded`, or `stopped`), `services`, `urls`, `state_path`, and `created_at`. Read-only.
- `destroy_universe(id)`: takes the universe down as `destroy` does and returns `id` and
  `removed`, the containers, networks, and volumes removed. Built images stay. Destructive and
  annotated idempotent, though a second call fails with `universe-not-found`.

The one resource is `ui://digital-twin-universe/dashboard`, `text/html;profile=mcp-app`: the
dashboard view, which lists every universe with its state and URLs and offers a destroy
button, calling the tools above. It may write to the clipboard. Closing it destroys nothing.

## Failures

Exits 0 when the client disconnects. Exits 2 on a bad invocation, such as passing any option.

Before serving, the command prints `message` and `remedy` to stderr and exits 1 when the
library raises `DigitalTwinUniverseError` with `code`:

- `dashboard-not-compiled`: the view `adapters/static/mcp_app.html` is missing from the
  package. It ships compiled, so this means a broken install or a source checkout whose build
  was removed; run `uv run build-dashboard.py` from the repository root.

Once serving, a failing tool call is a tool error, not an exit. Its text is
`Error executing tool <name>: ` followed by the message and remedy of the
`DigitalTwinUniverseError` the library raised:

- `universe-not-found`: from `open_dashboard` with `id`, `universe_status`, or
  `destroy_universe`, when no universe has that id. The remedy names the ids that exist, or
  the universe id when given a container or image name that starts with one.
- `docker-unavailable`: Docker is not usable from this process. Run
  `digital-twin-universe check` for the missing prerequisite.
