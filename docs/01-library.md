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

## Backend configuration and ownership

Deterministic host and universe calls accept optional `config: BackendConfig | None`. Import it from
`digital_twin_universe.lib` or `digital_twin_universe.schemas`:

```python
from pathlib import Path

from digital_twin_universe.lib import BackendConfig, launch, launch_id

config = BackendConfig(
    state_root=Path("/path/to/private/dtu-state"),
    docker_host="unix:///path/to/docker.sock",
)
request_id = "caller-persisted-request-id"
expected_id = launch_id(request_id, config=config)
universe = launch("./profile/compose.yaml", operation_id=request_id, config=config)
assert universe.id == expected_id
```

Configuration is scoped to the call without changing process environment or module globals. The default state
root is `~/.digital-twin-universe/universes`. New launches resolve and retain one Docker endpoint; changing the
ambient context later does not retarget them. Separate state roots produce different operation-derived project
ids. Lifecycle locks serialize launch, stop, upload, export, and destroy for one id across cooperating processes;
contention returns `operation-busy`. They do not serialize application commands, reserve host ports, or isolate
host mounts and explicitly shared resources in a trusted profile.

An embedding application chooses whether to enable this optional backend, approves profiles and source access,
owns its state root and durable request ids, and reports uncertain outcomes without replaying work. Installing
the Python package does not install Docker. `install(apply=True)` is a separate explicit host operation.
`create_profile`, `install`, dashboard, and MCP do not expose per-call backend configuration. This API is not a
remote orchestration service or a security boundary against privileged or untrusted Docker daemon operators.

For 0.6.0 compatibility, treat universe ids and returned draft paths as opaque, handle the explicit legacy
endpoint requirement below, and check `complete` before reporting stop or cleanup success. Native Windows
source snapshot, bounded export, and safe upload calls now return named unsupported errors; other operations
are not disabled by those capability limits. Native Windows and WSL have not been live-qualified for this release.

## Failures

Every failure the library can name is one exception:

```python
class DigitalTwinUniverseError(Exception):
    code: str  # stable slug to branch on, such as "port-in-use"
    message: str  # what went wrong, with the specifics: the service, the port, the variable
    remedy: str  # what to do about it
    universe_id: str | None  # owned attempt when allocation has happened
    operation_id: str | None  # caller's launch identity, when supplied
```

`str(error)` is the message followed by the remedy, which is what the CLI prints. Each capability lists the codes it raises. Anything else that escapes is a bug.

A result that carries a verdict (`HostReport.ok`, `ProfileReport.ok`, `InstallReport.outcome`) is never raised as an error when the verdict is negative. The verdict is the answer.

After a failed or interrupted launch, retain its identity and inspect `status`; use `recorded_ids` if Docker
cannot be reached. Missing observations are not proof of removal. `Stopped.complete` and `Destroyed.complete`
require observed completion; an incomplete result retains recovery state. A timeout or lost response does not
authorize another launch or command. Per-id calls can also raise `operation-busy`, `state-unreadable`,
`state-identity-mismatch`, `docker-endpoint-required`, or `docker-endpoint-mismatch`.

## Check

Whether this host can run a universe: the Docker CLI on `PATH`, a daemon answering behind it, and the Compose plugin.
Deterministic; needs no model provider.

