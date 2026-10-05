---
smart_tool_format: 1
name: digital-twin-universe
version: 0.4.0
description: >-
  Stands up an isolated, realistic environment from a profile on Docker Compose so software can be cloned, installed, run, and experienced like a real user would, without touching the host. Use when passing tests on your machine is not enough evidence and code must be exercised as though actually deployed
use_cases:
  - Drive a CLI such as OpenAI Codex as a real user would, with its config files and API keys provisioned, without touching the local setup
  - Run a web app against real dependencies such as Postgres and open it from the host's browser as if it were deployed
  - Install and exercise unpublished local repositories as though they were already on GitHub
  - Reproduce a failure in a disposable environment that leaves the host untouched
platforms:
  - linux
  - macos
  - windows
requires:
  - name: docker
    purpose: >-
      Every universe is a Docker Compose project. Without Docker nothing can be launched;
      `digital-twin-universe check` reports whether it is present and usable, and `digital-twin-universe install` offers
      to install it.
    install: https://docs.docker.com/get-started/get-docker/
  - name: gh
    purpose: >-
      Generates the token that signs in to GitHub Copilot, for the copilot agent provider.
      Without it, the model-backed capabilities cannot authenticate through copilot.
    optional: true
    install: https://cli.github.com/
  - name: github-copilot-subscription
    purpose: >-
      A Copilot subscription on the account signed in to gh powers the model-backed
      capabilities, for the copilot agent provider.
    optional: true
    install: https://github.com/github/copilot-cli#prerequisites
  - name: amplifier-agent-provider-credentials
    purpose: >-
      The credentials of the model provider the amplifier-agent agent provider calls, for
      instance OPENAI_API_KEY for its default model. Without them, the model-backed
      capabilities cannot run through amplifier-agent. See the full list of options at the install link.
    optional: true
    install: https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md
  - name: codex-sign-in
    purpose: >-
      The Codex CLI signed in with ChatGPT or an API key, for the codex agent provider.
      Without it, the model-backed capabilities cannot run through codex.
    optional: true
    install: https://developers.openai.com/codex/auth
---

Stands up an isolated, realistic environment from a profile on Docker Compose so software can be cloned, installed, run, and experienced like a real user would, without touching the host. Use when passing tests on your machine is not enough evidence and code must be exercised as though actually deployed.

**The library is the tool.** `digital_twin_universe.lib` holds every capability. The CLI is a thin
wrapper over it, so anything you can do from the shell you can also do from Python.

## When to reach for it

- Passing tests on your machine is not enough evidence, and the code has to run against the
  dependencies, ports, and network it is deployed with.
- A CLI has to be driven the way a person would: `exec` opens a shell in the twin as the
  provisioned user, with config files in place and API keys passed through from the host.
- Something running in the twin has to be reached from the host: exposed ports are forwarded to
  `http://localhost:<port>` and `launch` reports the URLs.
- Local repositories are not published yet, and you want to install them over `https://` as if
  they already were.
- A host must stay untouched: no DNS changes, no firewall rules, no daemons beyond Docker.

## Command surfaces

Deterministic commands run with no model provider configured. Today those are `check`,
`validate-profile`, `launch`, `list`, `status`, `exec`, `file-push`, `file-pull`, `destroy`, `dashboard`, and `mcp`; the
capability list below is authoritative. `install` is model-backed unless Docker is already usable, and
`create-profile` is model-backed; both say so in their help text. `dashboard` serves a local web page over the same library: every universe on the machine, its
URLs, and a destroy button, for a person rather than an agent. `mcp` serves the universe tools, and that page as an MCP App,
to an MCP client over stdio; `dashboard` serves the same MCP server at `/mcp`.

## Before writing code

Run `digital-twin-universe check` first. It reports whether Docker is present and usable and exits 1 with a
`remedy` per missing prerequisite when it is not. Use `digital-twin-universe install` to plan the fix;
universe commands need Docker to be usable.
Confirm every capability and argument against `digital-twin-universe <command> --help` before using it.
Do not fill gaps from memory. The library source beside this file, `lib.py`, carries the
signatures. The repository's `docs/01-library.md`, `docs/02-cli.md`, and `docs/03-profile.md`
carry the rest.

## A first universe

A profile is a Compose file with an `x-dtu` block. `launch --profile <name>` looks for
`.agents/digital-twin-universe/<name>/` in the project, then in the examples shipped under the
skill directory, so the shipped ones launch by name with nothing copied:

```bash
export GH_TOKEN="$(gh auth token)"          # the profile reads it at launch, never writes it
digital-twin-universe launch --profile copilot-cli       # prints the universe, with its id
digital-twin-universe exec --id <id> --command 'copilot --version'
digital-twin-universe exec --id <id>                     # interactive shell as the twin's user
digital-twin-universe file-push --id <id> --source ./src --destination /home/user
digital-twin-universe file-pull --id <id> --source /home/user/out.log --destination ./
digital-twin-universe status --id <id>                   # measured now; `list` shows every universe
digital-twin-universe destroy --id <id>
```

