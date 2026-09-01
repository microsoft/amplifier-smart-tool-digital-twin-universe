"""Managing environments from a request in words.

Model-backed. Consumes tokens and may return a different answer on a second run.

The model chooses from a fixed registry of deterministic actions and supplies
their arguments. It cannot invent an action, reach past the registry, or run
anything itself: deterministic code validates every step against the registry and
is the only thing that executes.

Planning is free of consequences. Execution is not, so a plan that mutates
anything runs only when the caller confirms that specific plan. Confirmation is
per invocation; there is no session-wide unlock and no environment variable that
grants it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from typing import Any

from amplifier_digital_twin_universe import environments
from amplifier_digital_twin_universe.drafting import DEFAULT_MAX_ATTEMPTS, draft_json, repair_section
from amplifier_digital_twin_universe.errors import SmartToolError
from amplifier_digital_twin_universe.intelligence import Intelligence, resolve
from amplifier_digital_twin_universe.prerequisites import HostReport, probe

__all__ = ["ACTIONS", "Action", "ManagePlan", "PlannedStep", "manage"]


@dataclass(frozen=True)
class Action:
    """One deterministic capability the planner is allowed to choose."""

    name: str
    summary: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    mutating: bool
    run: Callable[..., Any]

    def signature(self) -> str:
        args = [f"{name}" for name in self.required] + [f"{name}?" for name in self.optional]
        marker = "mutates" if self.mutating else "reads"
        return f"{self.name}({', '.join(args)}) [{marker}] {self.summary}"


ACTIONS: dict[str, Action] = {
    action.name: action
    for action in (
        Action(
            name="list_environments",
            summary="Every environment on this host.",
            required=(),
            optional=(),
            mutating=False,
            run=lambda: environments.list_environments(),
        ),
        Action(
            name="status",
            summary="One environment's state and access URLs.",
            required=("environment_id",),
            optional=(),
            mutating=False,
            run=lambda environment_id: environments.status(environment_id),
        ),
        Action(
            name="check_readiness",
            summary="Evaluate one environment's readiness checks.",
            required=("environment_id",),
            optional=("skip_access_check",),
            mutating=False,
            run=lambda environment_id, skip_access_check=False: environments.check_readiness(
                environment_id, skip_access_check=bool(skip_access_check)
            ),
        ),
        Action(
            name="run",
            summary="Run one command inside an environment. command is a list of argv strings.",
            required=("environment_id", "command"),
            optional=("timeout",),
            mutating=True,
            run=lambda environment_id, command, timeout=None: environments.run(
                environment_id,
                list(command),
                timeout=environments.DEFAULT_EXEC_TIMEOUT_SECONDS if timeout is None else int(timeout),
            ),
        ),
        Action(
            name="launch",
            summary="Stand up a new environment from a profile path.",
            required=("profile",),
            optional=("variables", "name", "hostname"),
            mutating=True,
            run=lambda profile, variables=None, name=None, hostname=None: environments.launch(
                profile, variables=variables, name=name, hostname=hostname
            ),
        ),
        Action(
            name="update",
            summary="Re-run one environment's update commands in place.",
            required=("environment_id",),
            optional=("variables", "skip_readiness"),
            mutating=True,
            run=lambda environment_id, variables=None, skip_readiness=False: environments.update(
                environment_id, variables=variables, skip_readiness=bool(skip_readiness)
            ),
        ),
        Action(
            name="push_files",
            summary="Copy host paths into an environment. sources is a list of paths.",
            required=("environment_id", "sources", "destination"),
            optional=("recursive",),
            mutating=True,
            run=lambda environment_id, sources, destination, recursive=False: environments.push_files(
                environment_id, list(sources), destination, recursive=bool(recursive)
            ),
        ),
        Action(
            name="pull_files",
            summary="Copy environment paths out to the host. sources is a list of paths.",
            required=("environment_id", "sources", "destination"),
            optional=("recursive",),
            mutating=True,
            run=lambda environment_id, sources, destination, recursive=False: environments.pull_files(
                environment_id, list(sources), destination, recursive=bool(recursive)
            ),
        ),
        Action(
            name="destroy",
            summary="Tear down an environment. Nothing inside survives.",
            required=("environment_id",),
            optional=(),
            mutating=True,
            run=lambda environment_id: environments.destroy(environment_id),
        ),
    )
}


@dataclass(frozen=True)
class PlannedStep:
    """One action with its arguments, and what came of running it."""

    action: str
    arguments: dict[str, Any]
    why: str
    mutating: bool
    ran: bool = False
    ok: bool | None = None
    result: Any = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "arguments": dict(self.arguments),
            "why": self.why,
            "mutating": self.mutating,
            "ran": self.ran,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
        }


@dataclass(frozen=True)
class ManagePlan:
    """What the request means in terms of this tool's deterministic actions."""

    request: str
    summary: str
    steps: list[PlannedStep] = field(default_factory=list)
    mutating: bool = False
    confirmed: bool = False
    executed: bool = False
    complete: bool = True
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "mutating": self.mutating,
            "confirmed": self.confirmed,
            "executed": self.executed,
            "complete": self.complete,
            "usage": dict(self.usage),
        }


