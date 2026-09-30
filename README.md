# Digital Twin Universe Smart Tool

Stands up an isolated, realistic environment from a profile on Docker Compose so software can be tested as though actually deployed. Use when passing tests on your machine is not enough evidence and code must run against real dependencies, published local repositories, and rewritten URLs without touching the host.

Digital Twin Universe is a [Smart Tool](https://github.com/microsoft/amplifier-smart-tools): a library with a thin CLI over it, whose model-backed capabilities sit behind an interface.
They run through an agent provider, the [GitHub Copilot SDK](https://github.com/github/copilot-sdk) or [Amplifier Agent](https://github.com/microsoft/amplifier-agent).

## Installation

Prerequisites:
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [Docker](https://docs.docker.com/get-started/get-docker/), which every universe runs on.
- For the model-backed capabilities, one of:
  - `copilot` agent provider: [GitHub CLI](https://cli.github.com/) signed in to an account with a [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites).
  - `amplifier-agent` agent provider: the model provider's credentials, for instance `OPENAI_API_KEY` for the default `openai/...` model. See [providers](https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md).

`digital-twin-universe install` gets Docker working: review the plan, then use `digital-twin-universe install --yes` to apply it.

```bash
uv tool install "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
```

To use it as a library:

```bash
uv add "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
```

To run it once without installing:

```bash
uvx --from "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe" digital-twin-universe --help
```

`[all]` brings both agent providers the model-backed capabilities run through. Alternatives:

```bash
# Only the GitHub Copilot agent provider
uv tool install "digital-twin-universe[copilot] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Only the Amplifier Agent agent provider
uv tool install "digital-twin-universe[amplifier-agent] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Deterministic capabilities only
uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
```

To teach a coding agent how to use it, install the [skill](skills/digital-twin-universe/SKILL.md):

```bash
npx skills add microsoft/amplifier-smart-tool-digital-twin-universe
```

To update:

```bash
uv tool upgrade digital-twin-universe
npx skills update digital-twin-universe   # add --global if the skill was installed globally
```

To uninstall:

```bash
uv tool uninstall digital-twin-universe
npx skills remove digital-twin-universe   # add --global if the skill was installed globally
```

Verify an install with `digital-twin-universe manifest`, which needs no prerequisites and no credentials.

## Interface

```bash
# Print the tool's manifest as JSON
digital-twin-universe manifest

# Report whether this host can run a universe: Docker CLI, daemon, and Compose plugin
digital-twin-universe check

# Plan Docker setup from official docs (model-backed); add --yes to act
digital-twin-universe install

# Write a profile from a description and the project, and prove it by launching it (model-backed)
digital-twin-universe create-profile --description "a FastAPI app on port 8000 using Postgres" --project .

# The same through Amplifier Agent on another model
digital-twin-universe create-profile --description "a FastAPI app on port 8000 using Postgres" --project . --agent-provider amplifier-agent --model anthropic/claude-opus-5

# Launch a universe from a profile name or Compose file and wait until it is ready
digital-twin-universe launch --profile <name-or-path>

# Every universe on this machine, or one measured now
digital-twin-universe list
digital-twin-universe status --id <id>

# Run a command in the twin, or open a shell in it
digital-twin-universe exec --id <id> --command "<command>"
digital-twin-universe exec --id <id>

# Copy files in and out of the twin
digital-twin-universe file-push --id <id> --source ./src --destination /workspace
digital-twin-universe file-pull --id <id> --source /var/log/app.log --destination ./

# Take it down
digital-twin-universe destroy --id <id>

# A web page showing every universe on this machine; prints its URL and its MCP server's
digital-twin-universe dashboard

# The universe tools and the dashboard as an MCP server on stdio, for MCP clients
digital-twin-universe mcp
```

Hosts that support MCP Apps render the dashboard in the chat. A client that connects over HTTP instead can use the `mcp_url` that `digital-twin-universe dashboard` prints.

Profiles live under `.agents/digital-twin-universe/<name>/` in a project; the tool also ships ready-to-launch [examples](src/digital_twin_universe/examples). See the [profile reference](docs/03-profile.md).

See the [CLI reference](docs/02-cli.md) for every flag and the [library reference](docs/01-library.md) for the Python surface.

## Contributing

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for details on how to set up your development environment.
