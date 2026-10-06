---
name: digital-twin-universe
description: >-
  Stands up an isolated, realistic environment from a profile on Docker Compose so software can be cloned, installed, run, and experienced like a real user would, without touching the host. Use when passing tests on your machine is not enough evidence and code must be exercised as though actually deployed. Drive it from the command line as `digital-twin-universe`, or from Python through `digital_twin_universe.lib`. Triggers on "digital-twin-universe".
license: MIT
metadata:
  author: DavidKoleczek
  version: "0.5.2"
  repository: https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
---

# digital-twin-universe

Stands up an isolated, realistic environment from a profile on Docker Compose so software can be cloned, installed, run, and experienced like a real user would, without touching the host. Use when passing tests on your machine is not enough evidence and code must be exercised as though actually deployed.

## Install

```bash
# as a CLI, choosing the agent provider(s) you want
uv tool install "digital-twin-universe[all | copilot | amplifier-agent | codex | claude] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

# as a library, from another project
uv add "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"

# once, without installing
uvx --from "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe" digital-twin-universe --help
```

Verify with `digital-twin-universe manifest`, which needs no credentials.

## Use it

Run `digital-twin-universe --help`. It prints the tool's skill: when to use it, every capability,
worked invocations, sharp edges, and which files to read. Follow it. Confirm every argument
against `digital-twin-universe <command> --help` rather than memory, including `install` and its explicit
consent flags for Docker setup.