```python
def check(config: BackendConfig | None = None) -> HostReport
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

The agent has tools and the tool has the verdict. The agent works in the project root (the git root above `project`, or `project` itself, or the working directory without one) with tools to read, search, and write files and run commands, reading the repository and a local clone of Docker's documentation, and writing only into a unique private `<name>.draft-<suffix>/` beside where the profile will land. It must validate, launch, run every check in the twin, and destroy on its own before submitting, and the submission is accepted only when it carries the id of a universe the tool saw appear during the run and a passed result for every check; anything less is sent back once as a correction, then `profile-rejected`. The tool then validates, launches, reruns the same checks through `execute`, and destroys. Only a draft that passes for the tool is renamed to `<name>/`.

Concurrent calls never empty or reuse each other's drafts. Promotion takes a per-profile lock and checks the
destination against its initial fingerprint, including with `overwrite=True`. A changed destination raises
`profile-changed` and preserves the independent draft. Replacement temporarily retains the previous profile
and restores it if the draft rename fails; inspect a retained `.previous` directory after process interruption.

After the tool's verdict, one more turn in the same session asks the agent to clean up: destroy anything it launched that is still listed, remove scratch it made outside the draft, and take out of the profile anything that was there only for iteration. When that edits the profile, or the draft's files differ afterwards whatever the agent said, the tool validates and launches once more before promoting.

```
created      the agent launched and checked, the tool launched and checked, cleanup ran; <name>/ exists
validated    verify=False; validate-profile has no errors; <name>/ exists
failed       attempts exhausted, or a required variable is unset; the result's private draft path holds the last attempt
```

`CreatedProfile.checks` is the tool's own run, not the agent's. `env-missing` is never sent back to the agent as a defect: the profile is right to demand the variable, so the run ends `failed` with `next` saying what to export. `keep=True` leaves the tool's verified universe running with its `universe_id` and `urls` in the result and its record pointed at `<name>/`; when cleanup changed the profile, the universe launched from the cleaned profile is the one kept.

Incomplete cleanup of the tool's verified universe also ends `failed`, retaining its id and cleanup details in
the result instead of promoting the draft or launching another verification universe.

The tool destroys only universes identified by its own launch result or launch error. It never infers cleanup
ownership from a before/after difference in the machine-wide inventory. Universes the agent launched are the
agent's to destroy, in the authoring turns and again in the cleanup turn. Any universe still listed with a
`profile_path` under this run's unique draft goes into `notes` with its id and a suggested `destroy` command;
the tool does not sweep it. Inventory observations support authoring evidence, not destructive authority.

The documentation the agent reads is kept at `~/.digital-twin-universe/reference/`: a sparse, shallow clone of `docker/docs` holding the Compose file reference, the Compose manual, and the build manual, plus the Dockerfile reference fetched from BuildKit, refreshed when older than seven days. Without `git` or without network the reference is reported absent in `notes` and the run goes on.

Raises `profile-exists`, `profile-changed`, `project-not-found`, `name-invalid`, `keep-needs-verify`, `docker-unavailable`, `profile-rejected` (the agent's submission was unusable after the correction round), `create-timeout` (with the report so far), and the intelligence preflight codes. A failed verification is an outcome, not an exception.

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

## Validate profile

Whether a profile can be launched, and what would be unrealistic about it if it were.
Deterministic. Needs the Docker CLI for `docker compose config`, which does the Compose-side validation.

```python
def validate_profile(profile: str | Path, config: BackendConfig | None = None) -> ProfileReport
```

Checks run in this order and every problem is reported, not only the first:

1. Profile resolution.
2. `docker compose config`, with the host's environment. This is Compose's own validation, and it also resolves interpolation, so an unset `${GH_TOKEN:?message}` surfaces here with its message.
3. `x-dtu` against its schema.
4. The universe invariants (errors) and realism checks (warnings) listed in the profile reference.

`ProfileReport.ok` is "no errors"; warnings do not affect it. Each `Finding` carries a stable `code`, a `location` in the file such as `services.copilot.volumes[0]`, the message, and a remedy.

Raises `profile-not-found` and `docker-unavailable`.

## Existing environment endpoints

Per-id calls and `list_universes` require a pinned or explicitly supplied Docker endpoint. Older records without one now raise
`docker-endpoint-required` before accessing Docker, even if the current context or `DOCKER_HOST` is set.
Identify the original endpoint and pass `config=BackendConfig(docker_host="unix:///path/to/docker.sock")` for
that call. This explicit selection does not change the record or infer its origin. New records keep their
pinned endpoint; an explicit mismatch raises `docker-endpoint-mismatch`.

`list_universes` can raise on a legacy record instead of reporting it against an unverified current context.
Use `recorded_ids` without Docker, then inspect each id with its original endpoint. The dashboard and MCP
surfaces cannot adopt legacy records; use the library or CLI for this recovery.

## Universe

A universe is one Compose project. Its `id` is the project's name: `dtu-<profile name>-<32 hex>` for a new random
launch, or `dtu-op-<32 hex>` for a caller-supplied operation id. Treat ids as opaque. `docker compose -p <id> logs`
reaches the same stack when used against its pinned endpoint.

What the tool renders for a universe lives in its state directory, `~/.digital-twin-universe/universes/<id>/`: `dtu.yaml`, the overlay; `overlay/`, its CA and self-contained repository snapshots with provenance receipts; `bake.yaml`, a private build-only definition excluding runtime environment; and `universe.json`, the universe record. Explicit build arguments remain saved inputs; use file/environment build secrets for sensitive values. Container state is measured from Docker.

Records are atomically replaced under a private state directory. `lifecycle.yaml` retains only service identities
and network/volume declarations for later control without rereading a changed or deleted source profile.
Resolved launch settings briefly reside in private `launch.private.yaml`; normal completion or failure removes
it, but process death can leave credentials in that file. Recovery never executes it automatically. Arbitrary
build contexts are not copied by this mechanism; only explicitly exported repository inputs are snapshots.

A profile that serves nothing and rewrites nothing renders no overlay at all, so `dtu.yaml` is absent and the universe is exactly the profile.
By hand, a universe with the gateway and a build runs as `docker buildx bake --allow=network.host --allow=fs.read=<dir> --load -f bake.yaml`, with one `fs.read` per build context and for `overlay/`, then `docker compose -p <id> -f <profile> -f dtu.yaml up --no-build`. Compose cannot build these itself: it never grants a build the host network, which Buildx 0.37.2 and later require.
The record is how an id leads back to a universe. Missing records raise `universe-not-found`, except that a
completed destroy can return its retained cleanup receipt. Do not delete state to reset an uncertain operation:
it contains the endpoint and ownership needed for recovery. Manually orphaned stacks need operator inspection
of the exact endpoint and resource labels before removal.

`launch`, `status`, and entries from `list_universes` return a `Universe`: the record above, plus `state`, its `services` as Compose reports them (state, health, image), and `urls`, the twin's published ports as the host reaches them: `http://localhost:<host port>/` for each, or the host, path, and label `x-dtu.urls` gives that container port. The entries are kept in the record, so `status` and `list` report them without rereading the profile. A `host` other than `localhost` is reported as written; whether it resolves is the client's business, and the profile reference says which clients honor `*.localhost`.

