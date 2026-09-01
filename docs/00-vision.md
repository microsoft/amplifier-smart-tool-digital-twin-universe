# Vision

Digital Twin Universe is a [Smart Tool](https://github.com/microsoft/amplifier-smart-tools)
that stands up isolated, realistic environments from a declarative profile, so
software can be tested as though actually deployed, and manages them for their
whole life.

## Goals

- Stand up an environment from a profile, exercise it, and tear it down, as
  library calls with a thin CLI wrapper on top.
- Wrap the existing Digital Twin Universe engine
  (`amplifier_bundle_digital_twin_universe`) and never reimplement its profile
  schema or launch behavior. Validation delegates to the engine's own loader,
  so a profile that validates here parses identically at launch.
- Deterministic paths (`manifest`, `check`, `validate-profile`, `launch`,
  `exec`, `status`, `list`, `check-readiness`, `update`, `file-push`,
  `file-pull`, `destroy`) run with no model provider configured.
- Model-backed paths (`create-profile`, `install`, `doctor`, `manage`) fail
  naming the remedy when no model provider is configured, rather than
  degrading to a lesser deterministic answer.
- The model proposes; deterministic code decides and executes. A drafted
  profile is validated by the engine's loader before acceptance. A `manage`
  plan is built from a fixed registry of deterministic actions the model can
  select and parameterize, never invent.
- `install` and `doctor` propose. They never install, configure, or repair
  anything themselves.
- `manage` requires per-invocation confirmation to mutate anything. Without
  `--confirmed` a plan changes nothing; confirmation authorizes exactly one
  invocation, never a session.

## Non-Goals

- Reimplementing the engine's schema, provisioning, or networking behavior.
- Reaping or garbage-collecting environments automatically.
- An interactive shell into an environment; that is the engine's own
  `amplifier-digital-twin exec`.
- Ideas that might become goals but are not built are tracked in
  [ROADMAP.md](ROADMAP.md), not here.

## Principles

- The library is the tool. Every capability lives in
  `amplifier_digital_twin_universe`; the CLI adds nothing of its own.
- The profile schema is owned by the upstream engine, never duplicated here.
- Failures carry a stable `code` to branch on and a `remedy` to act on.
