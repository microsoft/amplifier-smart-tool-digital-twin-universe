# Agent Conventions

## Highest authority

[docs/00-vision.md](docs/00-vision.md) is the highest-authority document in
this repository. When a change conflicts with it, the vision wins; raise the
conflict rather than silently overriding it.

## The library is the tool

Every capability lives in `amplifier_digital_twin_universe`. No capability
may exist only in the CLI (`cli.py`, `catalog.py`). Add a new capability to
the library first, export it from `__init__.py`, then wire the CLI to it.

## The profile schema is not ours

The DTU profile schema, its loader, and its launch semantics are owned by
`amplifier_bundle_digital_twin_universe`. `profiles.py` and `environments.py`
delegate to it. Never reimplement or partially re-parse the schema here; a
second answer to a question the engine already answers is exactly how
`validate-profile` and `launch` drift apart.

## Changes to environments.py need a live host

`environments.py` wraps real Incus lifecycle calls. Unit tests with mocks are
not sufficient evidence that a change works. Any change here needs a real
`launch` against a live Incus host, exercised through at least `launch`,
`run`, `check-readiness`, and `destroy`, before it is considered verified.

## Model-backed paths never degrade

`create_profile`, `plan_install`, `diagnose`, and `manage` are model-backed.
When no model provider is configured, they raise `NoProviderError` naming
the remedy. They must never fall back to a deterministic approximation and
return it as if it were the real answer; a lesser result returned silently
is a caller misled about what it received.

## Manifest and package version move together

`version` frontmatter in `SMART_TOOL.md` and `version` in
`pyproject.toml` describe the same release. Bumping one without the other
leaves `load_manifest()` reporting a version the package does not match.
Change both in the same commit.

## Writing style for durable output

Applies to docs, docstrings, comments, and error messages:

- No em dashes.
- Prefer code blocks over tables; no tables.
- No status language: no "currently", "for now", "not yet", "coming soon",
  "TODO", "planned". Write the finished version. Unbuilt ideas go only in
  [docs/ROADMAP.md](docs/ROADMAP.md).
- Never describe the format inside an instance of the format.
- Be concise and dense. Do not pad.
- Declarative, present tense, third person.
