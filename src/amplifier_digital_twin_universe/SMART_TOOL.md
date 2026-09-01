---
smart_tool_format: 1
name: amplifier-digital-twin-universe
version: 0.2.0
description: >
  Stands up isolated, realistic environments from a declarative profile so software
  can be tested as though actually deployed, and manages them for their whole life:
  author a profile, launch it, run commands inside, copy files in and out, update it,
  and tear it down. Use when "tests pass locally" is not enough evidence and you need
  to exercise code the way a real deployment would.
use_cases:
  - Turn a description of what you want to test into a launchable environment profile
  - Test a web app in a container that mirrors its real deployment
  - Verify a CLI tool installs and runs cleanly from scratch
  - Simulate an end-user environment without touching production
  - Exercise code against mocked third-party services without changing its configuration
  - Work out why a host cannot launch environments, and what to run to fix it
platforms:
  - linux
requires:
  - name: incus
    purpose: Runs the environments a profile describes. Without it, profiles can still be authored and validated, but nothing launches.
    optional: true
    install: docs/installing-incus.md
  - name: git
    purpose: Fetches the agent engine's modules. The model-backed capabilities cannot run without it.
    install: https://git-scm.com/downloads
  - name: docker
    purpose: Runs mock service sidecars beside an environment. Without it, profiles declaring sidecars cannot launch; everything else does.
    optional: true
    install: docs/installing-docker.md
  - name: avahi
    purpose: Publishes an environment as a .local hostname. Without it, environments are still reachable at localhost on their mapped ports.
    optional: true
    install: docs/installing-incus.md
---

# amplifier-digital-twin-universe

A Digital Twin Universe (DTU) is a complete, isolated environment stood up on demand
from a declarative profile. It closes the gap between "the tests pass on my machine"
and "this works where it will actually run."

## When to reach for it

- What you want to verify depends on its environment: a service that must resolve real
  hostnames, a CLI that must install from scratch, a web app whose behavior differs
  behind a proxy.
- You need a throwaway machine you can provision, exercise, and delete without touching
  anything you care about.
- A host will not launch environments and the error alone does not say why.

Not for pure logic you can test in-process. Standing up an environment costs seconds to
minutes; a function call costs microseconds.

## Straight and smart paths

`manifest`, `check`, `validate-profile`, `launch`, `exec`, `status`, `list`,
`check-readiness`, `update`, `file-push`, `file-pull`, and `destroy` are deterministic
and run with no model provider configured.

`create-profile`, `install`, `doctor`, and `manage` are model-backed. They consume
tokens, may answer differently on a second run, and fail saying so when no provider is
configured rather than returning a lesser answer.

## Worked invocations

```bash
# what this host can do right now, and what it is missing
amplifier-digital-twin-universe check

# ordered steps to make this host able to launch
amplifier-digital-twin-universe install --goal "test a web service"

# describe what you want to test; get a profile that parses
amplifier-digital-twin-universe create-profile \
  --description "a FastAPI app on port 8000 with a /health endpoint" --out profile.yaml

# stand it up, exercise it, take the results out, delete it
amplifier-digital-twin-universe launch --profile profile.yaml
amplifier-digital-twin-universe exec --id dtu-1a2b3c4d --command "curl -sf localhost:8000/health"
amplifier-digital-twin-universe file-pull --id dtu-1a2b3c4d --source /var/log/app.log --destination ./
amplifier-digital-twin-universe destroy --id dtu-1a2b3c4d

# work out what is wrong, in your own words
amplifier-digital-twin-universe doctor --symptom "the container has no outbound network"

# plan several actions from one request, then run them
amplifier-digital-twin-universe manage --request "tear down every stopped environment"
amplifier-digital-twin-universe manage --request "tear down every stopped environment" --confirmed
```

## Sharp edges

- Environments are ephemeral, and nothing reaps them. Anything worth keeping leaves
  through `file-pull` before `destroy`, and an environment nobody destroys runs until
  the host fills up. `launch` refuses past 15 concurrent environments; set
  `AMPLIFIER_DTU_MAX_ENVIRONMENTS` to move the ceiling.
- `list` is scoped to the machine, not to a session. An environment another session
  launched appears there too, and `destroy` will take it.
- A profile is named by path. Profile names that live inside the engine's own
  repository do not resolve from an installed package.
- `manage` plans without changing anything. It runs only with `--confirmed`, which
  authorizes one invocation and nothing else.
- `install` and `doctor` propose; they never install, configure, or repair anything.
- `exec` captures output and returns it. For an interactive shell inside an
  environment, use the engine's own `amplifier-digital-twin exec`.
- The model-backed capabilities send host evidence, and whatever context you pass, to
  the model provider.

The library, its configuration, and the full CLI surface are documented in the
repository's `docs/` directory.
