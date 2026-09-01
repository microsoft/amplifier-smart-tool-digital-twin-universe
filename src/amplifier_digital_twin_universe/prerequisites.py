"""What this host can do right now.

Deterministic. Runs with no model provider configured, and changes nothing: every
probe here reads, and none of them install, configure, or start anything.

The set of prerequisites is read from the manifest rather than restated, so the
failure a caller sees and the requirement the manifest declares cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform as platform_module
import shutil
import subprocess
from typing import Any

from amplifier_digital_twin_universe.manifest import Requirement, load_manifest

PROBE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Prerequisite:
    """One manifest requirement, measured against this host."""

    name: str
    purpose: str
    install: str
    optional: bool
    present: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "install": self.install,
            "optional": self.optional,
            "present": self.present,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HostReport:
    """The state of this host, as evidence for a decision.

    `ready` means every required prerequisite is present. `can_launch` is the
    stricter question a caller actually asks before launching: whether the
    container runtime is installed and its daemon answers.
    """

    platform: str
    supported: bool
    ready: bool
    can_launch: bool
    prerequisites: list[Prerequisite]
    model_providers: list[str]
    environments: int | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "supported": self.supported,
            "ready": self.ready,
            "can_launch": self.can_launch,
            "prerequisites": [p.to_dict() for p in self.prerequisites],
            "model_providers": list(self.model_providers),
            "environments": self.environments,
            "notes": list(self.notes),
        }

    def missing(self) -> list[Prerequisite]:
        """Prerequisites this host does not satisfy, required ones first."""
        absent = [p for p in self.prerequisites if not p.present]
        return sorted(absent, key=lambda p: p.optional)


def detect_platform() -> str:
    """`wsl2`, `linux`, or the bare platform name for anything else."""
    system = platform_module.system().lower()
    if system != "linux":
        return system or "unknown"
    version = Path("/proc/version")
    if version.is_file():
        try:
            if "microsoft" in version.read_text(encoding="utf-8", errors="ignore").lower():
                return "wsl2"
        except OSError:
            pass
    return "linux"


def probe() -> HostReport:
    """Measure this host against the manifest's requirements.

    Deterministic. Requires no model provider.
    """
    manifest = load_manifest()
    host_platform = detect_platform()
    supported = host_platform in {p.lower() for p in manifest.platforms} or host_platform == "wsl2"

    prerequisites = [_probe_requirement(requirement) for requirement in manifest.requires]
    by_name = {p.name: p for p in prerequisites}

    incus = by_name.get("incus")
    can_launch = bool(incus and incus.present)
    ready = all(p.present for p in prerequisites if not p.optional)

    notes: list[str] = []
    if not supported:
        notes.append(
            f"This tool supports {', '.join(manifest.platforms)}. Launching an environment on "
            f"{host_platform!r} is not supported."
        )
    if host_platform == "wsl2":
        notes.append(
            "On WSL2, Docker and Incus contend for the same iptables FORWARD chain. "
            "A container with no outbound network is almost always that conflict."
        )

    return HostReport(
        platform=host_platform,
        supported=supported,
        ready=ready,
        can_launch=can_launch,
        prerequisites=prerequisites,
        model_providers=_model_providers(),
        environments=_environment_count() if can_launch else None,
        notes=notes,
    )


def _probe_requirement(requirement: Requirement) -> Prerequisite:
    present, detail = _PROBES.get(requirement.name, _probe_on_path)(requirement.name)
    return Prerequisite(
        name=requirement.name,
        purpose=requirement.purpose,
        install=requirement.install,
        optional=requirement.optional,
        present=present,
        detail=detail,
    )


def _probe_on_path(name: str) -> tuple[bool, str]:
    located = shutil.which(name)
    return (True, f"found at {located}") if located else (False, "not found on PATH")


def _probe_incus(name: str) -> tuple[bool, str]:
    """Installed is not enough: the daemon has to answer this user."""
    if shutil.which(name) is None:
        return False, "not found on PATH"
    completed = _run([name, "version"])
    if completed is None:
        return False, "the incus client did not respond"
    if completed.returncode != 0:
        return False, _first_line(completed.stderr) or "the incus client exited non-zero"
    text = completed.stdout.strip()
    if "unreachable" in text.lower():
        return False, (
            "the incus daemon is unreachable from this user. Add yourself to the "
            "incus-admin group, then start a new login session."
        )
    return True, " ".join(text.split())


def _probe_docker(name: str) -> tuple[bool, str]:
    if shutil.which(name) is None:
        return False, "not found on PATH"
    completed = _run([name, "version", "--format", "{{.Server.Version}}"])
    if completed is None or completed.returncode != 0:
        detail = _first_line(completed.stderr) if completed else "the docker client did not respond"
        return False, detail or "the docker daemon is unreachable from this user"
    return True, f"server {completed.stdout.strip()}"


def _probe_avahi(name: str) -> tuple[bool, str]:
    located = shutil.which("avahi-publish-address")
    return (True, f"found at {located}") if located else (False, "avahi-publish-address is not on PATH")


_PROBES = {
    "incus": _probe_incus,
    "docker": _probe_docker,
    "avahi": _probe_avahi,
}


def _model_providers() -> list[str]:
    """Providers whose credentials resolve here, or an empty list."""
    try:
        from amplifier_digital_twin_universe.intelligence import default_intelligence

        return default_intelligence().available_providers()
    except Exception:
        # A probe reports what it can measure. An engine that will not import is
        # a model-backed problem, and the model-backed paths raise it precisely.
        return []


def _environment_count() -> int | None:
    try:
        from amplifier_digital_twin_universe import environments

        return len(environments.list_environments())
    except Exception:
        return None


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _first_line(text: str | None) -> str:
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
