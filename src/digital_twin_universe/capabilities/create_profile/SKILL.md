`create-profile` turns a description, and optionally a repository, into a profile that is known
to work. An agent reads the project and Docker's own documentation, writes `compose.yaml` and
anything it builds from into a draft directory, launches it, runs checks in the twin, and
destroys it. The tool then validates the draft, launches it again itself, reruns the agent's
checks, and keeps the profile only when every check passes.

Use it when nobody has written a profile for the project yet and you do not want to work out
the Dockerfile, services, ports, and environment by hand. It costs a few minutes and a model
call per submission. For a profile you can write from the examples, `validate-profile` and
`launch` are enough.

Run `digital-twin-universe check` first: without a usable Docker it fails before any model
call.

```bash
digital-twin-universe create-profile \
  --description "a FastAPI app on port 8000 using Postgres" \
  --project .

# keep the verified universe up and get its id and URLs back
digital-twin-universe create-profile \
  --description "the CLI installed from this repository, run as a new user would" \
  --project ~/src/my-cli \
  --name my-cli \
  --keep

# through Amplifier Agent, on another model
digital-twin-universe create-profile \
  --description "a FastAPI app on port 8000 using Postgres" \
  --project . \
  --agent-provider amplifier-agent \
  --model anthropic/claude-opus-5
```

```python
from pathlib import Path

from digital_twin_universe.lib import create_profile

created = create_profile(
    "a FastAPI app on port 8000 using Postgres",
    project=Path("~/src/my-app").expanduser(),
    keep=True,
)
created.outcome, created.path, created.checks, created.universe_id, created.urls, created.next
```

## Where the profile goes

The profile is written to `.agents/digital-twin-universe/<name>/` under the git root that
holds `--project`, or under `--project` itself when it is not in a repository. Without
`--project` the same rule applies to the working directory. While the run is in progress the
files live in `<name>.draft/` beside it; that directory is emptied at the start of every run
and renamed to `<name>/` only when the profile is kept.

Docker's documentation is cached under `~/.digital-twin-universe/reference/` and refreshed
when it is more than a week old. Fetching it is best effort: without git or network the run
goes on and the result's `notes` say what was missing.

## Arguments

- `--description TEXT`: what the universe is for, in your own words. Required. Name what
  has to be true inside it: the ports it serves, the services it needs, how the software gets
  in. The agent's checks prove these claims.
- `--project PATH`: the repository to profile. It must be a directory. Omit it when the
  description says everything, for instance a published package installed from an index.
- `--name NAME`: the profile name, lowercase letters, digits, and single hyphens. It becomes
  the Compose project name and the start of the universe id. Derived from the description's
  first words when omitted, skipping words like `a` and `the` and stopping at 40 characters,
  so `a FastAPI app on port 8000` becomes `fastapi-app-port-8000`.
- `--no-verify`: stop once `validate-profile` passes. Neither the agent nor the tool
  launches anything, the checks are reported `skipped`, and the outcome is `validated`.
  Library: `verify=False`.
- `--keep`: leave the tool's verified universe running and report its id and URLs. Refused
  together with `--no-verify`, which launches nothing to keep.
- `--overwrite`: replace an existing profile of the same name. Without it, an existing
  `<name>/` fails the run before any model call.
- `--max-attempts N`: how many submissions the tool considers, at least 1. Defaults to 3. A
  submission that fails validation or the tool's verification is sent back with the findings.
  One that is unusable (a missing file, the wrong `name:`, or no evidence the agent launched it
  and saw every check pass) gets one correction, within the same limit, before the run fails
  with `profile-rejected`.
- `--agent-provider`: what the agent runs through, `{{ agent_providers | join: "` or `" }}`. The
  first installed, in that order, when omitted.
