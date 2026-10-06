# Digital Twin Universe Smart Tool

Stands up isolated, realistic environments from a profile using Docker Compose called a "Digital Twin Universe", or DTU. Why would you want to use this?
- It gives your agents the optimized tools to test software in isolated and repeatable ways. Agents have to prove what they built or changed within a clean environment - the DTU.
- Parallelism. It allows you to work on and try things completely in parallel and isolated. For example, you can run many evaluations at once, each trying something different.
- Simulating and modeling the world. For example say we want to build an app that integrates with M365 or GitHub - we want to verify that integration with our own data easily and without hitting the real service over and over. DTUs allow you to easily override and re-route requests to a mock/simulated service running in the Compose stack.

This is a [Smart Tool](https://github.com/microsoft/amplifier-smart-tools) which is a library with a thin CLI over it and model-backed capabilities sit behind an interface so many providers can be used.
We support [GitHub Copilot SDK](https://github.com/github/copilot-sdk), [Amplifier Agent](https://github.com/microsoft/amplifier-agent), the [OpenAI Codex SDK](https://github.com/openai/codex/tree/main/sdk/python), and the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).

## Installation

Prerequisites:
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Docker](https://docs.docker.com/get-started/get-docker/)
- For the model-backed capabilities, one of:
  - `copilot` agent provider: [GitHub CLI](https://cli.github.com/) signed in to an account with a [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites).
  - `amplifier-agent` agent provider: the model provider's credentials, for instance `OPENAI_API_KEY` for the default `openai/...` model. See [providers](https://github.com/microsoft/amplifier-agent/blob/v0.20.0/docs/providers.md).
  - `codex` agent provider: the [Codex CLI](https://github.com/openai/codex) signed in with ChatGPT or an API key. See [authentication](https://developers.openai.com/codex/auth).
  - `claude` agent provider: `ANTHROPIC_API_KEY`, or any of its other supported modes of [authentication](https://code.claude.com/docs/en/agent-sdk/quickstart).
- (Optional) Install the Smart Tools skill so your agent knows about Smart Tools: `npx skills add microsoft/amplifier-smart-tools`

To get started:

```bash
# Install the Agent Skill so your agent knows about it.
npx skills add microsoft/amplifier-smart-tool-digital-twin-universe
# Install the tool, choosing the provider(s) you want
uv tool install "digital-twin-universe[all | copilot | amplifier-agent | codex | claude] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
# Check setup; if it needs fixing, this plans the fix with the agent provider. Apply it with --yes.
digital-twin-universe install
```

To use it as a library:

```bash
uv add "digital-twin-universe[all] @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
```

To update:

```bash
npx skills update digital-twin-universe   # add -g for global installs
uv tool upgrade digital-twin-universe
```

To uninstall:

```bash
npx skills remove digital-twin-universe   # add -g for global installs
uv tool uninstall digital-twin-universe
```

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
