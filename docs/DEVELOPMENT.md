# Development

## Development Setup

### Prerequisites

Install:

- [Git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/): Manages Python environments
- [prek](https://github.com/j178/prek): Used for precommit hooks. Recommended to install through PyPI/uv with `uv tool install prek`. Use `uv tool upgrade prek` to update it.
- [GitHub CLI](https://cli.github.com/) for intelligence features through the `copilot` agent provider.
- [Node.js](https://nodejs.org/) 22+ and [pnpm](https://pnpm.io/installation), optional: only to change the dashboard's frontend. The compiled dashboard is committed, so running it needs neither.
- [GitHub Copilot subscription](https://github.com/github/copilot-cli#prerequisites) for intelligent features through the `copilot` agent provider.
- [Model provider credentials](https://github.com/microsoft/amplifier-agent/blob/v0.25.0/docs/providers.md), such as `OPENAI_API_KEY`, for intelligent features through the `amplifier-agent` agent provider.
- [Codex CLI](https://github.com/openai/codex) [signed in](https://developers.openai.com/codex/auth) with ChatGPT or an API key, for intelligent features through the `codex` agent provider.
- [`ANTHROPIC_API_KEY` or a cloud provider's credentials](https://code.claude.com/docs/en/agent-sdk/quickstart), for intelligent features through the `claude` agent provider.

### Initial Setup

1. Clone this repository and change into it.

1. Run the development installation script (sets up the uv env, precommit hooks, and the `reference/` clones):

   ```bash
   uv run setup-for-dev.py
   ```

### Essential Development Commands

*Commands should be run from the repository root, unless otherwise specified.*

#### Precommit hooks

Setup precommit hooks:

```bash
prek install
```

Run precommit hooks manually:

```bash
prek run --all-files
```

#### Python Library Development

Create uv virtual environment and install dependencies:

```bash
uv sync --frozen --all-extras --all-groups
```

To update dependencies, follow [Updating Dependencies](#updating-dependencies).

Lint code:

```bash
uv run ruff check --fix --config pyproject.toml
```

Format code (also formats code blocks in .md files):

```bash
uv run ruff format --config pyproject.toml
```

Type check:

```bash
uv run ty check .
```

Run tests:

```bash
uv run pytest
```

This runs everything that does not launch a universe or call a model, in a few seconds on up to eight xdist workers. It needs Docker for `docker compose config`. The slow tests carry a marker that says which changes call for them (`markers` in `pyproject.toml`). Run the ones a change touches:

```bash
# One group of slow tests, or every test (about a minute and a half without `model`)
uv run pytest -m overlay
uv run pytest -m ""
uv run pytest -m "not model"

# One slow file, or one test in it; -m "" lifts the default deselection
uv run pytest -m "" tests/test_live_universe.py
uv run pytest -m "" tests/test_live_overlay.py -k working_tree

# In-process, for a debugger or -s
uv run pytest -n0 -m "" tests/test_live_universe.py -k web_site -s
```

Each worker hands out host ports from its own range (`free_port` in `tests/conftest.py`), so parallel launches never collide on a port.

Run the deterministic capabilities on an install without any agent provider, then restore the full environment:

```bash
uv sync --no-extra copilot --no-extra amplifier-agent --no-extra codex --no-extra claude
uv run digital-twin-universe manifest
uv run pytest
uv sync --all-extras --all-groups
```

#### Dashboard Development

`dashboard/` holds two builds:

- The MCP App view, a Vite+TS+React app with entry `mcp_app.html`, compiled to the single file `src/digital_twin_universe/adapters/static/mcp_app.html` that the MCP server serves.
- The dashboard's host page and sandbox proxy, plain TS in `dashboard/host/`, compiled to `src/digital_twin_universe/capabilities/dashboard/static/`. They render the view the same way any MCP Apps host does.

Both are committed, because the tool installs from this git repository and must run with no Node present.
The `dashboard` precommit hook runs `uv run build-dashboard.py` whenever frontend source changes: with pnpm present it installs, lints, formats, type checks, and recompiles; without pnpm it says so and passes, since a Python-only change cannot have touched the frontend.

Install dependencies (also done by `setup-for-dev.py` when pnpm is present):

```bash
pnpm --dir dashboard install --frozen-lockfile
```

Run the dashboard, then rebuild whichever side changed:

```bash
uv run digital-twin-universe dashboard --port 5199

# Host page or sandbox: reload the browser, since they are read per request
pnpm --dir dashboard build

# View: restart the dashboard, since the MCP server reads the view once when it starts
pnpm --dir dashboard build:view
```

Lint, format, and type check:

```bash
pnpm --dir dashboard check
```

Check and compile both into the package, then commit them:

```bash
uv run build-dashboard.py
```

#### Updating Dependencies

Every dependency has a 7 day cooldown: `exclude-newer = "7 days"` under `[tool.uv]` in `pyproject.toml`, and `minimumReleaseAge: 10080` (minutes) in `dashboard/pnpm-workspace.yaml`.
An update never applies a major version upgrade. Collect each one, check whether its peers allow it, and report it at the end.

##### Dependabot

1. List the open Dependabot alerts and pull requests:

   ```bash
   gh api repos/microsoft/amplifier-smart-tool-digital-twin-universe/dependabot/alerts \
     --jq '.[] | select(.state == "open") | [.dependency.package.name, .dependency.manifest_path, .security_vulnerability.first_patched_version.identifier] | @tsv'
   gh pr list --author app/dependabot
   ```

1. Do not merge the pull requests. Each one changes a lock file or workflow, so they conflict with each other and with the update, which moves the same packages. Dependabot closes them, and the alerts, once the merged lock files no longer hold the vulnerable versions.

##### Python

1. Runtime dependencies and extras carry a cap at the next major, for example `"mcp>=2.3,<3.0"`, so we never auto upgrade to a new major. A 0.x package treats its minor as the major, so its cap is the next minor, for example `"fastapi>=0.141.1,<0.142.0"`. Dev dependencies need no cap; keep the ones they have.
1. For each PyPI dependency in `[project.dependencies]`, `[project.optional-dependencies]`, and `[dependency-groups]`, find the latest version. Read `https://pypi.org/pypi/<name>/json` and take the newest non-prerelease, non-yanked release whose earliest file upload is at least 7 days old. Do not trust the top of the PyPI history page; it ignores the cooldown.
1. Raise each floor that is behind to that version, keeping the form the entry already uses (`>=2.13,<3.0` stays two-part, `>=0.27.2,<0.28.0` stays three-part). Never raise a cap: a latest version at or past the cap is a major upgrade to report.
1. Move `amplifier-agent`, the one git dependency, to its newest release tag, from `gh release list -R microsoft/amplifier-agent -L 1`. It has no cooldown. It is pinned exactly, so it moves even across a 0.x minor. Its tag is also in the providers documentation links; replace every match of `git grep <old-tag>`.
1. Update `uv_build` under `[build-system]` to its latest version at least 7 days old, moving its cap with it. It is the one exception to the cap rule.
1. `exclude-newer-package` exempts a package from the cooldown for a security fix. Remove each entry whose comment condition now holds.
1. Run `uv lock --upgrade`. It respects the cooldown and the caps, and moves transitive dependencies too. Do this even when `pyproject.toml` did not change.
1. Run `uv tree --outdated` and read every `(latest: ...)`. Each is a major held back by our cap, or a transitive dependency held back by a dependent's range, which `uv tree --invert --package <name>` names. Report each.
1. Run `uv sync --frozen --all-extras --all-groups`.
1. Run `uv build --wheel -o temp/dist` and confirm the wheel still holds `SMART_TOOL.md`, `capabilities/dashboard/static/`, and `adapters/static/mcp_app.html`. Delete `temp/dist` after.
1. `.pre-commit-config.yaml` only has local hooks, so it has no hook versions to bump. If a remote hook repository is ever added, bump its `rev` to its latest release the same way.

##### Dashboard

1. Run `pnpm --dir dashboard outdated`. Its Latest column already respects the cooldown.
1. Note every row whose Latest is a new major (for example `typescript` 6 to 7). For each, check its peers with `pnpm view <package>@<version> peerDependencies`, and the peer ranges of the packages that depend on it, so the report can say whether it is possible today. A common blocker is `typescript-eslint` capping `typescript`.
1. Update everything else, excluding each major by name:

   ```bash
   pnpm --dir dashboard update --latest '!<major-1>' '!<major-2>'
   ```

   Read the output for peer dependency warnings, especially across the React, Vite, and ESLint packages.
1. Run `pnpm --dir dashboard update <package>` for each excluded major, so it still moves to the newest release within its current range.
1. If `dashboard/pnpm-workspace.yaml` has `overrides`, make sure each still matches the updated versions, then run `pnpm --dir dashboard install`.
1. Set `packageManager` in `dashboard/package.json` to the newest pnpm release in its major that is at least 7 days old, from `pnpm view pnpm time --json`.
1. Run `uv run build-dashboard.py`. It installs from the lock file, lints, formats, type checks, and compiles the host page and sandbox into `src/digital_twin_universe/capabilities/dashboard/static/` and the view into `src/digital_twin_universe/adapters/static/mcp_app.html`. Those compiled files are committed, so they are part of the change.

##### GitHub Actions

1. For each `uses:` in `.github/workflows/*.yml`, find the newest release in its current major that is at least 7 days old. The list is not in version order, since some actions also release older majors:

   ```bash
   gh release list -R <owner>/<repo> --exclude-pre-releases --json tagName,publishedAt --jq '.[] | [.tagName, .publishedAt] | @tsv'
   ```

1. Pin it by the full commit SHA of its tag, with the tag as a comment, for example `actions/checkout@<sha> # v7.0.1`. Use the same SHA in every workflow:

   ```bash
   gh api repos/<owner>/<repo>/commits/<tag> --jq .sha
   ```

##### Finish

1. Run `prek run --all-files` and `uv run pytest`; both must pass.
1. Run the Dependabot alert command again. Each alerted package must now be at or above its patched version in `uv.lock` or `dashboard/pnpm-lock.yaml`.
   - If the cooldown holds back the fix, exempt that package, with a comment saying when to remove it: `exclude-newer-package` under `[tool.uv]`, or `minimumReleaseAgeExclude` in `dashboard/pnpm-workspace.yaml`.
   - If the fix is a new major of a transitive dependency that its dependents do not allow yet, report it with the other majors.
   - If there is no patched version, report it with what pulls it in, from `pnpm --dir dashboard why <package>` or `uv tree --invert --package <package>`.
1. Report what moved on each side, every major upgrade held back with whether its peers allow it, and which Dependabot alerts remain.

#### References

`reference/` holds gitignored clones of the repositories worth reading while developing this tool. `uv run setup-for-dev.py` clones any that are missing.

#### Conformance

Run the spec's [conformance kit](https://github.com/microsoft/amplifier-smart-tools/tree/main/conformance) against this repository.
The outer `uv run` puts this project's `digital-twin-universe` on `PATH` for the kit to invoke; the inner one runs the kit with its own inline dependencies:

```bash
uv run -- uv run --no-project https://raw.githubusercontent.com/microsoft/amplifier-smart-tools/main/conformance/run.py .
```

## Releasing

Each PR that changes the library or CLI already bumps the version: `uv version --bump <part>`, then the same version in
`SMART_TOOL.md`, `SKILL.md`, and the `@v<version>` and `#v<version>` install pins, which `tests/test_manifest.py`
holds to the package version.

Tag the merge commit right after the PR merges, since main's docs already point at the new tag:

```bash
git tag v<version> <commit>
git push origin v<version>
```

The tag runs `.github/workflows/release.yml`, which builds the wheel and sdist and publishes them as a GitHub Release
with generated notes.
