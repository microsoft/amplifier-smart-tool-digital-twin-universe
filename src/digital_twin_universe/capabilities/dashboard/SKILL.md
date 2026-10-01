`dashboard` serves a web page showing every universe launched from this machine, with its
state, URLs, and a destroy button, prints where it is as JSON, and keeps serving until
interrupted. The page is a minimal MCP Apps host rendering the view of the same MCP server
`digital-twin-universe mcp` runs, and that server is also served at `/mcp` over streamable
HTTP for any MCP client that connects by URL.

Use it when a person wants to see and tidy up their universes without remembering ids, or
when an MCP client needs the universe tools over HTTP rather than stdio. It adds no capability
of its own: an agent reading universes should call `list` or `status`, which return at once
and leave nothing running. For a client that launches its server itself, use `mcp`.

```bash
# loopback only, on a free port; prints the URLs, then blocks until Ctrl+C
digital-twin-universe dashboard

# a fixed port, so a bookmark or an MCP client config keeps working
digital-twin-universe dashboard --port 5199

# reachable from the local network
digital-twin-universe dashboard --host 0.0.0.0 --port 5199

# from a script: start it in the background, read the URLs, stop it when done
digital-twin-universe dashboard > dashboard.json &
kill $!
```

```python
import threading

from digital_twin_universe.lib import serve_dashboard

served = serve_dashboard(port=5199)
served.url, served.mcp_url, served.reachable
threading.Event().wait()  # the server runs on daemon threads and stops when the process exits
```

`serve_dashboard` returns as soon as both sockets are listening, so the URL answers at once.
The page's view runs on a second, free port on the same host, its own origin as the MCP Apps
spec requires; it is not reported and needs nothing from you.

## Arguments

- `--port N`: the port the page and `/mcp` are served on. A free one is chosen when omitted.
- `--host HOST`: the interface to bind. Defaults to `127.0.0.1`, which keeps the dashboard off
  the network. `0.0.0.0` exposes it, its destroy button, and `/mcp` to the local network.
  Requests to `/mcp` must name a loopback address or the bound host, except when bound to
  `0.0.0.0` or `::`.

## Result

`Dashboard` JSON on stdout, then nothing more while it serves. Uvicorn writes only warnings and
errors to stderr.

- `url`: what to open in a browser, `http://<host>:<port>`. The host is `127.0.0.1` when
  `--host` is `127.0.0.1`, `localhost`, or `0.0.0.0`, and `--host` itself otherwise.
- `mcp_url`: `<url>/mcp`, the MCP server over streamable HTTP: the tools `open_dashboard`,
  `list_universes`, `universe_status`, and `destroy_universe` and the view
  `ui://digital-twin-universe/dashboard`. `digital-twin-universe mcp --help` describes them.
- `host`: the interface that was bound, as given.
- `port`: the port that was bound, chosen or given.
- `reachable`: `only this machine` for `127.0.0.1` or `localhost`, otherwise
  `the local network (bound to <host>)`.

The process keeps serving until Ctrl+C or a signal ends it. The page reads the universes live
through the MCP server, so it never needs restarting for a new universe.

## Failures

Exits 0 when interrupted with Ctrl+C. Exits 2 on a bad invocation, such as a `--port` that is
not a number.

Anything else prints `message` and `remedy` to stderr and exits 1 before anything is printed on
stdout. The library raises `DigitalTwinUniverseError` with `code`:

- `port-in-use`: binding failed, most often because `--port` is held by another process. The
  same code covers a `--host` that is not an address of this machine. Omit `--port` to have a
  free one chosen.
- `dashboard-not-compiled`: the MCP App view `adapters/static/mcp_app.html` is missing from
  the package. It ships compiled, so this means a broken install or a source checkout whose
  build was removed; run `uv run build-dashboard.py` from the repository root.

A missing host page under `capabilities/dashboard/static/` is not raised: the page answers 503
with the same build command. Docker problems never stop the dashboard from starting; they fail
the MCP call the page makes, as a tool error carrying the message and remedy.
