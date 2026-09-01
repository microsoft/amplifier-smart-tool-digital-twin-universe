# Development

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [git](https://git-scm.com/downloads)
- [Incus](installing-incus.md), to run anything that touches `environments.py`
  against a live host.

## Setup

```bash
uv sync --all-groups
```

## Checks

```bash
uv run ruff format .
uv run ruff check .
uv run pytest -q
```

## Smart-tools conformance

CI clones the spec's conformance kit outside this checkout and runs it
against the installed console script. To run it locally:

```bash
git clone --depth 1 https://github.com/microsoft/amplifier-smart-tools /tmp/amplifier-smart-tools
export PATH="$PWD/.venv/bin:$PATH"
uv run --no-project /tmp/amplifier-smart-tools/conformance/run.py .
```

`uv run --no-project` runs the kit from its own inline script metadata, not
this project's dependency set. The kit never installs the tool under test,
so the console script must already resolve on `PATH`.
