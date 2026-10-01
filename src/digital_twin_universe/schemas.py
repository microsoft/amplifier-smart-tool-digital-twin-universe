from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

ReasoningEffort = Literal["low", "medium", "high", "xhigh", "max"]
AgentProvider = Literal["copilot", "amplifier-agent"]
# Also the order an agent provider is picked in when none is named.
AGENT_PROVIDERS: tuple[AgentProvider, ...] = ("copilot", "amplifier-agent")

DEFAULT_INTELLIGENCE_MODEL = "gpt-6.1-sol"
DEFAULT_INTELLIGENCE_MODELS: dict[AgentProvider, str] = {
    "copilot": DEFAULT_INTELLIGENCE_MODEL,
    "amplifier-agent": "openai/gpt-6.1-sol",
}
DEFAULT_INTELLIGENCE_REASONING_EFFORT: ReasoningEffort = "high"

SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class DigitalTwinUniverseError(Exception):
    """Raised for any failure the library can name and explain how to fix.

    `code` is a stable slug a caller can branch on, `message` says what went wrong with the specifics,
    and `remedy` says what to do about it.
    """

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{message} {remedy}")
        self.code = code
        self.message = message
        self.remedy = remedy


# region: Manifest


class ManifestRequirement(BaseModel):
    """One environment prerequisite; `install` references documentation, never a command."""

    name: str
    purpose: str
    install: str
    optional: bool = False