def manage(
    request: str,
    *,
    confirmed: bool = False,
    host: HostReport | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    provider: str | None = None,
    model: str | None = None,
    intelligence: Intelligence | None = None,
) -> ManagePlan:
    """Turn a request in words into deterministic actions, and run them when confirmed.

    Model-backed. Consumes tokens and may return a different answer on a second
    run.

    Without `confirmed`, the plan comes back unrun and nothing on the host changes.
    With `confirmed`, every step runs in order and execution stops at the first
    failure, so a later step never runs on a state an earlier step failed to reach.
    """
    if not request.strip():
        raise ValueError("request must not be empty")

    engine = resolve(intelligence)
    engine.preflight(provider)
    report = host if host is not None else probe()
    inventory = _inventory(report)

    drafted = draft_json(
        engine,
        lambda findings: _build_prompt(request, report, inventory, findings),
        _check,
        what="plan",
        max_attempts=max_attempts,
        provider=provider,
        model=model,
    )

    document = drafted.document
    steps = [
        PlannedStep(
            action=str(step["action"]),
            arguments=dict(step.get("arguments") or {}),
            why=str(step.get("why", "")).strip(),
            mutating=ACTIONS[str(step["action"])].mutating,
        )
        for step in document.get("steps", [])
    ]
    mutating = any(step.mutating for step in steps)
    plan = ManagePlan(
        request=request,
        summary=str(document.get("summary", "")).strip(),
        steps=steps,
        mutating=mutating,
        confirmed=confirmed,
        usage=drafted.usage(),
    )

    if not confirmed or not steps:
        return plan
    return _execute(plan)


def _execute(plan: ManagePlan) -> ManagePlan:
    ran: list[PlannedStep] = []
    complete = True
    for index, step in enumerate(plan.steps):
        if not complete:
            ran.append(step)
            continue
        action = ACTIONS[step.action]
        try:
            result = action.run(**step.arguments)
        except SmartToolError as exc:
            complete = False
            ran.append(
                PlannedStep(
                    action=step.action,
                    arguments=step.arguments,
                    why=step.why,
                    mutating=step.mutating,
                    ran=True,
                    ok=False,
                    error={"code": exc.code, "message": exc.message, "remedy": exc.remedy},
                )
            )
        except Exception as exc:
            complete = False
            ran.append(
                PlannedStep(
                    action=step.action,
                    arguments=step.arguments,
                    why=step.why,
                    mutating=step.mutating,
                    ran=True,
                    ok=False,
                    error={"code": "failed", "message": str(exc), "remedy": f"Step {index + 1} did not complete."},
                )
            )
        else:
            ran.append(
                PlannedStep(
                    action=step.action,
                    arguments=step.arguments,
                    why=step.why,
                    mutating=step.mutating,
                    ran=True,
                    ok=True,
                    result=result,
                )
            )

    return ManagePlan(
        request=plan.request,
        summary=plan.summary,
        steps=ran,
        mutating=plan.mutating,
        confirmed=True,
        executed=True,
        complete=complete,
        usage=plan.usage,
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

        name = str(step.get("action", "")).strip()
        action = ACTIONS.get(name)
        if action is None:
            findings.append(f"{where}.action {name!r} is not one of: {', '.join(sorted(ACTIONS))}")
            continue

        if not str(step.get("why", "")).strip():
            findings.append(f"{where}.why is missing or empty")

        arguments = step.get("arguments") or {}
        if not isinstance(arguments, dict):
            findings.append(f"{where}.arguments must be an object")
            continue

        allowed = set(action.required) | set(action.optional)
        for key in sorted(set(arguments) - allowed):
            findings.append(f"{where}.arguments has no parameter {key!r}; {name} takes {sorted(allowed) or 'nothing'}")
        for key in action.required:
            if key not in arguments:
                findings.append(f"{where}.arguments is missing the required parameter {key!r}")

        if name == "run" and not isinstance(arguments.get("command"), list):
            findings.append(f"{where}.arguments.command must be a list of argv strings")
        if name in ("push_files", "pull_files") and not isinstance(arguments.get("sources"), list):
            findings.append(f"{where}.arguments.sources must be a list of paths")
    return findings


def _inventory(report: HostReport) -> list[dict[str, Any]]:
    if not report.can_launch:
        return []
    try:
        return environments.list_environments()
    except Exception:
        return []


def _build_prompt(
    request: str,
    report: HostReport,
    inventory: list[dict[str, Any]],
    findings: list[str],
) -> str:
    parts = [
        "# Digital Twin Universe environment management",
        "",
        "A Digital Twin Universe is an isolated container stood up from a declarative profile.",
        "Plan the request below as an ordered list of the actions available here, and nothing else.",
        "",
        "## Actions available",
        *[f"- {ACTIONS[name].signature()}" for name in sorted(ACTIONS)],
        "",
        "Arguments marked with `?` are optional. Use no other parameter names.",
        "An action that mutates changes the host, so include one only when the request asks for it.",
        "",
        "## Environments on this host",
        "```json",
        json.dumps(inventory, indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## This host",
        "```json",
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        "```",
        "",
        "## Request",
        request.strip(),
        "",
        "## Answer format",
        "Reply with one JSON object in a ```json fenced block and nothing after it:",
        "```json",
        json.dumps(
            {
                "summary": "one sentence describing what the plan does",
                "steps": [
                    {
                        "action": "status",
                        "arguments": {"environment_id": "dtu-1a2b3c4d"},
                        "why": "what this step contributes to the request",
                    }
                ],
            },
            indent=2,
        ),
        "```",
        "An empty steps list is the right answer when no available action serves the request.",
        *repair_section(findings),
    ]
    return "\n".join(parts)
