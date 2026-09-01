# Library Reference

Every capability of Digital Twin Universe is reachable from the
`amplifier_digital_twin_universe` library. The CLI and any other surface are
thin wrappers over it and add no capability of their own.

## load_manifest

Reads the manifest shipped inside the installed package (`SMART_TOOL.md`) and
returns it as structured data. Deterministic. Requires no model provider.

```python
def load_manifest() -> Manifest
```

- Returns a `Manifest` with `smart_tool_format`, `name`, `version`,
  `description`, `use_cases`, `platforms`, and `requires` (a list of
  `Requirement`, each with `name`, `purpose`, `install`, `optional`).

## probe

Measures this host against the manifest's declared requirements. Deterministic
and read-only: nothing here installs, configures, or starts anything.

```python
def probe() -> HostReport
```

- Returns a `HostReport` with `platform`, `supported`, `ready` (every required
  prerequisite present), `can_launch` (the container runtime is installed and
  answers), `prerequisites` (list of `Prerequisite`), `model_providers` (list
  of provider names whose credentials resolve), `environments` (count, or
  `None` when `can_launch` is false), and `notes`.

## validate_profile

Checks whether a profile document is launchable, by delegating to the DTU
engine's own loader so a profile that validates here parses identically at
launch. Deterministic. Requires no model provider.

```python
def validate_profile(
    yaml_text: str,
    variables: dict[str, str] | None = None,
    base_dir: Path | str | None = None,
) -> ValidationReport
```

- `yaml_text`: the profile document.
- `variables`: values substituted for `${NAME}` references.
- `base_dir`: the directory relative `provision.files` sources resolve
  against. Without it, relative sources resolve against the process cwd.
- Returns a `ValidationReport` with `valid`, `name`, `description`, `errors`,
  `warnings` (the loader drops unknown fields silently, so a warning here is a
  profile that would launch and do the wrong thing), and
  `unresolved_variables`.

## launch

Stands up one environment from a profile and returns how to reach it.
Deterministic. Requires `incus`. Blocks until provisioning finishes, which is
minutes for a profile that installs a toolchain.

```python
def launch(
    profile: str | Path,
    *,
    variables: dict[str, str] | None = None,
    name: str | None = None,
    hostname: str | None = None,
    max_environments: int | None = None,
) -> dict[str, Any]
```

- `profile`: path to a profile document.
- `variables`: values substituted for `${NAME}` references at launch.
- `name`: name the environment instead of generating one.
- `hostname`: register `NAME.local` for the environment over mDNS.
- `max_environments`: ceiling on concurrent environments. Precedence is this
  argument, then `AMPLIFIER_DTU_MAX_ENVIRONMENTS`, then the default of 15.
  Zero removes the ceiling.
- Returns `id`, `name`, `profile`, `status`, `created_at`, and, when the
  profile declares them, `access`, `container_ip`, and `mock_services`.

## run

Runs one command inside an environment under a login shell and captures what
it produced. Deterministic. Requires `incus`.

```python
def run(
    environment_id: str,
    command: list[str],
    *,
    timeout: int | None = DEFAULT_EXEC_TIMEOUT_SECONDS,
) -> dict[str, Any]
```

- `environment_id`: the environment's id, as reported by `list_environments`.
- `command`: argv to run. Must not be empty.
- `timeout`: seconds to allow, or `None` for no timeout. `DEFAULT_EXEC_TIMEOUT_SECONDS` is 600.
- Returns `id`, `command`, `exit_code`, `stdout`, `stderr`. A non-zero
  `exit_code` is a result, not a failure of this call.

## status

Reports one environment's state, profile, creation time, and access URLs.
Deterministic. Requires `incus`.

```python
def status(environment_id: str) -> dict[str, Any]
```

- `environment_id`: the environment's id.
- Returns `id`, `profile`, `status`, `created_at`, and, when applicable,
  `hostname` and `access`.

## list_environments

Lists every environment this tool manages on this host. Deterministic.
Requires `incus`. Scoped to the machine, not to a session: an environment
another session launched appears here too.

```python
def list_environments() -> list[dict[str, Any]]
```

- Returns a list of environments, each shaped like `status`'s result.

## check_readiness

Evaluates an environment's readiness checks once. Deterministic. Requires
`incus`. Blocks for as long as the profile's access-port verification budget
allows.

```python
def check_readiness(environment_id: str, *, skip_access_check: bool = False) -> dict[str, Any]
```

- `environment_id`: the environment's id.
- `skip_access_check`: evaluate only the in-environment checks, not host-side
  port reachability.
- Returns `ready` (`true`, `false`, or `null` when the profile declares no
  checks, which is not the same as failing them), `message`, and, when
  applicable, `checks` and `access`.

