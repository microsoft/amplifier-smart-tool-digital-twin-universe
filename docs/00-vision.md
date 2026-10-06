# Vision

By default, AI generated software is verified in the environment it was built in. 
Agents claim it worked because the context says so, issues stay unsolved because nothing forced them to consider deployment details, and setup that happens to exist on the dev machine papers over what a real user would hit. 
There is a gap between "tests pass" and "this actually works in the real world."

Digital Twin Universe closes that gap: a complete, isolated environment, stood up on demand from a profile on Docker Compose, that simulates the world the code will live in, 
so it can be cloned, installed, run, and experienced like a real user would. 
It answers "what will reality be like if this were actually deployed?" without touching the host.

Example uses:

- "I want to try out a CLI like OpenAI Codex as a real user would, with its config and API keys provisioned for me, without touching my local setup."
- "I want to run my web app against a real Postgres and open it in my browser, as if it were deployed."
- "I want to install my unpublished repositories as if they were already on GitHub, and see whether the install actually works."

It takes the learnings from [amplifier-bundle-digital-twin-universe](https://github.com/microsoft/amplifier-bundle-digital-twin-universe/) and [amplifier-bundle-gitea](https://github.com/microsoft/amplifier-bundle-gitea) and rebuilds them on Docker Compose, so anyone with Docker gets the same experience on Windows, macOS, and Linux. 
It is a fresh design rather than a port: it shares no profile schema, engine, or state with amplifier-bundle-digital-twin-universe.

## What a universe is

A universe is one Docker Compose project. Every part of it is a service in the same `compose.yaml`:

- The twin: the container the code under test runs in, provisioned from the profile so that it looks like a real user's machine: packages installed, config files in the correct places, and API keys passed through from the host's environment at launch rather than written into the rendered files.
- Dependencies the profile declares, such as a database or a cache.
- Gitea, when the profile publishes local repositories, so they can be cloned and installed over `https://` as if they were on GitHub.
- A gateway, when the profile rewrites URLs or restricts outbound traffic. It is the twin's only route out. 
  - It terminates TLS for rewritten hosts, routes matching `host/path` prefixes such as `github.com/org/repo` to Gitea, tunnels everything else to the real internet untouched, and enforces the allowlist.

The gateway mints a certificate authority the first time it starts. 
The tool owns getting that CA trusted in every image where code runs: it is mounted into every service, installed into the system store at start, and named in the environment variables that git, pip, uv, curl, requests, and Node each consult. 
Trust is a property of the universe, not something each client configures.

A profile with no rewrites and no allowlist renders no gateway, no CA, and no proxy environment. 
The twin sits on one ordinary network with direct internet access, and published repositories are reachable at Gitea's own address.

All of this lives on a private Compose network. Nothing on the host is modified: no DNS changes, no firewall rules, no daemons beyond Docker itself.

The universe is reachable from the host in two ways. 
Ports the profile exposes are forwarded to localhost, so a web app in the twin opens in the host's browser at `http://localhost:<port>` and `launch` reports those URLs. 
`exec` runs a command or an interactive shell inside the twin as the provisioned user, so a CLI like OpenAI Codex can be driven exactly as a person would, with its config and keys already in place.

On Linux and macOS the twin is a Linux container. 
On Windows it is a Linux container by default, and a profile can opt into Windows containers when the software under test needs Windows itself, such as a `.exe` installer or PowerShell-first tooling. 
Both modes render the same kind of `compose.yaml` and answer to the same commands.

## What this adds to Docker Compose

Compose already runs multi-service stacks, so the value is in what an agent would otherwise have to build or remember each time:

- Local code as if released, out of the box. Name a repository on disk and the URL it will live at, and `git clone`, `pip install`, and `uv tool install` inside the universe fetch the local checkout from that URL. The git server, gateway, TLS termination, CA minting, and trust wiring come with the tool; nobody assembles them by hand.
- Real hosts replaced by services you control. The same gateway routes any `host/path` to a Compose service, so a mock of `api.openai.com` in the profile answers at the real URL, TLS included, and the code under test needs no configuration to talk to it.
- A place to look. Profiles live at `.agents/digital-twin-universe/<profile-name>/` in a project, so an agent finds them, and `launch --profile <profile-name>` resolves there. Any Compose file or Dockerfile elsewhere, such as an evaluation task's environment, launches by path, and adapts fully by adding an `x-dtu` block.
- Identity. Every universe has an id; `list`, `status`, `exec`, `file-push`, `file-pull`, and `destroy` take it. No project names, file paths, or service names to remember, and the twin is always the target.
- Failures that name the fix. Every command says what went wrong and what to do about it, in the same terms on every OS. Compose reports for a person reading a terminal; an agent needs the cause and the remedy stated.
- Checked before it runs. `validate-profile` runs Compose's own validation and then the invariants of a universe: a twin exists, nothing routes around the gateway, served paths are git repositories. Warnings point out where a profile is less realistic than it means to be.
- Ready means ready. `launch` waits on every healthcheck and reports the URLs the host can open, so a following `exec` never races the stack.
- Prerequisites owned. `check` says whether Docker is usable and `install` offers to make it so, on each platform.
- Help when it breaks. `create-profile` writes a profile from a description, and `doctor` reads the universe's logs and state to name what is wrong.

## Validating Digital Twin Universe with itself

Digital Twin Universe is held to its own standard: it is verified inside a universe, as a real user would install and run it, not on the machine it was developed on. 

A profile can ask for a Docker daemon inside the twin. 
The twin then runs a nested daemon (Docker-in-Docker) and any universe launched from within it lives entirely inside that daemon: the host's Docker sees one privileged container, nothing else. 
The self-validation profile publishes this repository through the universe's Gitea and rewrites its GitHub URL, so `uv tool install git+https://github.com/...` inside the twin installs the local checkout as if it were released. 
`exec` then drives `digital-twin-universe check`, `launch`, `exec`, and `destroy` against a sample profile, and a port the sample exposes is chained out through the twin so the host's browser reaches the inner universe.
The profile is `.agents/digital-twin-universe/self-validation/` in this repository, and `tests/test_live_self_validation.py` runs the whole loop.

Constraints this sets:

- The nested daemon is opt-in per profile, never on by default. It requires a privileged twin, since Docker alone offers no unprivileged path, and a privileged twin weakens the container boundary; the profile says so where it asks for it.
- Only a Linux twin can host a nested daemon. Windows containers cannot.
- `launch` inside the twin reports `localhost` URLs that are true inside the twin; the outer profile must expose the same port for the host to reach them.
- The inner daemon starts with an empty image cache, so the sample profile pulls its images on first use unless the profile keeps `/var/lib/docker` on a named volume.

## Goals

- Docker is the only prerequisite. `check` reports whether it is present and usable. `install` reads the official docs at run time and plans Docker Desktop's installer on Windows and macOS, or Docker's apt/dnf repositories on Linux. It only acts with explicit consent (`--yes`). If the system does not allow it, `install` says exactly what to do by hand.
- One interface on every OS. Every command takes the same flags and returns the same JSON on Windows, macOS, and Linux. Platform differences are absorbed inside the tool, never surfaced to the caller.
- From zero to a running universe in one command after Docker is present: `launch --profile <profile-name>`, resolving under `.agents/digital-twin-universe/`, or `launch --profile path/to/compose.yaml`.
- The profile is a Compose file. The twin, its dependencies, ports, environment, and healthchecks are ordinary Compose services, so nothing is lost in translation and anyone who knows Compose can write one. What Compose cannot say, which service is the twin, which local repositories to publish, and what the gateway rewrites or allows, lives in an `x-dtu` block that plain `docker compose` ignores. The tool renders an overlay file beside it, with Gitea, the gateway, and the trust plumbing, and runs both together.
- Windows containers are an option, not a requirement. A profile on Windows can ask for a Windows twin; everything else about the tool stays the same.
- A dashboard, in the style of [mybench-smart-tool](https://github.com/DavidKoleczek/mybench-smart-tool), shows every universe on the machine, what it exposes, and its logs, and lets a person open, shell into, or destroy one without remembering ids.
- The same command set as the Digital Twin Universe smart tool, so agents and skills written against it need only relearn the profile. Deterministic commands run with no model provider configured:

```bash
digital-twin-universe check
digital-twin-universe validate-profile --file profile.yaml
digital-twin-universe launch --profile profile.yaml
digital-twin-universe list
digital-twin-universe status --id <id>
digital-twin-universe exec --id <id> --command "curl -sf localhost:8000/health"
digital-twin-universe exec --id <id>
digital-twin-universe file-push --id <id> --source ./src --destination /workspace
digital-twin-universe file-pull --id <id> --source /var/log/app.log --destination ./
digital-twin-universe destroy --id <id>
```

Smart commands are model-backed and say so in their help text:

```bash
digital-twin-universe install
digital-twin-universe create-profile --description "a FastAPI app on port 8000 using Postgres"
digital-twin-universe doctor --symptom "the twin cannot reach the database"
```

Serve commands offer the same library as a local web UI, and as an MCP server whose dashboard renders in MCP clients as an MCP App:

```bash
digital-twin-universe dashboard
digital-twin-universe mcp
```

- Every result is one JSON document on stdout. Failures carry a stable `code` and a `remedy`.
- Nothing is hidden. The profile is a Compose file and the rendered overlay is a Compose file. Both can be read, and both can be run with `docker compose` by hand, after `docker buildx bake` for images built through the gateway.

## Non-Goals

- Compatibility with amplifier-bundle-digital-twin-universe's profiles, engine, or Incus-based environments.
- VM-level isolation or kernel fidelity. A twin is a container; software that needs its own kernel, systemd as PID 1, or nested virtualization is out of scope. A nested Docker daemon is not virtualization and is in scope as an opt-in.
- Running universes on remote or multiple hosts.
- Managing Docker beyond installing it and confirming it works.

## Principles

- If it needs more than Docker, it does not ship.
- Render, then run. The tool never drives containers through hidden state; every universe is fully described by files on disk that `docker compose` understands.
- Host untouched by universes. Rewriting, publishing, proxying, and certificate trust all happen inside the universe; only `install --yes` changes the host to set up Docker.
- The library is the tool. The CLI and any other surface are thin wrappers over it.
- Deterministic capabilities run with no model provider configured, and never refuse to load without one.
- The intelligence is behind an interface, so another implementation is a new module rather than a rewrite.
- The tool works on Windows, macOS, and Linux seamlessly.
