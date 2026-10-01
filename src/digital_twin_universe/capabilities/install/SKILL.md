`install` gets the Docker CLI, its daemon, and the Compose plugin working on this host from
Docker's official documentation. It runs `check` first: when Docker is already usable it
reports `ready` and stops, with no model call and no agent provider needed. Otherwise it
gathers facts about the host, fetches the official install pages for this platform, and has a
model turn them into one plan: Docker Desktop's own installer on Windows and macOS, Docker's
apt or dnf repository on Linux, including inside WSL 2.

Without `--yes` it only plans: the host is left as it was and the plan is reported and saved.
With `--yes` it runs the plan's unattended steps, waits for Docker to answer, and proves it by
launching the shipped `hello` universe, running `echo dtu-ok` in it, and destroying it. Nothing
prompts on stdin. A step that needs a password, a click, a restart, a new login, or license
acceptance is left to a person, and automation stops there. A partial installation is reported,
not rolled back.

Use it when `check` exits 1. Show the plan to the person who owns the host before running it
with `--yes`: its unattended steps install system packages, through `sudo -n` where they need
root.

```bash
digital-twin-universe install          # plan and save it; exits 1 with outcome planned
digital-twin-universe install --yes    # run that plan's unattended steps

# Docker Desktop, with its terms accepted; make the same choice on both calls
digital-twin-universe install --accept-license
digital-twin-universe install --yes --accept-license

# through Amplifier Agent, on another model
digital-twin-universe install \
  --agent-provider amplifier-agent \
  --model anthropic/claude-opus-5
```

```python
from digital_twin_universe.lib import install

planned = install()
planned.outcome, planned.steps, planned.docs

report = install(apply=True)
report.outcome, report.next, report.docker.ok
```

## The plan and `--yes`

The plan is saved at `~/.digital-twin-universe/install/plan.json` with a hash of the host
facts it was made for: platform, architecture, OS release, WSL, systemd, whether `sudo -n`
works, root or administrator rights, `--accept-license`, and what `check` saw. `--yes` runs the
saved plan when those facts still match. When they do not, for instance `--accept-license`
differs or Docker's state changed in between, or nothing was saved, `--yes` makes a new plan
and runs it in the same call without showing it first. `--yes` fetches the official pages and
preflights the agent provider again either way, so it needs the network and an agent provider
too.

The tool refuses a plan, and asks the model for a corrected one up to twice, when a step cites
a page it did not fetch, the method does not fit the platform, an unattended command uses
`sudo` without `-n`, the get.docker.com script, `newgrp`, `reboot`, or `shutdown`, or a command
carries `--accept-license` without `--accept-license` passed.

Each unattended step runs in a new `sh -ec` (PowerShell on Windows) with stdin closed, in a
temporary directory removed when the run ends. When one fails, the model gets one repair round
in the session the plan was made in, and its replacement steps run after the failed one. A plan
saved through another agent provider has no session to resume and gets no repair. After the
last unattended step the tool runs `check` every 2 seconds until Docker answers, a manual step
remains, or the deadline passes.

## Arguments

- `--yes`: run the plan's unattended steps. Without it, plan, save, and report only. Library:
  `apply=True`.
- `--accept-license`: explicitly accept Docker Desktop's Subscription Service Agreement, so the
  plan may pass `--accept-license` to its installer. Pass it only when the person who owns the
  host has agreed. Without it, accepting the terms is left to a person. Pass the same choice to
  the planning call and the `--yes` call, or `--yes` plans again.
- `--agent-provider`: what the agent runs through, `{{ agent_providers | join: "` or `" }}`. The
  first installed, in that order, when omitted. Unused when Docker is already usable.
- `--model`: the model that plans the install and its repair: a Copilot model id for
  `copilot`, `<provider>/<model>` for `amplifier-agent` (for instance
  `anthropic/claude-opus-5`).
  Defaults to `{{ default_intelligence_models.copilot }}` on `copilot` and
  `{{ default_intelligence_models["amplifier-agent"] }}` on `amplifier-agent`.
- `--reasoning-effort`: one of `low`, `medium`, `high`, `xhigh`, `max`. Defaults to
  `{{ default_intelligence_reasoning_effort }}`. Applies to the `copilot` agent provider only.
- `--timeout-seconds N`: the deadline for the whole run, at least 1. Defaults to 1200. It
  covers planning, downloads, installers, and waiting for Docker to start; the verification
  universe afterwards has its own 300 seconds to launch.
- `intelligence`, library only: the `Intelligence` implementation the agent runs through,
  which wins over `agent_provider`. Tests inject a fake.

## Result

`InstallReport` JSON on stdout. On stderr, `[i/n] <title> ...` before each step that runs,
including the verification.

- `outcome`: `ready` (Docker was already usable), `planned` (no `--yes`), `installed` (Docker
  answers and the verification universe ran), `action-required` (the unattended steps are done
  and a manual step remains), or `failed`.
- `summary`: one sentence on what happened or what the plan does.
- `method`: `docker-desktop-windows-per-user`, `docker-desktop-macos`, `docker-engine-apt`,
  `docker-engine-dnf`, or null when Docker was ready or no method fits.
- `steps`: each with `title`, `commands`, `source` (the official page it comes from),
  `unattended`, `status` (`pending`, `done`, `skipped`, `failed`, or `manual`), and `reason`
  (why it is manual or skipped, or for a failed step its exit code and last 20 lines of
  output). After `--yes` reaches `installed`, the last step is the verification universe.
- `next`: the one instruction for the person: `nothing` on `ready` and `installed`,
  `rerun with --yes` on `planned`, the plan's own instruction on `action-required`, and to read
  the failed step's `reason` on `failed`.
- `notes`: the model's notes on where it departed from the pages and what that means, where the
  plan was saved, and why no repair ran when a step failed without a session to repair in.
- `docs`: the official pages the plan was made from.
- `docker`: the `check` report, measured again after the steps ran.

## Failures

Exits 0 on `ready` or `installed`. Exits 1 on `planned`, `action-required`, or `failed`, with
the report on stdout. `failed` means an unattended step failed again after the repair round, or
failed with no session to repair in, or Docker answers but the verification universe did not
run; the failed step's `reason` says why. A bad invocation exits 2.

Anything else prints `message` and `remedy` to stderr and exits 1. None of these are raised
when Docker is already usable. The library raises `DigitalTwinUniverseError` with `code`:

- `agent-provider-not-installed` or `no-agent-provider`: the remedy is the install command.
- `gh-missing` or `gh-not-signed-in`: `copilot` needs `gh` signed in with Copilot access.
- `model-invalid` or `amplifier-agent-unavailable`: `amplifier-agent` needs
  `<provider>/<model>` and that provider's credentials.
- `docs-unreachable`: an official page could not be fetched, redirected away from
  docs.docker.com or learn.microsoft.com, or was not Markdown. The remedy lists the pages to
  read by hand.
- `plan-rejected`: the model returned an error or no acceptable plan after two corrections, a
  repair plan was refused (the message carries the report so far), the saved plan cannot be
  read or no longer fits this host's pages, or the plan cannot be saved.
- `install-timeout`: the deadline passed. A step still running is killed with everything it
  started, the remaining steps are reported `skipped`, and the message carries the report so
  far with outcome `failed`.

Run `digital-twin-universe check` after any failure to see what Docker state the host is in.