`state` is `running` when every expected service is up and healthy, `starting` while any is still becoming healthy,
`degraded` when some are missing, exited, or unhealthy while others run, and `stopped` when none is running.
`phase` is the retained lifecycle progress, not proof that a process survived; `operation_id` and
`expected_services` preserve the attempt's identity and intended service set.

## Launch

From a profile to a running, ready universe, in one call.

```python
def launch(
    profile: str | Path,
    timeout_seconds: int = 600,
    operation_id: str | None = None,
    config: BackendConfig | None = None,
) -> Universe

def launch_id(operation_id: str, config: BackendConfig | None = None) -> str
```

`launch_id` computes the id without Docker or effects from a nonempty operation id (up to 512 characters) and
the canonical state-root path. Persist that caller identity before launching. Repeating a ready operation
measures its existing environment; repeating an allocated, failed, stopped, or otherwise incomplete operation
raises `operation-incomplete` without restarting it. A removed operation raises `operation-removed`, and a
different profile request under an occupied identity raises `operation-conflict`. Use a new operation id only
for explicitly requested new work. Without one, each call allocates a new random id.

Validates before starting Docker resources, records the owner, renders the overlay, then brings the stack up
in the order it needs: the `git` and `gateway` services first when the profile calls for them, then the certificate
authority is taken out to the host, then everything else, building with the gateway reachable. Repository
readiness waits for all repositories and refs to finish publishing. Returns after Compose's health checks.
That is `docker compose -p <id> -f <profile> -f <overlay> up --build --wait`, once per pass; Compose's progress is passed through to stderr.
With the gateway present and Buildx installed, the images are built first with `docker buildx bake --allow=network.host`, since Compose cannot grant a build the host network, and the last pass is `up --no-build --wait`; Bake's progress goes to stderr too.

Failures after allocation retain the owned id in `DigitalTwinUniverseError.universe_id` and leave state and
any started resources for inspection. A killed caller can recover its deterministic id with `launch_id`.
Inspect first, then stop or export and destroy as appropriate; launch does not replay uncertain effects.

Raises:

