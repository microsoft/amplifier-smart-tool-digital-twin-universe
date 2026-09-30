"""Install: plan from live official documentation, then act only with explicit consent."""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from digital_twin_universe.capabilities.install import docs, facts, run
from digital_twin_universe.intelligence.interface import Intelligence, resolve_agent_provider, select_intelligence
from digital_twin_universe.intelligence.schemas import AgentRequest
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    AgentProvider,
    DigitalTwinUniverseError,
    HostReport,
    InstallPlan,
    InstallReport,
    ReasoningEffort,
)

PLAN_PATH = Path.home() / ".digital-twin-universe" / "install" / "plan.json"
PROMPT_PATH = Path(__file__).with_name("plan.md")
MAX_PLAN_RETRIES = 2
NEVER_UNATTENDED = re.compile(r"get\.docker\.com|\b(newgrp|reboot|shutdown)\b")
SUDO_WITHOUT_N = re.compile(r"\bsudo\b(?!\s+-n\b)")
EXPECTED_METHOD = {"windows": "docker-desktop-windows-per-user", "macos": "docker-desktop-macos"}


class SavedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts_hash: str
    plan: InstallPlan
    docs: list[str]
    session_id: str | None
    # A session only resumes through the agent provider that holds it; None for an injected intelligence.
    agent_provider: AgentProvider | None = None


@dataclass
class PlanningSession:
    host: facts.HostFacts
    pages: dict[str, str]
    intelligence: Intelligence
    model: str
    reasoning_effort: ReasoningEffort
    deadline: float
    session_id: str | None = None

    def plan(self, failure: str = "") -> InstallPlan:
        problem = ""
        attempts = 1 if failure else MAX_PLAN_RETRIES + 1
        for _ in range(attempts):
            result = self.intelligence.run(
                AgentRequest(
                    prompt=render_prompt(self.host, self.pages, failure, problem),
                    model=self.model,
                    reasoning_effort=self.reasoning_effort,
                    workspace=None,
                    output_schema=InstallPlan.model_json_schema(),
                    resume=self.session_id,
                    timeout_seconds=math.ceil(facts.remaining(self.deadline)),
                )
            )
            facts.remaining(self.deadline)
            self.session_id = result.session_id or self.session_id
            if result.error is not None:
                raise rejected(result.error)
            try:
                plan = InstallPlan.model_validate(result.output, strict=True)
                validate_plan(plan, self.host, list(self.pages))
            except ValueError as error:
                problem = str(error)
            else:
                return plan
        raise rejected(problem)


def render_prompt(host: facts.HostFacts, pages: dict[str, str], failure: str, rejection: str) -> str:
    with PROMPT_PATH.open(encoding="utf-8") as file:
        template = file.read()
    documentation = "\n".join(
        f"Source: {source}\n<documentation>\n{markdown}\n</documentation>\n" for source, markdown in pages.items()
    )
    repair = (
        "## Repair round\n"
        "Continue this SAME session. The failed command's last output and the report of everything attempted follow.\n"
        "Return replacement steps for the remaining work ONLY, including retrying or correcting the failed work.\n"
        "Do not repeat completed steps. New commands are allowed only from the same official pages and under the\n"
        "same consent rules. If a person must intervene, return a manual step. This is the only execution repair round.\n"
        f"<failure>\n{failure}\n</failure>\n"
        if failure
        else ""
    )
    correction = (
        f"## Validation correction\nYour plan was rejected: {rejection}\nSubmit one corrected InstallPlan under the same rules.\n"
        if rejection
        else ""
    )
    return (
        template.replace("{accept_license}", json.dumps(host.accept_license))
        .replace("{facts}", host.model_dump_json(indent=2))
        .replace("{pages}", documentation)
        .replace("{repair}", repair)
        .replace("{correction}", correction)
    )


def rejected(problem: str) -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        "plan-rejected",
        f"The Docker install plan was not accepted: {problem}",
        "Rerun `digital-twin-universe install` to generate a new plan, or follow the official pages by hand.",
    )


def validate_plan(plan: InstallPlan, host: facts.HostFacts, sources: list[str]) -> None:
    """The invariants that keep a plan safe to run; everything else about its quality is the model's job."""
    if not plan.steps or not plan.summary.strip() or not plan.next.strip():
        raise ValueError("A plan needs steps, a summary, and one next instruction")
    expected = EXPECTED_METHOD.get(host.platform, "docker-engine-")
    if plan.method is not None and not plan.method.startswith(expected):
        raise ValueError(f"The install method {plan.method} does not fit a {host.platform} host")
    for step in plan.steps:
        if step.source not in sources:
            raise ValueError(f"Step source is not one of the supplied official pages: {step.source}")
        if not step.title.strip() or not step.commands or any(not command.strip() for command in step.commands):
            raise ValueError("Every step needs a title and nonempty commands")
        step.status = "pending" if step.unattended else "manual"
        for command in step.commands:
            if not host.accept_license and "--accept-license" in command.lower():
                raise ValueError("--accept-license requires the caller's explicit accept_license=true")
            if not step.unattended:
                continue
            if SUDO_WITHOUT_N.search(command):
                raise ValueError("Unattended sudo must use sudo -n")
            if NEVER_UNATTENDED.search(command):
                raise ValueError("Convenience scripts, new logins and restarts are not unattended steps")
            if all(not line.strip() or line.lstrip().startswith("#") for line in command.splitlines()):
                raise ValueError("An unattended command cannot be only a comment")


