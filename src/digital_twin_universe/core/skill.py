"""Skill: what the tool tells an agent that has decided to drive it."""

from importlib.metadata import metadata
from pathlib import Path

from digital_twin_universe.core.manifest import MANIFEST_PATH, load_manifest
from digital_twin_universe.schemas import Capability, DigitalTwinUniverseError

DISTRIBUTION = "digital-twin-universe"

# One table drives the skill's capability list, so it cannot drift from what the CLI exposes.
# Every capability added to the library gets a row here.
CAPABILITIES = (
    Capability("manifest", "Print the tool's manifest as JSON.", model_backed=False),
    Capability(
        "check",
        "Report whether this host can run a universe: Docker CLI, daemon, and Compose plugin.",
        model_backed=False,
    ),
    Capability(
        "install",
        "Get Docker working from official docs; show the plan, or run unattended steps with --yes.",
        model_backed=True,
    ),
    Capability(
        "create-profile",
        "Write a profile from a description and the project, launch it, run checks in it, and keep it only when it passes.",
        model_backed=True,
    ),
    Capability(
        "validate-profile",
        "Check a profile without launching it: Compose, `x-dtu`, the universe invariants, and realism warnings.",
        model_backed=False,
    ),
    Capability(
        "launch",
        "Launch a universe from a profile name or Compose file and wait until every healthcheck passes.",
        model_backed=False,
    ),
    Capability(
        "list",
        "List every universe launched from this machine, running or not, measured now.",
        model_backed=False,
    ),
    Capability(
        "status",
        "Measure one universe now: its state, services, and the URLs the host can open.",
        model_backed=False,
    ),
    Capability(
        "exec",
        "Run a command in the twin as its user through a login shell, or open an interactive shell in it.",
        model_backed=False,
    ),
    Capability(
        "file-push",
        "Copy a file or directory from the host into the twin, owned by the twin's user.",
        model_backed=False,
    ),
    Capability(
        "file-pull",
        "Copy a file or directory from the twin onto the host.",
        model_backed=False,
    ),
    Capability(
        "destroy",
        "Remove a universe: its containers, networks, volumes, and state. Built images stay.",
        model_backed=False,
    ),
    Capability(
        "dashboard",
        "Serve a web page showing every universe on this machine, with its URLs and a destroy button, and its MCP server; "
        "print both URLs.",
        model_backed=False,
    ),
    Capability(
        "mcp",
        "Serve list, status, and destroy, and the dashboard as an MCP App, to an MCP client over stdio.",
        model_backed=False,
    ),
)

# Paths relative to the skill directory. All ship inside the package, so all resolve after installation.
# An example is listed by its compose.yaml alone; it names the Dockerfile and anything else beside it.
SKILL_RESOURCES = (
    "SMART_TOOL.md",
    "lib.py",
    "examples/hello/compose.yaml",
    "examples/copilot-cli/compose.yaml",
    "examples/served-repository/compose.yaml",
    "examples/web-site/compose.yaml",
)


def skill_directory() -> Path:
    """The installed package root, resolved at runtime, where the tool's own files live."""
    return MANIFEST_PATH.parent.resolve()


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    for entry in metadata(DISTRIBUTION).get_all("Project-URL") or []:
        label, _, url = str(entry).partition(",")
        if label.strip().lower() == "repository":
            return url.strip()
    return None


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory."""
    root = skill_directory()
    missing = [path for path in SKILL_RESOURCES if not (root / path).is_file()]
    if missing:
        raise DigitalTwinUniverseError(
            "skill-resource-missing",
            f"The skill names files that are not in the installed package: {', '.join(missing)}.",
            f"Ship them under {root} or drop them from SKILL_RESOURCES.",
        )
    return list(SKILL_RESOURCES)


def skill() -> str:
    """Compose the tool's skill: the manifest body, the capability list, and where the tool's files are."""
    manifest = load_manifest()
    repository = repository_url()
    lines = [
        f'<skill_content name="{manifest.name}">',
        f"Skill directory: {skill_directory()}",
    ]
    # A caller that can run the tool cannot always read its files, so the canonical source stands in for them.
    if repository:
        lines.append(f"Repository: {repository}")
    lines += [
        "Relative paths in this skill are relative to the skill directory.",
        "",
        f"# {manifest.name}",
        "",
        manifest.body,
        "",
        "## Capabilities",
        "",
    ]
    for capability in CAPABILITIES:
        kind = "model-backed" if capability.model_backed else "deterministic"
        lines.append(
            f"- `{capability.name}` [{kind}] -- {capability.summary} "
            f"Arguments, result, and exit codes: `{manifest.name} {capability.name} --help`."
        )
    lines += ["", "<skill_resources>"]
    lines += [f"  <file>{path}</file>" for path in skill_resources()]
    lines += ["</skill_resources>", "</skill_content>"]
    return "\n".join(lines)