- `profile-not-found`, `profile-invalid` (the report's errors), `docker-unavailable`
- `env-missing`: the variable, and the message the profile gave it
- `port-in-use`: the port, and who holds it when Docker says
- `build-failed`: the service, and the last lines of build output
- `unhealthy`: the service, its healthcheck, and the last lines of its logs
- `timeout`: what was still starting when `timeout_seconds` ran out
- `launch-failed`: anything else Compose refused, with the last lines of its output
- `operation-id-invalid`, `operation-conflict`, `operation-incomplete`, `operation-removed`, `operation-busy`
- `allocation-failed`, `docker-endpoint-unknown`, `docker-endpoint-missing`, and repository snapshot errors

## List

Every universe recorded under the selected state root, running or not, oldest first.

```python
def list_universes(config: BackendConfig | None = None) -> list[Universe]
```

Every record in the state directory, each measured against Docker in one pass. A universe whose containers are gone appears as `stopped` with no services. An empty machine returns an empty list without touching Docker.

Raises `docker-unavailable`.

### Recorded ids

```python
def recorded_ids(config: BackendConfig | None = None) -> list[str]
```

Sorted ids with a `universe.json` record on this machine, including failed launches. Reads only the filesystem,
without Docker inventory or parsing the records. An absent state root returns an empty list.

## Status

One universe, measured now. `launch` returns the same measurement.

```python
def status(id: str, config: BackendConfig | None = None) -> Universe
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
    max_output_bytes: int = 1048576,  # retained bytes per stream, at most 16 MiB
    config: BackendConfig | None = None,
) -> ExecResult
```

The command runs through a login shell (`sh -lc`), so `PATH` changes an installer made in `~/.profile` apply, the way they would in a person's terminal.
A non-zero exit is a result, not a failure: `execute(id, "curl -sf localhost:8000/health")` returning `22` is the answer.

Both streams are drained while retaining at most `max_output_bytes` each. `stdout_truncated` and
`stderr_truncated` report discarded output. Raises `universe-not-found`, `twin-not-running`,
`execution-limit-invalid`, `docker-unavailable`, and `timeout`. A timeout terminates the local Docker client;
the command may still be running inside the twin. Inspect or stop the environment rather than replaying it.

## Shell

An interactive shell in the twin, attached to the caller's terminal.

```python
def shell(
    id: str, user: str | None = None, workdir: str | None = None, config: BackendConfig | None = None
) -> int
```

Returns the shell's exit code when the person leaves it. The shell is `bash -l` when the image has bash, `sh -l` otherwise. In practice only the CLI calls this; it is in the library so the CLI adds nothing.

Raises `universe-not-found`, `twin-not-running`, `docker-unavailable`, and `no-tty` when the caller has no terminal.

## Export repository snapshot

```python
def export_repository_snapshot(source: Path, destination: Path) -> RepositorySnapshot
```

Deterministic, without Docker or a model provider. Exports an approved repository or linked worktree's current
tracked and nonignored untracked bytes to a new private tar archive outside the source. Preserves modes,
deletions and symlink text; submodules become empty directories. No source Git metadata, hooks, filters,
export-ignore or export-subst transformations are applied. Source files and index are unchanged. An existing
archive is never replaced, and detected concurrent source changes fail without retry. Source credentials are
not scrubbed: the caller approves what source it exports.

Archive consistency checks cover the selected checkout's HEAD, branch, index and file contents. Unrelated
branch or tag changes, including commits in other linked worktrees, do not invalidate an archive that excludes
history. Repository exports that include history still require stable local branches and tags.

Returns archive path, byte count and SHA256, source HEAD/branch, snapshot tree/commit, file count and shallow
source flag. Safe file reads currently require Linux, WSL or macOS; native Windows reports
`repository-snapshot-unsupported`. See `export-repository-snapshot --help` for the named size bounds and failure
codes. Consumers must extract safely without following links outside their destination.

## Push and pull files

Copy between the host and the twin. `Transfer` says where the copy landed and how many files moved.

```python
def push_files(id: str, source: Path, destination: str, config: BackendConfig | None = None) -> Transfer
def pull_files(id: str, source: str, destination: Path, config: BackendConfig | None = None) -> Transfer
```

Same rules as `docker cp`: when the destination is an existing directory the source is placed inside it under its own name, otherwise the source lands at the destination path itself, and the destination's parent must exist. A path in the twin is resolved against `/`, as `docker cp` does, not the twin's working directory.
Pushed files receive the twin user's numeric ownership through a private upload archive, preserving host permissions and avoiding in-container `chown`. Modes and symlink text are preserved without reading symlink targets. Uploads require Linux, WSL, or macOS no-follow reads; native Windows returns `transfer-unsupported`. Preparation and upload are bounded; see `skill("file-push")` for limits and failure recovery.

Both calls require a running twin. A failed upload may leave partial container files; inspect before retrying.
Pull copies directly onto the host. Use `export_files` for bounded, unextracted retention from a stopped twin.

Raises `universe-not-found`, `twin-not-running`, `source-not-found` (the host path for a push, the twin path for a pull), `transfer-failed`, and `docker-unavailable`. Uploads also report `transfer-limit`, `transfer-timeout`, `transfer-source-changed`, `transfer-file-unsupported`, `transfer-owner-unknown`, or `transfer-path-invalid` when preparation cannot complete safely.

## Stop

```python
def stop(id: str, config: BackendConfig | None = None, timeout_seconds: int = 10) -> Stopped
```

Stops exact-project-labelled containers, including orphans, then checks whether any are running or restarting.
The grace period is 1 to 300 seconds. Containers, volumes, networks, and state remain. `outcome` is `stopped`,
`incomplete`, or `unknown`; `running=None` means inspection failed. `complete` requires observed cessation and
no errors. Stop does not resume the environment or replay commands. Raises `timeout-invalid` and shared
ownership/state errors; Docker stop or inspection failures are reported in `Stopped.errors`.

## Export files

```python
def export_files(
    id: str,
    source: str,
    destination: Path,
    max_bytes: int = 268435456,
    timeout_seconds: int = 60,
    config: BackendConfig | None = None,
) -> FileArchive
```

Streams a tar archive from exactly one owned twin container, running or stopped, without restarting it or
extracting archive entries on the host. `source` is an absolute container path. The destination directory must
exist; publication is private, atomic, and never overwrites an existing file. `FileArchive` gives the path,
byte count, and SHA-256. Limits are 256 MiB and 60 seconds by default, at most 16 GiB and 3600 seconds.

Export preserves only the requested path, not every volume, another service's data, or a complete environment
backup. Stop first for a stable source; an export from a running container is not a transactional snapshot.
Treat archive entries as untrusted when opening them elsewhere. Export required paths successfully before
destroy, and retain the returned receipts. A failed export leaves the environment intact and removes only
that call's partial archive.

Bounded pipe handling requires Linux, WSL, or macOS; native Windows raises `export-unsupported` before copying.
Other failures include `export-path-invalid`, `export-limit-invalid`, `export-destination-exists`,
`export-destination-missing`, `export-twin-ambiguous`, `export-inspection-failed`, `export-timeout`,
`export-too-large`, `export-empty`, `export-failed`, `export-io-failed`, and shared ownership/state errors.

## Destroy

Take a universe down and observe its containers, networks, and volumes by exact Compose project label.
Compose teardown preserves volumes; the tool separately removes only observed owned volumes after a fresh
identity check, and residual owned networks by immutable id. Declared resource names alone never authorize
removal. External resources are preserved. Volume removal is non-forced, so an in-use volume remains as residue.
Built images stay. `Destroyed.removed` names pre-down resources confirmed absent afterwards.
`remaining` holds post-down names under `containers`, `networks`, and `volumes`; `None` means that inventory failed.
`errors` names inspection or state-cleanup errors. `outcome` is `removed`, `residue`, or `unknown`.

State is removed only after all three inventories succeed and are empty. Residue or failed inspection retains
the record. Failed state deletion reports `residue` and attempts to restore the record; if restoration fails,
the error names manual state cleanup rather than a retry command. `state_removed` reports whether the directory is gone.
`Destroyed.complete` is true only when all inventories are empty, state is gone, and there are no errors.
`Destroyed.require_removed()` raises `cleanup-incomplete` otherwise, including for defaulted legacy results without observations.
The CLI prints the result JSON and exits 1 for incomplete cleanup, 0 for complete cleanup.

Export required data first. Containers get one second after SIGTERM before forced cessation. Use `stop` when
the environment and its data should survive. A successful cleanup receipt is retained outside the removed
state directory; repeating destroy returns that observation without deleting new resources.

Locks and label/identity checks coordinate cooperating callers. Docker volumes have no immutable id or atomic
compare-and-delete operation, so this is not protection against a privileged daemon operator replacing a
volume between final inspection and removal.

```python
def destroy(id: str, config: BackendConfig | None = None) -> Destroyed
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
`version()` is the installed package's version, from its metadata.

## Adding a capability

A capability's code goes in `digital_twin_universe/capabilities/<name>/`, with its prompts and templates beside it, and `lib.py` gets a facade function that imports it and is the only caller of it.
It also gets a row in `CAPABILITIES` in `core/skill.py` naming its skill, a Markdown file `skill_<capability>.md` beside its code, the capability's name with hyphens as underscores, that carries when to use it, a worked invocation, every argument, and the result.
Each capability of the library gets a section here: what it does and when to reach for it, the signature `lib.py` exposes, what each argument means, and what it returns or raises. Name the result class; describe a field only when its name does not say enough.
Model-backed capabilities say so, and take `agent_provider`, `model`, and `reasoning_effort`, defaulting to the first installed agent provider, its model in `DEFAULT_INTELLIGENCE_MODELS`, and `DEFAULT_INTELLIGENCE_REASONING_EFFORT` from `digital_twin_universe.schemas`.