`examples/copilot-cli/` under the skill directory is that profile: GitHub Copilot CLI installed
the way its README says, as a created user, signed in with the host's token. Read it before
writing a profile of your own; `docs/03-profile.md` in the repository is the schema.
`examples/hello/` is the smallest universe, an Alpine twin with nothing installed: launch it to
try `exec` on a machine you have not used the tool on before. `examples/web-site/` is a site
opened from the host's browser at the URLs its `x-dtu.urls` names; read it before writing a
profile for a web app.

Every universe launched from this machine leaves a directory under `~/.digital-twin-universe/universes/<id>/`
until it is destroyed, and its containers keep running. Destroy what you launch.

## Writing a profile

`digital-twin-universe create-profile --description "<what the universe is for>" --project <repository>` has an agent
read the project and Docker's own documentation, write the profile under
`.agents/digital-twin-universe/<name>/`, launch it, run checks in the twin, and destroy it; the tool
then launches it again, reruns the checks, and keeps the profile only when it passes. The result names
the profile, the checks as the tool saw them, and one `next` step. It exits 1 on `failed` and leaves the
draft at `<name>.draft/` for a person to finish. Add `--keep` to leave the verified universe running
and get its id and URLs back. It costs a few minutes and a model call per attempt; for a profile you can
write from the examples, `validate-profile` and `launch` are enough.

## Install

```bash
# as a CLI
uv tool install "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

# as a library, from another project
uv add "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

# once, without installing
uvx --from "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe" digital-twin-universe --help
```

`[all]` brings every agent provider the model-backed capabilities run through. Alternatives:

```bash
# Only the GitHub Copilot agent provider
uv tool install "digital-twin-universe[copilot] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Only the Amplifier Agent agent provider
uv tool install "digital-twin-universe[amplifier-agent] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Only the Codex agent provider
uv tool install "digital-twin-universe[codex] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Deterministic capabilities only
uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
```

Verify with `digital-twin-universe manifest`, which needs no credentials. To upgrade, run
`uv tool upgrade digital-twin-universe`.

## Prerequisites

Docker is what universes are built on: `digital-twin-universe check` reports whether it is present and
usable. `digital-twin-universe install` reads the official docs at run time and plans Docker Desktop's installer
on Windows and macOS, or Docker's apt/dnf repositories on Linux. It only acts with `--yes`.

```bash
digital-twin-universe install             # show and save a sourced plan; exits 1 for planned
digital-twin-universe install --yes       # run that plan if the host facts still match
# To explicitly accept Docker Desktop's terms, use the same choice on both calls:
digital-twin-universe install --accept-license
digital-twin-universe install --yes --accept-license
```

The report's `outcome` is `ready`, `planned`, `installed`, `action-required`, or `failed`;
only `ready` and `installed` exit 0, and `installed` means a universe was launched and ran on the
new Docker, not just that `check` passes. Follow its single `next` instruction when manual action
remains. No step prompts on stdin. Partial installation is reported, not rolled back.

Deterministic capabilities need only `uv` and Docker. Model-backed capabilities run through an agent
provider, picked with `--agent-provider`, or the first installed of `copilot`,
`amplifier-agent`, and `codex` when omitted:

- `copilot`: GitHub Copilot, signed in as the GitHub CLI's user. `gh` must be installed and
  `gh auth login` completed with an account that has a Copilot subscription.
- `amplifier-agent`: [Amplifier Agent](https://github.com/microsoft/amplifier-agent), calling
  the model provider named in `--model <provider>/<model>` with that provider's credentials,
  for instance `OPENAI_API_KEY` for the default `openai/...` model. See its
  [providers](https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md).
- `codex`: [OpenAI Codex](https://github.com/openai/codex), with the user's Codex configuration.
  The Codex CLI must be signed in with ChatGPT or an API key, see
  [authentication](https://developers.openai.com/codex/auth).

Without an agent provider installed and configured, a model-backed capability fails immediately
and names what to install or configure; it never falls back to a deterministic answer.

Runs on Linux, macOS, and Windows. The twin is a Linux container everywhere by default; on
Windows a profile can opt into Windows containers.

## Straight and smart paths

Deterministic capabilities run with no provider configured. Model-backed capabilities go
through GitHub Copilot, Amplifier Agent, or Codex, whichever `--agent-provider` names, and say so in
their help text.

## Output and failure contract

Results go to stdout, diagnostics to stderr. A failure prints a message naming what went
wrong and how to fix it, and exits non-zero: 1 for a failure the tool can name, 2 for a
bad invocation. Never treat an empty result as success.

## Choosing a surface

Import the library from Python. Shell out to the CLI from anything that cannot import
Python in-process: a shell script, a CI job, or an agent that can run commands but not
load a Python object. Both reach the same capabilities.
