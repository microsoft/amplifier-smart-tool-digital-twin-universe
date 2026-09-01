"""Install planning: what this host needs, in the order it needs it.

Model-backed. Consumes tokens and may return a different answer on a second run.

The plan is a proposal. Nothing here installs, configures, or starts anything, so
a caller reads the steps, decides, and runs them. The host evidence the plan is
built from is gathered deterministically and returned alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from amplifier_digital_twin_universe.drafting import DEFAULT_MAX_ATTEMPTS, draft_json, repair_section
from amplifier_digital_twin_universe.intelligence import Intelligence, resolve
from amplifier_digital_twin_universe.knowledge import install_guide
from amplifier_digital_twin_universe.prerequisites import HostReport, probe

__all__ = ["InstallPlan", "InstallStep", "plan_install"]


@dataclass(frozen=True)
class InstallStep:
    """One action a person takes, and how they confirm it worked."""

    title: str
    why: str
    commands: list[str] = field(default_factory=list)
    verify: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "why": self.why, "commands": list(self.commands), "verify": self.verify}


@dataclass(frozen=True)
class InstallPlan:
    """What to do to make this host able to launch environments."""

    host: HostReport
    ready: bool
    summary: str
    steps: list[InstallStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "notes": list(self.notes),
            "host": self.host.to_dict(),
            "usage": dict(self.usage),
        }


def plan_install(
    *,
    goal: str | None = None,
    context: str | None = None,
    host: HostReport | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> InstallPlan:
    """Produce ordered install steps for this host.

    Model-backed. Consumes tokens and may return a different answer on a second
    run.

    `goal` narrows the plan to what a particular use needs, such as launching
    without mock sidecars. `context` is additional material the caller already
    holds, passed as data rather than as a path.
    """
    engine = resolve(intelligence)
    engine.preflight(provider)
    report = host if host is not None else probe()

    drafted = draft_json(
        engine,
        lambda findings: _build_prompt(report, goal, context, findings),
        _check,
        what="install plan",
        max_attempts=max_attempts,
        provider=provider,
        model=model,
    )

    document = drafted.document
    return InstallPlan(
        host=report,
        ready=report.ready and report.can_launch,
        summary=str(document.get("summary", "")).strip(),
        steps=[
            InstallStep(
                title=str(step.get("title", "")).strip(),
                why=str(step.get("why", "")).strip(),
                commands=[str(c) for c in step.get("commands", [])],
                verify=str(step.get("verify", "")).strip(),
            )
            for step in document.get("steps", [])
        ],
        notes=[str(note) for note in document.get("notes", [])],
        usage=drafted.usage(),
    )


def _check(document: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if not str(document.get("summary", "")).strip():
        findings.append("'summary' is missing or empty")

    steps = document.get("steps")
    if not isinstance(steps, list):
        findings.append("'steps' is missing or is not a list")
        return findings

    for index, step in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(step, dict):
            findings.append(f"{where} is not an object")
            continue
        for key in ("title", "why"):
            if not str(step.get(key, "")).strip():
                findings.append(f"{where}.{key} is missing or empty")
        commands = step.get("commands", [])
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            findings.append(f"{where}.commands must be a list of shell command strings")
    return findings


def _build_prompt(
    report: HostReport,
    goal: str | None,
    context: str | None,
    findings: list[str],
) -> str:
    missing = report.missing()
    parts = [
        install_guide(),
        "",
        "# Task",
        "Produce the ordered steps that make this specific host able to launch a Digital Twin Universe.",
        "Plan only for what this host is missing. Do not restate steps it already satisfies.",
        "Every command must be one a person can paste into a shell as written.",
        "",
        "## This host",
        "```json",
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        "```",
        "",
        "## What is absent",
        *(
            [f"- {item.name}: {item.detail} ({'optional' if item.optional else 'required'})" for item in missing]
            or ["Nothing. Every declared prerequisite is present."]
        ),
    ]

    if goal:
        parts += ["", "## What the caller wants to do with it", goal.strip()]
    if context:
        parts += ["", "## Additional context supplied by the caller", context.strip()]

    parts += [
        "",
        "## Answer format",
        "Reply with one JSON object in a ```json fenced block and nothing after it:",
        "```json",
        json.dumps(
            {
                "summary": "one sentence naming what this host needs",
                "steps": [
                    {
                        "title": "short imperative title",
                        "why": "what this step achieves for this host",
                        "commands": ["shell command", "shell command"],
                        "verify": "the command that confirms the step worked",
                    }
                ],
                "notes": ["anything the caller should know before starting"],
            },
            indent=2,
        ),
        "```",
        "An empty steps list is the right answer when the host already satisfies everything.",
        *repair_section(findings),
    ]
    return "\n".join(parts)
