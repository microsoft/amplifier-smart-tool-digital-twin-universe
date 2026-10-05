# CLI Reference

The CLI is a thin wrapper over the [library](01-library.md): one command per capability, taking the same arguments under the same names, and doing nothing the library does not. 
What each argument means and what a capability returns or raises is documented there. This page covers only what the CLI adds: the invocation shape, and what reaches stdout, stderr, and the exit code.

Results go to stdout and diagnostics to stderr. A failure the library can name prints its message to stderr and exits 1; a bad invocation exits 2.

## Help

```
digital-twin-universe -h                 terse summary for a person: the commands, a line each
digital-twin-universe --help             the tool's skill, written for an agent driving it
digital-twin-universe <command> -h       terse summary of one command: its options
digital-twin-universe <command> --help   the command's skill: when to use it, arguments, result, failures
digital-twin-universe -V, --version      the installed version
```

`--help` prints what `lib.skill()` returns on the tool and `lib.skill(<command>)` on a command; the CLI adds nothing of its own.

## digital-twin-universe manifest

```bash
digital-twin-universe manifest
```

`lib.load_manifest()`, printed as JSON.

## digital-twin-universe check

```bash
digital-twin-universe check
```

`lib.check()`, printed as JSON. Exits 0 when the report says `ok`, 1 when a prerequisite is missing, so `digital-twin-universe check && digital-twin-universe launch ...` does the right thing. The report is on stdout either way.

## digital-twin-universe install

```bash
digital-twin-universe install [--yes] [--accept-license] [--agent-provider copilot|amplifier-agent|codex] [--model MODEL]
                 [--reasoning-effort low] [--timeout-seconds 1200]
```

`lib.install()`, with `--yes` as `apply=True`, printed as JSON. One progress line per step goes to stderr while `--yes` runs, so a person watching a long download knows it is alive. Exits 0 on `ready` or `installed` and 1 otherwise, so `digital-twin-universe install --yes && digital-twin-universe launch ...` behaves like `check`.

## digital-twin-universe create-profile

```bash
digital-twin-universe create-profile --description "a FastAPI app on port 8000 using Postgres" [--project .] [--name web-app]
                        [--no-verify] [--keep] [--overwrite] [--max-attempts 3]
                        [--agent-provider copilot|amplifier-agent|codex] [--model MODEL]
                        [--reasoning-effort low] [--timeout-seconds 1800]
```

`lib.create_profile()`, with `--no-verify` as `verify=False`, printed as JSON. One progress line per phase goes to stderr (authoring, validating, launching, checking, destroying, cleaning up); Compose's own progress goes there too through `launch`. The agent's own launches run inside its shell and are not echoed, so the authoring phase is simply long. Exits 0 on `created` or `validated`, 1 on `failed`, and 2 on `--keep` with `--no-verify`.

## digital-twin-universe validate-profile

```bash
digital-twin-universe validate-profile --profile <name-or-path>
```

`lib.validate_profile()`, printed as JSON. Exits 0 when the report has no errors and 1 when it has any, so `digital-twin-universe validate-profile --profile p && digital-twin-universe launch --profile p` does the right thing. Warnings never change the exit code. The report is on stdout either way.

## digital-twin-universe launch

```bash
digital-twin-universe launch --profile <name-or-path> [--timeout-seconds 600]
```

`lib.launch()`, printed as JSON. Compose's progress goes to stderr while it runs, so the JSON on stdout stays clean. Exits 1 with the cause and remedy on any launch failure; the remedy names the `destroy` command that clears whatever started.

## digital-twin-universe list

```bash
digital-twin-universe list
```

`lib.list_universes()`, printed as a JSON array. An empty machine prints `[]` and exits 0.

## digital-twin-universe status

```bash
digital-twin-universe status --id <id>
```

`lib.status()`, printed as JSON in the same shape `launch` prints.

## digital-twin-universe exec

```bash
digital-twin-universe exec --id <id> --command "<shell command>" [--user <user>] [--workdir <path>] [--timeout-seconds 300]
digital-twin-universe exec --id <id> [--user <user>] [--workdir <path>]
```

With `--command`, `lib.execute()`: prints the `ExecResult` as JSON and exits with the command's own exit code, so `digital-twin-universe exec ... --command "curl -sf ..." && ...` does the right thing. The JSON is on stdout whatever the code.

Without `--command`, `lib.shell()`: attaches an interactive shell to the terminal, prints nothing, and exits with the shell's exit code. Without a terminal it exits 1 with `no-tty`.

## digital-twin-universe file-push and file-pull

```bash
digital-twin-universe file-push --id <id> --source <host path> --destination <twin path>
digital-twin-universe file-pull --id <id> --source <twin path> --destination <host path>
```

`lib.push_files()` and `lib.pull_files()`, printed as JSON. Exits 1 with `source-not-found` when the source is missing on its side, and `transfer-failed` when `docker cp` refuses the paths.

## digital-twin-universe destroy

```bash
digital-twin-universe destroy --id <id>
```

`lib.destroy()`, printed as JSON.

## digital-twin-universe dashboard

```bash
digital-twin-universe dashboard [--port <n>] [--host 127.0.0.1]
```

`lib.serve_dashboard()`, printed as JSON: the `url` to open, the `mcp_url` of its MCP server, and who can reach them. The command then keeps serving until Ctrl+C, which exits 0. Exits 1 with `port-in-use` when `--port` names a held port, and `dashboard-not-compiled` when the view is missing; without `--port` a free one is chosen.

## digital-twin-universe mcp

```bash
digital-twin-universe mcp
```

`digital_twin_universe.adapters.mcp.create_server()` over stdio, for MCP clients that launch the server themselves. stdin and stdout carry only MCP messages until the client disconnects. Exits 1 with `dashboard-not-compiled` when the view is missing.

## Adding a command

Each command gets a section here: the invocation shape with its options and defaults, which library function it calls, and what it prints and exits with. Argument meanings belong in the library reference, not here.