- `--model`: the model that writes the profile: a Copilot model id for `copilot`,
  `<provider>/<model>` for `amplifier-agent` (for instance `anthropic/claude-opus-5`), a
  Codex model id for `codex`, a Claude model id for `claude`.
  Defaults to `{{ default_intelligence_models.copilot }}` on `copilot`,
  `{{ default_intelligence_models["amplifier-agent"] }}` on `amplifier-agent`,
  `{{ default_intelligence_models.codex }}` on `codex`, and
  `{{ default_intelligence_models.claude }}` on `claude`.
- `--reasoning-effort`: one of `low`, `medium`, `high`, `xhigh`, `max`. Defaults to
  `{{ default_intelligence_reasoning_effort }}`. Applies to the `copilot`, `codex`, and `claude`
  agent providers.
- `--timeout-seconds N`: the deadline for the whole run, at least 1. Defaults to 1800. A
  launch that builds images takes minutes, so leave room for several.
- `intelligence`, library only: the `Intelligence` implementation the agent runs through,
  which wins over `agent_provider`. Tests inject a fake.

## Result

`CreatedProfile` JSON on stdout, one progress line per phase on stderr (`authoring`,
`validating`, `launching`, `checking`, `destroying`, `cleaning up`).

- `outcome`: `created` (verified and kept), `validated` (kept under `--no-verify`), or
  `failed` (left as a draft).
- `name` and `path`: the profile name and where it is, `<name>/` when kept and
  `<name>.draft/` when failed.
- `summary`: what the universe is and how the software gets into it, in the agent's words.
- `files`: what the agent wrote, relative to `path`.
- `environment`: host variables the profile reads; set them before `launch`.
- `validation`: the last `validate-profile` report, with its `errors` and `warnings`.
- `checks`: the tool's own run of each check, each with `command`, `expect_stdout`,
  `status` (`passed`, `failed`, or `skipped`), and `detail` (the exit code and output tail when
  it did not pass).
- `universe_id` and `urls`: the tool's universe and its published ports under `--keep`. A
  `universe_id` without `--keep` is one the tool could not destroy; `notes` says so.
- `attempts`: how many submissions were made.
- `cleanup`: what the agent's cleanup turn destroyed and removed, or null when the run failed
  before cleanup.
- `notes`: trade-offs the agent made, validation warnings, missing documentation, and any
  universe still up.
- `next`: the one thing to do now, such as the `launch` command for the new profile or the
  `destroy` commands for anything left running.

## Failures

Exits 0 on `created` or `validated`. Exits 1 on `failed`, with the draft at
`<name>.draft/` for a person to finish from `next` and `notes`: every submission failed
verification, or the profile needs a host variable that is not set (`next` names it). Exits 2
on `--keep` with `--no-verify` and on any other bad invocation.

Anything else prints `message` and `remedy` to stderr and exits 1. The library raises
`DigitalTwinUniverseError` with `code`:

- `project-not-found`: `--project` is not a directory.
- `docker-unavailable`: Docker is not usable; the remedy is the missing prerequisite's.
- `name-invalid`: the name, given or derived, is not a profile name.
- `profile-exists`: `<name>/` exists and `--overwrite` was not passed.
- `keep-needs-verify`: library only, `keep=True` with `verify=False`.
- `agent-provider-not-installed` or `no-agent-provider`: the remedy is the install command.
- `gh-missing` or `gh-not-signed-in`: `copilot` needs `gh` signed in with Copilot access.
- `model-invalid` or `amplifier-agent-unavailable`: `amplifier-agent` needs
  `<provider>/<model>` and that provider's credentials.
- `codex-unavailable` or `codex-not-signed-in`: `codex` needs its runtime and a Codex sign-in
  or an OpenAI API key model provider.
- `claude-unavailable` or `claude-not-signed-in`: `claude` needs its runtime and
  `ANTHROPIC_API_KEY` or a cloud provider's credentials.
- `profile-rejected`: the agent's submission was unusable even after a correction.
- `create-timeout`: the deadline passed. The tool's universe is destroyed, the message
  carries the report so far, and the draft stays.

The tool never destroys a universe the agent launched and left up; `notes` and `next` name
it. Run `digital-twin-universe list` after any failure to see what is still running.