def facts_hash(host: facts.HostFacts) -> str:
    return hashlib.sha256(json.dumps(host.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


def read_plan(host: facts.HostFacts) -> SavedPlan | None:
    try:
        with PLAN_PATH.open(encoding="utf-8") as file:
            saved = SavedPlan.model_validate_json(file.read())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError, UnicodeError) as error:
        raise rejected(f"Cannot read {PLAN_PATH}: {error}") from error
    if saved.facts_hash != facts_hash(host):
        return None
    try:
        if saved.docs != docs.page_urls(host):
            raise ValueError("Saved documentation does not match this host's official pages")
        validate_plan(saved.plan, host, saved.docs)
    except ValueError as error:
        raise rejected(f"Invalid saved plan at {PLAN_PATH}: {error}") from error
    return saved


def save_plan(saved: SavedPlan) -> None:
    temporary = PLAN_PATH.with_name(f"plan-{uuid4().hex}.tmp")
    try:
        PLAN_PATH.parents[0].mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as file:
            file.write(saved.model_dump_json(indent=2))
        temporary.replace(PLAN_PATH)
    except OSError as error:
        raise rejected(f"Cannot save the plan at {PLAN_PATH}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def install(
    check_host: Callable[[], HostReport],
    verify: Callable[[], None],
    apply: bool = False,
    accept_license: bool = False,
    agent_provider: AgentProvider | None = None,
    model: str | None = None,
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1200,
    intelligence: Intelligence | None = None,
) -> InstallReport:
    deadline = time.monotonic() + timeout_seconds
    report: InstallReport | None = None
    try:
        docker = check_host()
        report = InstallReport(
            outcome="ready" if docker.ok else "planned",
            summary="Docker CLI, daemon, and Compose are ready." if docker.ok else "Docker is not ready on this host.",
            method=None,
            steps=[],
            next="nothing" if docker.ok else "rerun with --yes",
            notes=[],
            docs=[],
            docker=docker,
        )
        if docker.ok:
            return report
        runner = facts.SubprocessRunner()
        host = facts.host_facts(docker, accept_license, runner, deadline)
        if intelligence is None:
            agent_provider = resolve_agent_provider(agent_provider)
        agent, model = select_intelligence(intelligence, agent_provider, model, DEFAULT_INTELLIGENCE_MODELS)
        agent.preflight()
        saved = read_plan(host) if apply else None
        pages = docs.fetch_docs(host, deadline)
        resume = saved.session_id if saved and saved.agent_provider == agent_provider else None
        session = PlanningSession(host, pages, agent, model, reasoning_effort, deadline, resume)
        plan = saved.plan if saved else session.plan()
        if saved is None:
            save_plan(
                SavedPlan(
                    facts_hash=facts_hash(host),
                    plan=plan,
                    docs=list(pages),
                    session_id=session.session_id,
                    agent_provider=agent_provider,
                )
            )
        report.summary = plan.summary
        report.method = plan.method
        report.steps = plan.steps
        report.notes = [*plan.notes, f"Plan saved at {PLAN_PATH}."]
        report.docs = list(pages)
        if not apply:
            return report
        report.next = plan.next
        with TemporaryDirectory(prefix="digital-twin-universe-install-") as directory:
            _apply(report, session, runner, check_host, Path(directory))
        if report.outcome == "installed":
            run.verify(verify, report)
        return report
    except (TimeoutError, subprocess.TimeoutExpired) as error:
        if report is not None:
            report.outcome = "failed"
            report.summary = "The Docker installation deadline expired."
            report.next = "run `digital-twin-universe check` before retrying the install"
            run.skip_remaining(report.steps, "The install deadline expired before this step completed.")
        partial = report.model_dump_json(indent=2) if report is not None else "No host report completed."
        raise DigitalTwinUniverseError(
            "install-timeout",
            f"Docker installation exceeded {timeout_seconds}s. Report so far:\n{partial}",
            "Inspect the report and run `digital-twin-universe check`; raise timeout_seconds before retrying.",
        ) from error


def _apply(
    report: InstallReport,
    session: PlanningSession,
    runner: facts.Runner,
    check_host: Callable[[], HostReport],
    cwd: Path,
) -> None:
    index = 0
    repaired = False
    while index < len(report.steps):
        step = report.steps[index]
        if not step.unattended:
            run.skip_remaining(report.steps[index + 1 :], "An earlier step needs a person first.")
            break
        try:
            run.run_step(step, index + 1, len(report.steps), session.host.platform, runner, session.deadline, cwd)
        except subprocess.TimeoutExpired:
            step.status = "failed"
            step.reason = "The install deadline expired while this step was running."
            raise
        if step.status == "failed":
            if repaired or session.session_id is None:
                run.skip_remaining(report.steps[index + 1 :], "An earlier unattended step failed.")
                report.docker = check_host()
                report.outcome = "failed"
                report.summary = f"Docker installation stopped at: {step.title}."
                report.next = "read the failed step's reason and follow its source page before retrying"
                if session.session_id is None:
                    report.notes.append(
                        "No planning session to resume for a repair: the intelligence returned no session_id, "
                        "or the saved plan was made through another agent provider."
                    )
                return
            repaired = True
            try:
                repair = session.plan(failure=report.model_dump_json(indent=2))
            except DigitalTwinUniverseError as error:
                report.outcome = "failed"
                run.skip_remaining(report.steps[index + 1 :], "The repair plan was rejected.")
                raise DigitalTwinUniverseError(
                    error.code,
                    f"{error.message}\nReport so far:\n{report.model_dump_json(indent=2)}",
                    error.remedy,
                ) from error
            report.steps = [*report.steps[: index + 1], *repair.steps]
            report.notes.extend(repair.notes)
            report.method = repair.method
            report.next = repair.next
        index += 1
    run.poll_check(check_host, report, session.deadline)