## update

Re-runs a running environment's update commands in place, from the profile
snapshot taken at launch rather than the host copy. Deterministic. Requires
`incus`.

```python
def update(
    environment_id: str,
    *,
    variables: dict[str, str] | None = None,
    skip_readiness: bool = False,
) -> dict[str, Any]
```

- `environment_id`: the environment's id.
- `variables`: values substituted for `${NAME}` references in the update
  commands.
- `skip_readiness`: do not re-run readiness checks afterward.
- Returns `id`, `profile`, `status`, `pypi_refreshed`, `cmds_run`, and, unless
  skipped, `readiness`.

## push_files

Copies host paths into an environment. Deterministic. Requires `incus`. A
directory source is walked whether or not `recursive` is set, and keeps its
own name under the destination, as `cp -r` does.

```python
def push_files(
    environment_id: str,
    sources: list[str],
    destination: str,
    *,
    recursive: bool = False,
    create_dirs: bool = True,
    mode: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    timeout: int = DEFAULT_FILE_TIMEOUT_SECONDS,
) -> dict[str, Any]
```

- `sources`: host paths to copy. Must not be empty.
- `destination`: path inside the environment.
- `recursive`: copy directories and their contents.
- `create_dirs`: create missing parent directories at the destination.
- `mode`, `uid`, `gid`: permission bits and ownership to set inside the
  environment.
- `timeout`: seconds allowed per underlying transfer, not the whole tree.
  `DEFAULT_FILE_TIMEOUT_SECONDS` is 120.
- Returns `id`, `sources`, `destination`, `transferred`.

## pull_files

Copies environment paths out to the host. Deterministic. Requires `incus`.
Environments are ephemeral, so anything worth keeping leaves this way before
`destroy`.

```python
def pull_files(
    environment_id: str,
    sources: list[str],
    destination: str,
    *,
    recursive: bool = False,
    create_dirs: bool = True,
    timeout: int = DEFAULT_FILE_TIMEOUT_SECONDS,
) -> dict[str, Any]
```

- `sources`: environment paths to copy. Must not be empty.
- `destination`: host path to write to.
- `recursive`: copy directories and their contents.
- `create_dirs`: create missing parent directories on the host.
- `timeout`: seconds allowed per underlying transfer.
- Returns `id`, `sources`, `destination`, `transferred`.

## destroy

Tears down an environment and everything launched beside it: stops mock
sidecars, releases the mDNS hostname, and deletes the container. Nothing
inside survives. Deterministic. Requires `incus`.

```python
def destroy(environment_id: str) -> dict[str, Any]
```

- `environment_id`: the environment's id.
- Returns `id`, `destroyed`.

## create_profile

**Model-backed.** Consumes tokens and may return a different answer on a
second run. Fails rather than degrades when no model provider is configured.

Drafts a launchable DTU profile from a description of what to test. Every
draft is run through the engine's own loader and repaired against its
findings until it parses cleanly or the attempt budget is spent.

```python
def create_profile(
    description: str,
    *,
    context: str | None = None,
    variables: dict[str, str] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> ProfileDraft
```

- `description`: what to stand up and test. Must not be empty.
- `context`: additional material the caller already holds, passed as data
  rather than as a path.
- `variables`: names that resolve at launch and may be referenced as
  `${NAME}` in the drafted profile.
- `max_attempts`: draft-and-repair budget. `DEFAULT_MAX_ATTEMPTS` is 3.
- `provider`, `model`: override the model provider and model to use.
- `intelligence`: an `Intelligence` implementation to use instead of the
  shipped default.
- Returns a `ProfileDraft` with `yaml_text`, `name`, `description`,
  `attempts`, `warnings`, `unresolved_variables`, and usage
  (`provider`, `model`, `tokens_in`, `tokens_out`, `cost_usd`). Raises
  `GenerationFailedError` when no draft parses cleanly within the budget.

## plan_install

**Model-backed.** Consumes tokens and may return a different answer on a
second run. Fails rather than degrades when no model provider is configured.

Produces ordered install steps for this host. The host evidence the plan is
built from is gathered deterministically and returned alongside it. Proposes
only: nothing is installed, configured, or started.

```python
def plan_install(
    *,
    goal: str | None = None,
    context: str | None = None,
    host: HostReport | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> InstallPlan
```

- `goal`: narrows the plan to what a particular use needs.
- `context`: additional material the caller already holds.
- `host`: a `HostReport` to plan against instead of probing this host again.
- `max_attempts`, `provider`, `model`, `intelligence`: as in `create_profile`.
- Returns an `InstallPlan` with `ready`, `summary`, `steps` (each an
  `InstallStep` with `title`, `why`, `commands`, `verify`), `notes`, `host`
  (the `HostReport` the plan was built from), and `usage`.

