---
name: amplifier-digital-twin-universe
description: >-
  Stand up isolated, realistic environments from a declarative profile so
  software can be tested as though actually deployed. Use when (1) "the tests
  pass on my machine" is not enough evidence and code must be exercised the way
  a real deployment would exercise it, (2) verifying a CLI or service installs
  and runs cleanly from scratch, (3) exercising code against mocked third-party
  services without changing its configuration, (4) driving the
  amplifier-digital-twin-universe smart tool from a library, a CLI, or an agent.
  Triggers on "digital twin", "DTU", "digital twin universe", "isolated
  environment", "incus container", "test as if deployed", "amplifier-digital-twin-universe".
license: MIT
metadata:
  author: DavidKoleczek
  version: "0.2.0"
  repository: https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
---

# Using amplifier-digital-twin-universe

A Digital Twin Universe (DTU) is a complete, isolated environment stood up on demand
from a declarative profile. It closes the gap between "the tests pass on my machine"
and "this works where it will actually run."

**The library is the tool.** `amplifier_digital_twin_universe` holds every capability.
The CLI is a thin wrapper over it and adds nothing of its own, so anything you can do
from the shell you can also do from Python.

## Before writing code

Confirm every capability, argument, and field name against `--help` or the library's
own signatures before you write it. Do not fill gaps from memory.

```bash
amplifier-digital-twin-universe --help
```

`--help` is the complete, agent-facing listing: every capability, its arguments, what
it returns, and which capabilities are model-backed. `-h` is a short summary for a
person and is deliberately not the same output. If you cannot confirm something from
`--help` or the source, say so rather than guessing.

Reference material, when `--help` is not enough:

```
docs/01-library.md   every capability as Python: signatures, parameters, returns
docs/03-cli.md       every capability as a command: flags, defaults, exit codes
docs/02-configuration.md   the environment variables this tool reads
docs/installing-incus.md   installing the runtime that launches environments
```

All four live at <https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe>.

## Install

As a CLI:

```bash
uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
```

As a library:

```bash
uv add "amplifier-digital-twin-universe @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
```

## Prerequisites

Ask the tool rather than assuming. `check` measures this host and reports what is
present, what is absent, and what each absent thing costs you.

```bash
amplifier-digital-twin-universe check
```

`incus` runs the environments; without it nothing launches. `docker` runs mock service
sidecars; without it everything works except profiles that declare sidecars. `git` is
required by the model-backed capabilities. `avahi` publishes `.local` hostnames.

Linux only. The tool does not claim platforms it has not been run on.

## The shape of the surface

Deterministic, and runnable with no provider configured:

```
manifest           the manifest as structured data
check              what this host can do right now
validate-profile   whether a profile document is launchable
launch             stand up an environment from a profile path
exec               run one command inside and capture its output
status             one environment's state and access URLs
list               every environment on this host
check-readiness    evaluate an environment's readiness checks once
update             re-run an environment's update commands in place
file-push          copy host paths in
file-pull          copy environment paths out
destroy            tear one down
```

Model-backed, and failing loudly when no provider is configured:

```
create-profile     draft a profile from a description of what to test
install            ordered steps to make this host able to launch
doctor             diagnose a symptom against measured evidence
manage             turn a request in words into deterministic actions
```

A model-backed capability reasons over measured evidence and takes minutes, up to tens
of minutes for a broad request. Give it a timeout sized for that, and poll rather than
blocking on a short one.

## Working with environments

An environment is long-lived and nothing reaps it. Destroy what you launch, and pull
anything worth keeping out first, because nothing inside survives teardown.

```bash
amplifier-digital-twin-universe launch --profile ./profile.yaml
amplifier-digital-twin-universe check-readiness --id dtu-1a2b3c4d
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "curl -sf localhost:8000/health"
amplifier-digital-twin-universe file-pull --id dtu-1a2b3c4d --source /var/log/app.log --destination ./
amplifier-digital-twin-universe destroy --id dtu-1a2b3c4d
```

`list` is scoped to the machine, not to your session, so an environment another session
launched appears there too and `destroy` will take it. Name the profile by path;
profile names that live inside the engine's own repository do not resolve from an
installed package.

`manage` plans without changing anything and runs only with `--confirmed`, which
authorizes that one invocation. Read the plan before confirming it.

## Reading the manifest

The manifest says what the tool is for and what it needs, so a caller can decide
whether to reach for it before invoking anything. It is reachable two ways.

```bash
amplifier-digital-twin-universe manifest
```

```python
from amplifier_digital_twin_universe import load_manifest

manifest = load_manifest()
manifest.name, manifest.version, manifest.platforms
manifest.to_dict()  # plain data, JSON-serializable
```

Read it through the library rather than by locating a file. Install layouts differ by
ecosystem and no filesystem path is portable across them.

## Output and failure contract

Every CLI capability writes exactly one JSON document to stdout.

A failure writes a structured envelope and exits non-zero. Never parse prose out of it,
and never treat an empty result as success.

```json
{"error": {"code": "no_provider", "message": "...", "remedy": "..."}}
```

```
0  success
2  bad invocation
3  a model-backed capability was called with no provider configured
4  a required prerequisite is missing
5  the capability ran and failed
```

A model-backed capability with no provider configured fails saying exactly that and
names what to configure. It never falls back to a degraded deterministic answer, so a
result you receive is always the result you asked for.

## Choosing a surface

Import the library from Python. Shell out to the CLI from anything that cannot import
Python in-process: a shell script, a CI job, or an agent that can run commands but not
load a Python object. Both reach the same capabilities.
