# Library Reference

Every capability of Digital Twin Universe is reachable from `digital_twin_universe.lib`.
All other surfaces, including the CLI, are thin wrappers over the library and add no capability of their own.

Each capability has a section: what it does, its signature, and what it raises.
Result shapes are the classes in `digital_twin_universe/schemas.py`; this page names them and explains only what a field name cannot.
The sections `Failures`, `Profiles`, and `Universe` define what the capabilities share.

## Docker access

Universe operations and host checks go through [python-on-whales](https://github.com/gabrieldemarmiesse/python-on-whales), which drives the `docker` CLI and its Compose plugin from Python with typed results.
It is the only maintained Python route to `docker compose`; the official `docker` SDK speaks the Engine API and has no Compose support, and Compose is what a universe is.
The trade is that the Docker CLI must be on `PATH`, which Docker Desktop and Docker Engine both provide and `check` confirms.
`execute` and `shell` take the `docker compose ... exec` command python-on-whales builds and run it through `subprocess`, since that is where a timeout, the exit code, and the caller's terminal are.
File transfers use `docker cp` rather than `docker compose cp`, which fails on whole directories.
`install` is the exception: it runs documented host-shell commands, not Docker.

## Failures

Every failure the library can name is one exception:

```python
class DigitalTwinUniverseError(Exception):
    code: str  # stable slug to branch on, such as "port-in-use"
    message: str  # what went wrong, with the specifics: the service, the port, the variable
    remedy: str  # what to do about it
```

`str(error)` is the message followed by the remedy, which is what the CLI prints. Each capability lists the codes it raises. Anything else that escapes is a bug.

A result that carries a verdict (`HostReport.ok`, `ProfileReport.ok`, `InstallReport.outcome`) is never raised as an error when the verdict is negative. The verdict is the answer.

## Check

Whether this host can run a universe: the Docker CLI on `PATH`, a daemon answering behind it, and the Compose plugin.
Deterministic; needs no model provider.

```python
def check() -> HostReport
```

`HostReport.prerequisites` is in probe order and stops at the first one missing; each missing one carries a `remedy`, which names `digital-twin-universe install`.

## Install

Get Docker working on this host. Model-backed, except that when `check()` already passes it returns `ready` without loading the intelligence or touching the network.

```python
def install(
    apply: bool = False,            # run the unattended steps; otherwise only plan
    accept_license: bool = False,   # allow --accept-license in Docker Desktop's installer
    agent_provider: AgentProvider | None = None,  # copilot, amplifier-agent, codex, or claude; the first installed when None
    model: str | None = None,       # the agent provider's default in DEFAULT_INTELLIGENCE_MODELS when None
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1200,    # the whole run; Desktop downloads are large and daemon start is polled
    intelligence: Intelligence | None = None,
) -> InstallReport
```

Host facts are gathered deterministically, the official Docker pages for this platform and distribution are fetched as Markdown at run time, and the agent, given no tools, turns both into one `InstallPlan`: ordered steps, each with its commands, the page it came from, and whether it can run unattended.
The tool checks the plan before showing it: every step cites a supplied page, `--accept-license` appears only with `accept_license`, and no unattended step uses `sudo` without `-n`, a convenience script, or anything that needs a new login or a restart.

Without `apply`, the plan is the result and is saved at `~/.digital-twin-universe/install/plan.json` with a hash of the facts and the agent provider it was made through. `apply=True` reuses it while the facts still match, so what runs is what was shown and the two calls cost one model call. It then runs the unattended steps in order with stdin closed, stops at the first manual step, and on a failing step resumes the same agent session once for replacement steps. A plan made through another agent provider is still reused, but its session is not, so a failing step ends the run without a repair round.
When `check()` passes afterwards, it launches the shipped `hello` example, runs a command in it, and destroys it, and only then reports `installed`.

```
ready            Docker was already usable; nothing was done
planned          no apply; the steps are the plan
installed        apply; check() passes and a universe ran
action-required  apply; the unattended steps ran, a manual one remains
failed           apply; a step failed after the repair round, or the universe did not run
```

Every step in `InstallReport.steps` keeps its status and, when it failed, the last lines of its output in `reason`. `next` is exactly one instruction for the person. `notes` carries what the plan wants said: license terms, deviations from the docs such as `-y`, the docker group's privileges.

Raises `docs-unreachable` (with the pages to read by hand), `plan-rejected`, `install-timeout` (with the report so far), `no-agent-provider` and `agent-provider-not-installed` (with the command that installs one), and the intelligence preflight codes: `gh-missing` and `gh-not-signed-in` for `copilot`, `model-invalid` and `amplifier-agent-unavailable` for `amplifier-agent`, `codex-unavailable` and `codex-not-signed-in` for `codex`, `claude-unavailable` and `claude-not-signed-in` for `claude`.

## Create profile

From "I want a universe for X" to a profile at `.agents/digital-twin-universe/<name>/` that has been launched and exercised, not just written. Model-backed; needs Docker.

```python
def create_profile(
    description: str,                        # what the universe is for, in the user's words
    project: Path | None = None,             # the repository to profile; None means the description is everything
    name: str | None = None,                 # profile name; derived from the description when None
    verify: bool = True,                     # agent and tool both launch and run the checks; False stops both at validate-profile
    keep: bool = False,                      # leave the tool's verified universe running and report it; needs verify
    overwrite: bool = False,                 # replace an existing <name>/
    max_attempts: int = 3,                   # submissions the tool will consider; the agent iterates within each
    agent_provider: AgentProvider | None = None,  # copilot, amplifier-agent, codex, or claude; the first installed when None
    model: str | None = None,                # the agent provider's default in DEFAULT_INTELLIGENCE_MODELS when None
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1800,             # the whole run; a launch with builds is minutes
    intelligence: Intelligence | None = None,
) -> CreatedProfile
```

The agent has tools and the tool has the verdict. The agent works in the project root (the git root above `project`, or `project` itself, or the working directory without one) with tools to read, search, and write files and run commands, reading the repository and a local clone of Docker's documentation, and writing only into `<name>.draft/` beside where the profile will land. It must validate, launch, run every check in the twin, and destroy on its own before submitting, and the submission is accepted only when it carries the id of a universe the tool saw appear during the run and a passed result for every check; anything less is sent back once as a correction, then `profile-rejected`. The tool then validates, launches, reruns the same checks through `execute`, and destroys. Only a draft that passes for the tool is renamed to `<name>/`.

After the tool's verdict, one more turn in the same session asks the agent to clean up: destroy anything it launched that is still listed, remove scratch it made outside the draft, and take out of the profile anything that was there only for iteration. When that edits the profile, or the draft's files differ afterwards whatever the agent said, the tool validates and launches once more before promoting.

```
created      the agent launched and checked, the tool launched and checked, cleanup ran; <name>/ exists
validated    verify=False; validate-profile has no errors; <name>/ exists
failed       attempts exhausted, or a variable the profile rightly demands is unset; <name>.draft/ holds the last attempt
```

`CreatedProfile.checks` is the tool's own run, not the agent's. `env-missing` is never sent back to the agent as a defect: the profile is right to demand the variable, so the run ends `failed` with `next` saying what to export. `keep=True` leaves the tool's verified universe running with its `universe_id` and `urls` in the result and its record pointed at `<name>/`; when cleanup changed the profile, the universe launched from the cleaned profile is the one kept.

The tool destroys only universes it launched itself. Universes the agent launched are the agent's to destroy, in the authoring turns and again in the cleanup turn. A universe whose record's `profile_path` is under the draft can only have come from this run, so any such universe still listed at the end goes into `notes` with its id and `next` names its `destroy` command; the tool does not sweep it, since a sweep by name would take down a universe someone else launched from a profile of the same name.

The documentation the agent reads is kept at `~/.digital-twin-universe/reference/`: a sparse, shallow clone of `docker/docs` holding the Compose file reference, the Compose manual, and the build manual, plus the Dockerfile reference fetched from BuildKit, refreshed when older than seven days. Without `git` or without network the reference is reported absent in `notes` and the run goes on.

Raises `profile-exists`, `project-not-found`, `name-invalid`, `keep-needs-verify`, `docker-unavailable`, `profile-rejected` (the agent's submission was unusable after the correction round), `create-timeout` (with the report so far), and the intelligence preflight codes. A failed verification is an outcome, not an exception.

## Profiles

A profile is a Compose file with an `x-dtu` block; [the profile reference](03-profile.md) is the schema.
Every `profile` argument in this library accepts the same three forms:

- A name, such as `copilot-cli`: the directory `.agents/digital-twin-universe/copilot-cli/`, searched for from the working directory upward to the git root.
- A path to a Compose file.
- A path to a directory, which must hold `compose.yaml` or `docker-compose.yaml`.

When a name is not found in the project, it is looked for under the examples shipped inside the package, so `copilot-cli` launches on a fresh install with nothing copied. The name form never accepts a path separator.
Whatever the form, the result is the entry point: a Compose file.
When nothing is found, the capability raises `profile-not-found`, saying what it looked for and where.

## Examples

`examples/` in the installed package holds profiles that are complete and known to launch. Each is a directory under the examples root, in the same shape as `.agents/digital-twin-universe/<name>/`, and the skill lists its `compose.yaml` under `<skill_resources>` so an agent reading `--help` can open it and find the `Dockerfile` and anything else it names beside it.

```
examples/
  hello/                The smallest universe: an Alpine twin with nothing installed
  copilot-cli/          GitHub Copilot CLI installed as a user would, signed in with the host's GH_TOKEN
  served-repository/    A local git repository cloned in the twin from the URL it stands in for
  web-site/             A static site opened from the host's browser at the URLs `x-dtu.urls` names
```

The examples root is `skill_directory() / "examples"`.

## Validate profile

Whether a profile can be launched, and what would be unrealistic about it if it were.
Deterministic. Needs the Docker CLI for `docker compose config`, which does the Compose-side validation.

```python
def validate_profile(profile: str | Path) -> ProfileReport
```

Checks run in this order and every problem is reported, not only the first:

1. Profile resolution.
2. `docker compose config`, with the host's environment. This is Compose's own validation, and it also resolves interpolation, so an unset `${GH_TOKEN:?message}` surfaces here with its message.
3. `x-dtu` against its schema.
4. The universe invariants (errors) and realism checks (warnings) listed in the profile reference.

`ProfileReport.ok` is "no errors"; warnings do not affect it. Each `Finding` carries a stable `code`, a `location` in the file such as `services.copilot.volumes[0]`, the message, and a remedy.

Raises `profile-not-found` and `docker-unavailable`.

## Universe

A universe is one Compose project. Its `id` is the project's name, `dtu-<profile name>-<4 hex>`, so `docker compose -p <id> logs` reaches the same stack by hand and `list` can tell two launches of one profile apart.

What the tool renders for a universe lives in its state directory, `~/.digital-twin-universe/universes/<id>/`: `dtu.yaml`, the overlay, `overlay/`, the files it refers to, including the certificate authority the gateway minted, `bake.yaml`, the stack as Compose resolved it, which Buildx builds the gateway's builds from, and `universe.json`, the record: id, name, description, profile path, twin, and creation time. Everything else about a universe is in Docker.

A profile that serves nothing and rewrites nothing renders no overlay at all, so `dtu.yaml` is absent and the universe is exactly the profile.
By hand, a universe with the gateway and a build runs as `docker buildx bake --allow=network.host --allow=fs.read=<dir> --load -f bake.yaml`, with one `fs.read` per build context and for `overlay/`, then `docker compose -p <id> -f <profile> -f dtu.yaml up --no-build`. Compose cannot build these itself: it never grants a build the host network, which Buildx 0.37.2 and later require.
The record is how an id leads back to a universe: every capability that takes an `id` reads it first and raises `universe-not-found` when it is missing. A stack whose directory was deleted by hand is no longer a universe to the tool; `docker compose -p <id> down --volumes` clears it.

Every capability that acts on a universe returns a `Universe`: the record above, plus `state`, its `services` as Compose reports them (state, health, image), and `urls`, the twin's published ports as the host reaches them: `http://localhost:<host port>/` for each, or the host, path, and label `x-dtu.urls` gives that container port. The entries are kept in the record, so `status` and `list` report them without rereading the profile. A `host` other than `localhost` is reported as written; whether it resolves is the client's business, and the profile reference says which clients honor `*.localhost`.

`state` is `running` when every service is up and healthy, `starting` while any is still becoming healthy, `degraded` when any has exited or is unhealthy, and `stopped` when none is running.

## Launch

From a profile to a running, ready universe, in one call.

```python
def launch(profile: str | Path, timeout_seconds: int = 600) -> Universe
```

Validates the profile and stops on any error, before anything is recorded or started. Assigns an id, renders the overlay, then brings the stack up in the order it needs: the `git` and `gateway` services first when the profile calls for them, then the certificate authority is taken out to the host, then everything else, building with the gateway reachable. Returns when every healthcheck passes.
That is `docker compose -p <id> -f <profile> -f <overlay> up --build --wait`, once per pass; Compose's progress is passed through to stderr.
With the gateway present and Buildx installed, the images are built first with `docker buildx bake --allow=network.host`, since Compose cannot grant a build the host network, and the last pass is `up --no-build --wait`; Bake's progress goes to stderr too.

When something fails after containers have started, they are left running so `doctor` and `docker compose logs` have something to read. `destroy` clears them; every failure's remedy names the command.

Raises:

- `profile-not-found`, `profile-invalid` (the report's errors), `docker-unavailable`
- `env-missing`: the variable, and the message the profile gave it
- `port-in-use`: the port, and who holds it when Docker says
- `build-failed`: the service, and the last lines of build output
- `unhealthy`: the service, its healthcheck, and the last lines of its logs
- `timeout`: what was still starting when `timeout_seconds` ran out
- `launch-failed`: anything else Compose refused, with the last lines of its output

## List

Every universe launched from this machine, running or not, oldest first.

```python
def list_universes() -> list[Universe]
```

Every record in the state directory, each measured against Docker in one pass. A universe whose containers are gone appears as `stopped` with no services. An empty machine returns an empty list without touching Docker.

Raises `docker-unavailable`.

## Status

One universe, measured now. `launch` returns the same measurement.

```python
def status(id: str) -> Universe
```

Raises `universe-not-found` and `docker-unavailable`.

## Execute

Run one command in the twin and get its `ExecResult`: exit code, stdout, stderr.

```python
def execute(
    id: str,
    command: str,
    user: str | None = None,        # default: the twin's own user
    workdir: str | None = None,     # default: the twin's own working directory
    timeout_seconds: int = 300,
) -> ExecResult
```

The command runs through a login shell (`sh -lc`), so `PATH` changes an installer made in `~/.profile` apply, the way they would in a person's terminal.
A non-zero exit is a result, not a failure: `execute(id, "curl -sf localhost:8000/health")` returning `22` is the answer.

Raises `universe-not-found`, `twin-not-running` (with the twin's state), `docker-unavailable`, and `timeout`. On `timeout` the command is abandoned, not killed; whatever it started is still running in the twin.

## Shell

An interactive shell in the twin, attached to the caller's terminal.

```python
def shell(id: str, user: str | None = None, workdir: str | None = None) -> int
```

Returns the shell's exit code when the person leaves it. The shell is `bash -l` when the image has bash, `sh -l` otherwise. In practice only the CLI calls this; it is in the library so the CLI adds nothing.

Raises `universe-not-found`, `twin-not-running`, `docker-unavailable`, and `no-tty` when the caller has no terminal.

## Push and pull files

Copy between the host and the twin. `Transfer` says where the copy landed and how many files moved.

```python
def push_files(id: str, source: Path, destination: str) -> Transfer
def pull_files(id: str, source: str, destination: Path) -> Transfer
```

Same rules as `docker cp`: when the destination is an existing directory the source is placed inside it under its own name, otherwise the source lands at the destination path itself, and the destination's parent must exist. A path in the twin is resolved against `/`, as `docker cp` does, not the twin's working directory.
Pushed files end up owned by the twin's user. `docker cp` alone would leave them owned by `root`, which is a trap when the twin runs as a user.

Raises `universe-not-found`, `twin-not-running`, `source-not-found` (the host path for a push, the twin path for a pull), `transfer-failed` (what `docker cp` refused, with its message), and `docker-unavailable`.

## Destroy

Remove a universe: every container, network, and volume, and its state directory. Built images stay, so the next `launch` of the same profile is fast. `Destroyed.removed` names what was taken down.

Containers get one second after SIGTERM, not Compose's ten: the volumes go with them, so a graceful stop has nothing to preserve, and a process running as PID 1 (`sleep infinity`, `python -m http.server`) ignores the signal and would sit out the full grace period.

```python
def destroy(id: str) -> Destroyed
```

Raises `universe-not-found` and `docker-unavailable`.

## Dashboard

A web page for a person: every universe on this machine, its state, its URLs, and a destroy button, so nobody has to remember ids. Deterministic.

```python
def serve_dashboard(port: int | None = None, host: str = "127.0.0.1") -> Dashboard
```

Binds the port, starts serving from daemon threads, and returns at once with `Dashboard`: the `url` to open, the `mcp_url` of its MCP server, and `reachable`, which says whether that is only this machine or the local network. The server lives until the process exits; the CLI blocks for it. The default host keeps it off the network; pass `0.0.0.0` to expose it deliberately.

The page is a minimal MCP Apps host that renders the view of the MCP server below, served on the same port at `/mcp` over streamable HTTP. A sandbox proxy for the view runs on a second, free port, since the MCP Apps spec puts the view on its own origin. Both are compiled assets shipped inside the package at `capabilities/dashboard/static/`, so running it needs nothing beyond the Python dependencies. The dashboard adds no capability; a capability it should show gets an MCP tool that calls it.

Raises `port-in-use`, and `dashboard-not-compiled` when the view is missing from the package.

## MCP server

The universe tools and the dashboard view as an [MCP](https://modelcontextprotocol.io) server, a thin surface over the library like the CLI, in `digital_twin_universe.adapters.mcp`.

```python
def create_server(view: Path = VIEW_PATH) -> MCPServer
```

```
open_dashboard(id?)     list_universes(), rendered as the dashboard view in hosts that support MCP Apps
list_universes()        list_universes()
universe_status(id)     status(id)
destroy_universe(id)    destroy(id)
```

The view is `ui://digital-twin-universe/dashboard`, a single HTML file shipped at `adapters/static/mcp_app.html` and read once when the server is created. A `DigitalTwinUniverseError` in a tool is a tool error carrying its message and remedy. `digital-twin-universe mcp` runs it over stdio; the dashboard serves it over streamable HTTP.

Raises `dashboard-not-compiled` when the view is missing.

## Intelligence

Model-backed capabilities run through the `Intelligence` protocol in `digital_twin_universe.intelligence.interface`:

```python
class Intelligence(Protocol):
    implementation: str

    def preflight(self) -> None: ...
    def run(self, request: AgentRequest) -> AgentResult: ...
```

`preflight` raises `DigitalTwinUniverseError` naming what to configure when the implementation cannot run.
`run` executes one agent: `AgentRequest` holds the prompt, model, optional workspace, and optional output schema; `AgentResult` holds the text, structured output, or error.
Setting `AgentRequest.resume` to an earlier `AgentResult.session_id` continues that session instead of starting a fresh one, so the agent keeps what it learned.
A request with a `workspace` gives the agent tools to read and search files and run commands in that directory on this host, and to write files too when `writable`: `view`, `grep`, and `bash`, plus `edit` and `write`, on `copilot`; `read_file`, `glob`, `grep`, and `bash`, plus `write_file` and `edit_file`, on `amplifier-agent`; Codex's own shell and file editing on `codex`, whether or not `writable`; `Read`, `Glob`, `Grep`, and `Bash`, plus `Edit` and `Write`, on `claude`. `bash` is not sandboxed to the workspace: what bounds the agent is the caller's prompt, and the caller validates everything the agent produced before any of it is kept, the way `create_profile` checks that every file lies in the draft and launches the draft itself.

`resolve_intelligence(agent_provider)` returns a shipped implementation, one per agent provider, each installed through the extra of the same name:

- `copilot`: `CopilotIntelligence`, built on the [GitHub Copilot SDK](https://github.com/github/copilot-sdk) and signed in through the GitHub CLI.
- `amplifier-agent`: `AmplifierAgentIntelligence`, built on [Amplifier Agent](https://github.com/microsoft/amplifier-agent). The model is `<provider>/<model>`. Sessions live under the platform's per-user state directory, in `digital-twin-universe/amplifier-agent`.
- `codex`: `CodexIntelligence`, built on the [OpenAI Codex SDK](https://github.com/openai/codex/tree/main/sdk/python) and run with the user's Codex sign-in and `~/.codex/config.toml`. A session is a Codex thread, kept where Codex keeps its threads. An output schema is enforced by the model API in its strict form, every property required and an optional one nullable; the nulls are dropped before the answer is checked against the original schema.
- `claude`: `ClaudeIntelligence`, built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) and run with the user's Claude Code settings and the credentials Claude Code resolves: `ANTHROPIC_API_KEY`, or a cloud provider's, as [its docs](https://code.claude.com/docs/en/agent-sdk/quickstart) describe. Sessions are kept where Claude Code keeps them; a plain completion runs in the platform's per-user state directory, in `digital-twin-universe/claude`.

Without an agent provider named, the first installed in that order is used; one that is not installed raises `DigitalTwinUniverseError` with the command that installs it.
Another implementation is a module satisfying the protocol and a branch in that factory.

## Manifest and skill

`load_manifest()` returns the tool's `SMART_TOOL.md` as structured data: the frontmatter as fields, the Markdown below it as `Manifest.body`.

`skill()` is what an agent reads once it has decided to drive the tool: the manifest body and the capability list, wrapped so the reader knows where the tool's files are. The CLI's `--help` prints exactly this.
`skill(capability)` is one capability's skill in the same shape: its body is the Liquid template the capability's row in `core/skill.py` names, rendered with the defaults from `digital_twin_universe.schemas`. The CLI's `<command> --help` prints exactly this.
`skill_directory()` is the installed package root, where the files the skill names can be read; `skill_resources()` lists those files relative to it, and every one ships inside the package.
`version()` is the installed package's version, from its metadata.
`repository_url()` is the tool's canonical source from the package metadata's `[project.urls]` `Repository` entry, or `None`; the skill carries it so a caller that can run the tool but not read its files still reaches the documentation.

## Adding a capability

A capability's code goes in `digital_twin_universe/capabilities/<name>/`, with its prompts and templates beside it, and `lib.py` gets a facade function that imports it and is the only caller of it.
It also gets a row in `CAPABILITIES` in `core/skill.py` naming its skill, a Markdown file beside its code that carries when to use it, a worked invocation, every argument, the result, and the failures.
Each capability of the library gets a section here: what it does and when to reach for it, the signature `lib.py` exposes, what each argument means, and what it returns or raises. Name the result class; describe a field only when its name does not say enough.
Model-backed capabilities say so, and take `agent_provider`, `model`, and `reasoning_effort`, defaulting to the first installed agent provider, its model in `DEFAULT_INTELLIGENCE_MODELS`, and `DEFAULT_INTELLIGENCE_REASONING_EFFORT` from `digital_twin_universe.schemas`.