## diagnose

**Model-backed.** Consumes tokens and may return a different answer on a
second run. Fails rather than degrades when no model provider is configured.

Explains what is wrong with this host or one environment, and how to fix it.
Evidence is measured before the model sees anything and returned with the
diagnosis so the reading can be checked against it. Repairs nothing.

```python
def diagnose(
    symptom: str | None = None,
    *,
    environment_id: str | None = None,
    context: str | None = None,
    evidence: dict[str, Any] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> Diagnosis
```

- `symptom`: what the caller observed, in their own words. Without it the
  diagnosis covers whatever the evidence itself shows.
- `environment_id`: measure this environment as well as the host.
- `context`: additional material the caller already holds.
- `evidence`: pre-gathered evidence to diagnose instead of calling
  `gather_evidence` again.
- `max_attempts`, `provider`, `model`, `intelligence`: as in `create_profile`.
- Returns a `Diagnosis` with `summary`, `findings` (each a `Finding` with
  `issue`, `cause`, `confidence`, `remedy`, `commands`), `evidence`, `usage`.

`gather_evidence(environment_id=None, *, host=None) -> dict[str, Any]` is the
deterministic probe `diagnose` runs first; a failed probe is recorded as
evidence rather than raised.

## manage

**Model-backed.** Consumes tokens and may return a different answer on a
second run. Fails rather than degrades when no model provider is configured.

Turns a request in words into deterministic actions, and runs them when
confirmed. The model chooses from a fixed registry of this tool's own
deterministic capabilities (`ACTIONS`) and supplies their arguments; it
cannot invent an action or run anything itself. Without `confirmed`, the plan
comes back unrun and nothing changes. With `confirmed`, every step runs in
order and execution stops at the first failure.

```python
def manage(
    request: str,
    *,
    confirmed: bool = False,
    host: HostReport | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> ManagePlan
```

- `request`: what to do, in the caller's own words. Must not be empty.
- `confirmed`: run the planned steps. Authorizes this invocation only; there
  is no session-wide unlock and no environment variable that grants it.
- `host`: a `HostReport` to plan against instead of probing this host again.
- `max_attempts`, `provider`, `model`, `intelligence`: as in `create_profile`.
- Returns a `ManagePlan` with `request`, `summary`, `steps` (each a
  `PlannedStep` with `action`, `arguments`, `why`, `mutating`, `ran`, `ok`,
  `result`, `error`), `mutating`, `confirmed`, `executed`, `complete`, `usage`.

## Failure

Every failure this tool raises deliberately is a `SmartToolError`, carrying a
stable `.code` a caller can branch on and a `.remedy` a caller can act on.

- `MissingPrerequisiteError` (`missing_prerequisite`): something the manifest
  declares under `requires` is absent.
- `NoProviderError` (`no_provider`): a model-backed capability was invoked
  with no model provider configured. Raised instead of falling back to a
  deterministic answer.
- `ProfileInvalidError` (`profile_invalid`): a profile failed to parse, so
  nothing can be launched from it.
- `ProfileNotFoundError` (`profile_not_found`): the named profile does not
  resolve to a file on this host.
- `EnvironmentNotFoundError` (`environment_not_found`): no environment on this
  host carries the given id.
- `EnvironmentLimitError` (`environment_limit`): launching would exceed the
  concurrent environment ceiling.
- `OperationFailedError` (`operation_failed`): an environment operation ran
  and did not succeed.
- `OperationTimedOutError` (`timed_out`): an environment operation exceeded
  its time budget.
- `GenerationFailedError` (`generation_failed`): the model did not produce a
  usable answer within the attempt budget. Carries `.attempts`, the per-attempt
  findings.

## The model seam

Every model-backed capability runs through the `Intelligence` protocol:

```python
class Intelligence(Protocol):
    implementation: str

    def available_providers(self) -> list[str]: ...
    def preflight(self, provider: str | None = None) -> str: ...
    def run(self, request: ModelRequest) -> ModelResult: ...
```

`available_providers` reports which providers have resolvable credentials.
`preflight` returns the provider that will serve a request, or raises
`NoProviderError` naming the remedy, before any prompt is built. `run` runs
one turn to completion.

Every model-backed capability (`create_profile`, `plan_install`, `diagnose`,
`manage`) accepts an `intelligence=` argument. Passing one substitutes that
implementation for the call; omitting it resolves to
`default_intelligence()`, which returns the shipped `AmplifierIntelligence`,
built on the amplifier-agent engine. Importing this package never pulls in a
provider stack: `default_intelligence()` imports its implementation inside
the function body, so deterministic capabilities stay runnable with nothing
configured.
