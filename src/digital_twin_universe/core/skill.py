"""Skill: what the tool tells an agent that has decided to drive it."""

from importlib.metadata import metadata
from importlib.metadata import version as distribution_version
from pathlib import Path

from liquid import Environment, StrictUndefined

from digital_twin_universe.core.manifest import MANIFEST_PATH, load_manifest
from digital_twin_universe.schemas import (
    AGENT_PROVIDERS,
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    Capability,
    DigitalTwinUniverseError,
)

DISTRIBUTION = "digital-twin-universe"

# One table drives the skill's capability list, so it cannot drift from what the CLI exposes.
# Every capability added to the library gets a row here.
CAPABILITIES = (
    Capability("manifest", "Print the tool's manifest as JSON.", model_backed=False, skill="core/manifest.md"),
    Capability(
        "check",
        "Report whether this host can run a universe: Docker CLI, daemon, and Compose plugin.",
        model_backed=False,
        skill="capabilities/check/SKILL.md",
    ),
    Capability(
        "install",
        "Get Docker working from official docs; show the plan, or run unattended steps with --yes.",
        model_backed=True,
        skill="capabilities/install/SKILL.md",
    ),
    Capability(
        "create-profile",
        "Write a profile from a description and the project, launch it, run checks in it, and keep it only when it passes.",
        model_backed=True,
        skill="capabilities/create_profile/SKILL.md",
    ),
    Capability(
        "validate-profile",
        "Check a profile without launching it: Compose, `x-dtu`, the universe invariants, and realism warnings.",
        model_backed=False,
        skill="capabilities/universe/validate-profile.md",
    ),
    Capability(
        "launch",
        "Launch a universe from a profile name or Compose file and wait until every healthcheck passes.",
        model_backed=False,
        skill="capabilities/universe/launch.md",
    ),
    Capability(
        "list",
        "List every universe launched from this machine, running or not, measured now.",
        model_backed=False,
        skill="capabilities/universe/list.md",
    ),
    Capability(
        "status",
        "Measure one universe now: its state, services, and the URLs the host can open.",
        model_backed=False,
        skill="capabilities/universe/status.md",
    ),
    Capability(
        "exec",
        "Run a command in the twin as its user through a login shell, or open an interactive shell in it.",
        model_backed=False,
        skill="capabilities/universe/exec.md",
    ),
    Capability(
        "file-push",
        "Copy a file or directory from the host into the twin, owned by the twin's user.",
        model_backed=False,
        skill="capabilities/universe/file-push.md",
    ),
    Capability(
        "file-pull",
        "Copy a file or directory from the twin onto the host.",
        model_backed=False,
        skill="capabilities/universe/file-pull.md",
    ),
    Capability(
        "destroy",
        "Remove a universe: its containers, networks, volumes, and state. Built images stay.",
        model_backed=False,
        skill="capabilities/universe/destroy.md",
    ),
    Capability(
        "dashboard",
        "Serve a web page showing every universe on this machine, with its URLs and a destroy button, and its MCP server; "
        "print both URLs.",
        model_backed=False,
        skill="capabilities/dashboard/SKILL.md",
    ),
    Capability(
        "mcp",
        "Serve list, status, and destroy, and the dashboard as an MCP App, to an MCP client over stdio.",
        model_backed=False,
        skill="adapters/mcp.md",
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


ENVIRONMENT = Environment(undefined=StrictUndefined)
# Capability skills are Liquid templates so what they say about defaults cannot drift from the code that applies them.
SKILL_VARIABLES = {
    "agent_providers": AGENT_PROVIDERS,
    "default_intelligence_models": DEFAULT_INTELLIGENCE_MODELS,
    "default_intelligence_reasoning_effort": DEFAULT_INTELLIGENCE_REASONING_EFFORT,
}


def skill_directory() -> Path:
    """The installed package root, resolved at runtime, where the tool's own files live."""
    return MANIFEST_PATH.parent.resolve()


def version() -> str:
    """The installed package's version, from its metadata."""
    return distribution_version(DISTRIBUTION)


def repository_url() -> str | None:
    """The tool's canonical source, from the package metadata, or None when the package declares none."""
    for entry in metadata(DISTRIBUTION).get_all("Project-URL") or []:
        label, _, url = str(entry).partition(",")
        if label.strip().lower() == "repository":
            return url.strip()
    return None


def capability(name: str) -> Capability:
    """The capability that answers to `name`, as the skill and the CLI both present it."""
    for entry in CAPABILITIES:
        if entry.name == name:
            return entry
    known = ", ".join(entry.name for entry in CAPABILITIES)
    raise DigitalTwinUniverseError(
        "capability-unknown", f"'{name}' is not a capability of this tool.", f"The capabilities are: {known}."
    )


def skill_resources() -> list[str]:
    """The files the skill lists, as paths relative to the skill directory."""
    return _installed(SKILL_RESOURCES)


def skill(capability: str | None = None) -> str:
    """The tool's skill, or one capability's skill when named."""
    if capability is None:
        return _tool_skill()
    return _capability_skill(capability)


def _tool_skill() -> str:
    """The tool's skill: the manifest body, the capability list, and where the tool's files are."""
    manifest = load_manifest()
    body = [manifest.body, "", "## Capabilities", ""]
    for entry in CAPABILITIES:
        kind = "model-backed" if entry.model_backed else "deterministic"
        body.append(
            f"- `{entry.name}` [{kind}] -- {entry.summary} "
            f"Arguments, result, and exit codes: `{manifest.name} {entry.name} --help`."
        )
    return _document(manifest.name, [], body, skill_resources())


def _capability_skill(name: str) -> str:
    """One capability's skill: the same shape as the tool's, scoped to what it takes, returns, and fails on."""
    entry = capability(name)
    tool = load_manifest().name
    # The body and the resources are checked together, so neither can name a file the package does not ship.
    body_path, *resources = _installed((entry.skill, *entry.resources))
    kind = "Model-backed." if entry.model_backed else "Deterministic."
    template = (skill_directory() / body_path).read_text(encoding="utf-8")
    body = [kind, "", ENVIRONMENT.render(template, **SKILL_VARIABLES).strip()]
    return _document(
        f"{tool} {entry.name}", [f"Part of `{tool}`; `{tool} --help` is the tool's skill."], body, resources
    )


def _document(name: str, header: list[str], body: list[str], resources: list[str]) -> str:
    """Render one skill, so the tool's and a capability's cannot drift apart in shape."""
    repository = repository_url()
    lines = [f'<skill_content name="{name}">', f"Skill directory: {skill_directory()}"]
    # A caller that can run the tool cannot always read its files, so the canonical source stands in for them.
    if repository:
        lines.append(f"Repository: {repository}")
    lines.append("Relative paths in this skill are relative to the skill directory.")
    lines += header
    lines += ["", f"# {name}", "", *body]
    if resources:
        lines += ["", "<skill_resources>"]
        lines += [f"  <file>{path}</file>" for path in resources]
        lines.append("</skill_resources>")
    lines.append("</skill_content>")
    return "\n".join(lines)


def _installed(paths: tuple[str, ...]) -> list[str]:
    """The given paths, checked against the installed package so a skill can never name a file that is not there."""
    root = skill_directory()
    missing = [path for path in paths if not (root / path).is_file()]
    if missing:
        raise DigitalTwinUniverseError(
            "skill-resource-missing",
            f"The skill names files that are not in the installed package: {', '.join(missing)}.",
            f"Ship them under {root} or drop them from the skill.",
        )
    return list(paths)