class Manifest(BaseModel):
    """The structured form of SMART_TOOL.md."""

    smart_tool_format: int
    name: str = Field(pattern=SLUG_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    description: str
    use_cases: list[str]
    platforms: list[str]
    requires: list[ManifestRequirement] = Field(default_factory=list)
    body: str = Field(description="The Markdown below the frontmatter: the skill `--help` renders")


# endregion

# region: Skill


class Capability(NamedTuple):
    """One capability of the tool: its line in the skill's capability list and its own skill."""

    name: str
    summary: str
    model_backed: bool
    skill: str  # the capability's skill body, a Liquid-templated Markdown file relative to the skill directory
    resources: tuple[str, ...] = ()  # the files that skill refers to, relative to the skill directory


# endregion

# region: Check

Platform = Literal["linux", "macos", "windows"]


class Prerequisite(BaseModel):
    """One thing a universe needs from the host, measured."""

    name: str
    present: bool
    detail: str = Field(description="The version when present, otherwise what the probe saw")
    remedy: str | None = Field(default=None, description="How to make it present, set only when it is not")


class HostReport(BaseModel):
    """Whether this host can run a universe, and why not when it cannot."""

    platform: Platform
    ok: bool
    docker_version: str | None
    compose_version: str | None
    prerequisites: list[Prerequisite]


# endregion

# region: Install

InstallOutcome = Literal["ready", "planned", "installed", "action-required", "failed"]
StepStatus = Literal["pending", "done", "skipped", "failed", "manual"]
InstallMethod = Literal[
    "docker-desktop-windows-per-user", "docker-desktop-macos", "docker-engine-apt", "docker-engine-dnf"
]


class InstallStep(BaseModel):
    """One documented host-shell step, including whether it can run without a person."""

    model_config = ConfigDict(extra="forbid")

    title: str
    commands: list[str]
    source: str
    unattended: bool
    status: StepStatus
    reason: str | None


class InstallPlan(BaseModel):
    """The agent's single proposed plan; the tool supplies the verdict and measured Docker state."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    method: InstallMethod | None
    steps: list[InstallStep]
    next: str
    notes: list[str]


class InstallReport(BaseModel):
    """What was planned or actually ran, and the one thing left for the person to do."""

    outcome: InstallOutcome
    summary: str
    method: str | None
    steps: list[InstallStep]
    next: str
    notes: list[str]
    docs: list[str]
    docker: HostReport


# endregion

# region: Profile


class Finding(BaseModel):
    """One thing `validate-profile` has to say about a profile, as an error or as a warning."""

    code: str
    location: str | None = Field(description="Where in the file, such as `services.box.volumes[0]`; None for all of it")
    message: str
    remedy: str


class ProfileReport(BaseModel):
    """Whether a profile can be launched, and what would be unrealistic about it if it were."""

    path: Path
    name: str
    twin_machine: str = Field(description="Empty when the profile names no twin this tool can find")
    services: list[str]
    ok: bool = Field(description="No errors; warnings do not affect it")
    errors: list[Finding]
    warnings: list[Finding]


# endregion

# region: Universe

UniverseState = Literal["starting", "running", "degraded", "stopped"]
Health = Literal["starting", "healthy", "unhealthy"]


class Service(BaseModel):
    """One Compose service of a universe, as Docker reports it now."""

    name: str
    state: str = Field(description="Compose's word: running, exited, created, ...")
    health: Health | None = Field(description="None when the service has no healthcheck")
    image: str


class UrlSpec(BaseModel):
    """An `x-dtu.urls` entry: a path and label for a port the twin listens on."""

    model_config = ConfigDict(extra="forbid")

    port: int = Field(description="The container port; the host port it is published on is read at launch")
    path: str = Field(default="/", pattern=r"^/")
    host: str = Field(default="localhost", description="The name the URL is reported with; it must reach loopback")
    label: str | None = None


class Url(BaseModel):
    """One of the twin's published ports, as the host reaches it."""

    url: str
    port: int = Field(description="The host port")
    path: str
    label: str | None


class Universe(BaseModel):
    """One Compose project the tool launched, measured now."""

    id: str
    name: str
    description: str | None
    profile_path: Path
    twin_machine: str
    state: UniverseState
    services: list[Service]
    urls: list[Url]
    state_path: Path
    created_at: datetime


class ExecResult(BaseModel):
    """What one command in the twin produced. A non-zero exit is a result, not a failure."""

    exit_code: int
    stdout: str
    stderr: str


class Transfer(BaseModel):
    """What one push or pull copied, with `docker cp` semantics for where it landed."""

    source: str
    destination: str = Field(description="Where the copy landed: inside an existing directory, or at the path itself")
    files: int = Field(description="How many files were copied; a directory counts its files, a file counts one")


class Destroyed(BaseModel):
    """What `destroy` took down."""

    id: str
    removed: list[str] = Field(description="Names of the containers, networks, and volumes taken down")


# endregion

# region: Create profile

CreateOutcome = Literal["created", "validated", "failed"]
CheckStatus = Literal["pending", "passed", "failed", "skipped"]


class Check(BaseModel):
    """One command run in the twin that proves a claim the profile's summary makes."""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(description="Run in the twin through `execute`")
    expect_stdout: str | None = Field(description="A substring stdout must contain; None means exit 0 is enough")
    status: CheckStatus
    detail: str | None = Field(description="The exit code and last lines of output when it did not pass")


class ProfileDraft(BaseModel):
    """What the agent submits: the profile and the evidence it ran. The tool supplies the verdict."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="What the universe is and how the software gets into it")
    files: list[str] = Field(description="Relative to the draft directory")
    environment: list[str] = Field(description="Host variables the profile reads with ${NAME:?...}")
    universe_id: str | None = Field(description="The universe launched from this draft; None only without verify")
    checks: list[Check] = Field(description="With status and detail as the agent saw them in that universe")
    notes: list[str] = Field(description="Trade-offs, what was left unrealistic and why, missing documentation")


class Cleanup(BaseModel):
    """What the cleanup turn did."""

    model_config = ConfigDict(extra="forbid")

    destroyed: list[str] = Field(description="Universe ids")
    removed: list[str] = Field(description="Host paths outside the draft the agent created and deleted")
    profile_changed: bool = Field(description="Whether compose.yaml or anything the build reads was edited")
    notes: list[str]


class CreatedProfile(BaseModel):
    """A profile that was written, checked, and either kept under its name or left as a draft."""

    outcome: CreateOutcome
    name: str
    path: Path = Field(description="`<name>/` when the outcome is created or validated, `<name>.draft/` when failed")
    summary: str
    files: list[str]
    environment: list[str]
    validation: ProfileReport = Field(description="The last validate-profile")
    checks: list[Check] = Field(description="The tool's own run, not the agent's")
    universe_id: str | None = Field(
        description="The tool's universe when it is still up: kept, or left after a failure"
    )
    urls: list[Url] = Field(description="The kept universe's published ports; empty otherwise")
    attempts: int
    cleanup: Cleanup | None = Field(description="None when the run failed before a cleanup turn made sense")
    notes: list[str]
    next: str = Field(description="One instruction for the person")


# endregion

# region: Dashboard


class Dashboard(BaseModel):
    """Where the dashboard is being served, and who can reach it."""

    url: str = Field(description="What to open in a browser")
    mcp_url: str = Field(description="The same MCP server over streamable HTTP, for any other MCP client")
    host: str = Field(description="The interface that was bound")
    port: int
    reachable: str = Field(description="`only this machine`, or the local network when bound to every interface")


# endregion
