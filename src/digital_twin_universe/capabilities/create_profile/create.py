"""Create profile: an agent with tools writes a profile and proves it; the tool proves it again and keeps what survives.

The agent works in the project with tools to read, search, and write files and run commands, writing only into a
draft directory. It must validate, launch, check, and destroy on its own before submitting, and the submission has to
carry the evidence. The tool does not believe it: it validates, launches, reruns the checks, and destroys, and only
a draft that passes is renamed into place. Failed attempts cost the agent's launches; the tool never launches a
draft the agent has not already launched and checked.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import NamedTuple

from pydantic import BaseModel, ValidationError
import yaml

from digital_twin_universe.capabilities.create_profile import facts as facts_module
from digital_twin_universe.capabilities.create_profile import reference as reference_module
from digital_twin_universe.capabilities.install.facts import SubprocessRunner, remaining
from digital_twin_universe.capabilities.universe.profile import PROFILE_DIRECTORY
from digital_twin_universe.core.skill import skill_directory
from digital_twin_universe.intelligence.interface import Intelligence, select_intelligence
from digital_twin_universe.intelligence.schemas import AgentRequest, HostWorkspace
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    SLUG_PATTERN,
    AgentProvider,
    Check,
    Cleanup,
    CreatedProfile,
    Destroyed,
    DigitalTwinUniverseError,
    ExecResult,
    HostReport,
    ProfileDraft,
    ProfileReport,
    ReasoningEffort,
    Universe,
)

AUTHORING_PROMPT = Path(__file__).with_name("authoring.md")
CLEANUP_PROMPT = Path(__file__).with_name("cleanup.md")
COMPOSE_FILE = "compose.yaml"
DRAFT_SUFFIX = ".draft"
MAX_CORRECTIONS = 1
LAUNCH_TIMEOUT = 600
CHECK_TIMEOUT = 300
LOG_TAIL = 20
NAME_LIMIT = 40
NAME_STOPWORDS = frozenset({"a", "an", "the", "and", "for", "of", "on", "to", "with", "using"})
UNIVERSE_ID_PATTERN = re.compile(r"^dtu-[a-z0-9-]+-[0-9a-f]{4}$")
# The agent launches through its own `bash`, so a universe it destroyed before submitting leaves no record behind.
# Sampling the state directory while the agent works is how the tool still sees it appear.
WATCH_INTERVAL = 0.25


class Universes(NamedTuple):
    """The universe capabilities the loop drives, handed in by `lib` so this module never reaches Docker itself."""

    validate: Callable[[Path], ProfileReport]
    launch: Callable[[Path, int], Universe]
    execute: Callable[[str, str, int], ExecResult]
    destroy: Callable[[str], Destroyed]
    list_universes: Callable[[], list[Universe]]
    recorded: Callable[[], list[str]]
    relocate: Callable[[str, Path], None]


class Verdict(NamedTuple):
    """The tool's own launch of a draft: the checks as it saw them, and what is left up."""

    checks: list[Check]
    universe: Universe | None
    failure: str | None
    error: DigitalTwinUniverseError | None = None


class UniverseWatch:
    """Which universes appeared since the run began, including any destroyed before the agent submitted."""

    def __init__(self, recorded: Callable[[], list[str]]) -> None:
        self._recorded = recorded
        self._before = set(recorded())
        self.appeared: set[str] = set()

    @contextmanager
    def watching(self) -> Iterator[None]:
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                self._sample()
                stop.wait(WATCH_INTERVAL)

        thread = threading.Thread(target=poll, name="digital-twin-universe-universe-watch", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
            self._sample()

    def _sample(self) -> None:
        with suppress(OSError):
            self.appeared |= set(self._recorded()) - self._before


@dataclass
class Run:
    """One `create_profile` call: what was asked, where the agent works, and what has happened so far."""

    universes: Universes
    agent: Intelligence
    description: str
    name: str
    draft: Path
    facts: facts_module.ProjectFacts
    reference: reference_module.Reference
    digital_twin_universe: str
    verify: bool
    keep: bool
    max_attempts: int
    model: str
    reasoning_effort: ReasoningEffort
    deadline: float
    watch: UniverseWatch
    session_id: str | None = None
    attempts: int = 0
    tool_ids: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    result: CreatedProfile | None = None
    up: Universe | None = None

    @property
    def compose(self) -> Path:
        return self.draft / COMPOSE_FILE

    def ask(self, prompt: str, schema: type[BaseModel]) -> dict[str, object]:
        with self.watch.watching():
            result = self.agent.run(
                AgentRequest(
                    prompt=prompt,
                    model=self.model,
                    reasoning_effort=self.reasoning_effort,
                    workspace=HostWorkspace(path=self.facts.root),
                    writable=True,
                    output_schema=schema.model_json_schema(),
                    resume=self.session_id,
                    timeout_seconds=math.ceil(remaining(self.deadline)),
                )
            )
        remaining(self.deadline)
        self.session_id = result.session_id or self.session_id
        if result.error is not None:
            raise rejected(result.error)
        return result.output or {}

    def author(self, failure: str, correction: str) -> tuple[ProfileDraft | None, str | None]:
        """One authoring submission: the draft when it is usable, otherwise what to send back."""
        self.attempts += 1
        _phase(f"authoring (submission {self.attempts}/{self.max_attempts}) ...")
        output = self.ask(render_authoring(self, failure, correction), ProfileDraft)
        try:
            draft = ProfileDraft.model_validate(output, strict=True)
        except ValidationError as error:
            return None, f"the submission is not a ProfileDraft: {error.errors()[0]['msg']}"
        return draft, self.acceptance_problem(draft)

    def acceptance_problem(self, draft: ProfileDraft) -> str | None:
        """What is wrong with a submission before Docker is touched: files, `name:`, and nothing else yet."""
        root = self.draft.resolve()
        for listed in draft.files:
            resolved = (self.draft / listed).resolve()
            if not resolved.is_relative_to(root):
                return f"draft-escaped: {listed} is outside the draft directory {self.draft}; only files there may be written"
            if not resolved.is_file():
                return f"{listed} is listed in files but does not exist under {self.draft}"
        if not self.compose.is_file():
            return f"{self.compose} does not exist; the profile's Compose file must be written there"
        try:
            with self.compose.open(encoding="utf-8") as file:
                loaded = yaml.safe_load(file)
        except (yaml.YAMLError, UnicodeError) as error:
            return f"{self.compose} is not valid YAML: {error}"
        declared = loaded.get("name") if isinstance(loaded, dict) else None
        if declared != self.name:
            return f"compose.yaml declares `name: {declared}`; it must declare `name: {self.name}`"
        return None

    def evidence_problem(self, draft: ProfileDraft) -> str | None:
        """Whether the agent launched the draft and saw every check pass, which only matters with `verify`."""
        if draft.universe_id is None:
            return "universe_id is null: launch the draft, run every check in it, destroy it, and submit the id"
        if (
            not UNIVERSE_ID_PATTERN.match(draft.universe_id)
            or draft.universe_id not in self.watch.appeared - self.tool_ids
        ):
            return (
                f"universe_id {draft.universe_id!r} is not a universe that appeared on this machine during this run; "
                "submit the `id` that `launch` printed for this draft"
            )
        if not draft.checks:
            return "checks is empty: submit at least one check per claim the summary makes, each run in the twin"
        unproven = [check for check in draft.checks if check.status != "passed"]
        if unproven:
            listed = "; ".join(f"`{check.command}` is {check.status}" for check in unproven)
            return f"every check must be passed before submitting: {listed}. Run them and submit when they pass, or say what you cannot make work"
        return None

    def verify_draft(self, checks: list[Check], hold: bool) -> Verdict:
        """Launch the draft, run the checks, and destroy the universe unless `hold` and every check passed."""
        fresh = [Check(command=c.command, expect_stdout=c.expect_stdout, status="pending", detail=None) for c in checks]
        before = set(self.universes.recorded())
        _phase("launching ...")
        try:
            universe = self.universes.launch(self.compose, min(LAUNCH_TIMEOUT, math.ceil(remaining(self.deadline))))
        except DigitalTwinUniverseError as error:
            for check in fresh:
                check.status, check.detail = "skipped", "The launch failed."
            started = set(self.universes.recorded()) - before
            self.tool_ids |= started
            self.notes += [note for note in map(self.destroy_universe, sorted(started)) if note is not None]
            return Verdict(fresh, None, f"launch failed [{error.code}]: {error.message}\nRemedy: {error.remedy}", error)
        self.tool_ids.add(universe.id)
        _phase(f"checking ({len(fresh)} checks) ...")
        passed = self.run_checks(universe.id, fresh)
        if hold and passed:
            return Verdict(fresh, universe, None)
        note = self.destroy_universe(universe.id)
        if note is not None:
            self.notes.append(note)
        left_up = universe if note is not None else None
        if passed:
            return Verdict(fresh, left_up, None)
        failed = [check for check in fresh if check.status != "passed"]
        failure = "the tool launched the draft and these checks did not pass for it:\n" + "\n".join(
            f"- `{check.command}` (expect_stdout: {check.expect_stdout!r}): {check.detail}" for check in failed
        )
        return Verdict(fresh, left_up, failure)

    def run_checks(self, universe_id: str, checks: list[Check]) -> bool:
        passed = True
        for check in checks:
            try:
                result = self.universes.execute(
                    universe_id, check.command, min(CHECK_TIMEOUT, math.ceil(remaining(self.deadline)))
                )
            except DigitalTwinUniverseError as error:
                check.status, check.detail = "failed", f"[{error.code}] {error.message}"
                passed = False
                continue
            ok = result.exit_code == 0 and (check.expect_stdout is None or check.expect_stdout in result.stdout)
            check.status = "passed" if ok else "failed"
            check.detail = None if ok else f"exit {result.exit_code}: {_tail(result.stdout + result.stderr)}"
            passed &= ok
        return passed

    def destroy_universe(self, id: str) -> str | None:
        """Destroy a universe the tool launched; a failure is a note naming the command, never an exception."""
        _phase(f"destroying {id} ...")
        try:
            self.universes.destroy(id)
        except DigitalTwinUniverseError as error:
            return f"Universe {id} could not be destroyed ({error.message}); run `digital-twin-universe destroy --id {id}`."
        return None

    def cleanup(self) -> Cleanup | None:
        """The cleanup turn; an unusable answer is a note, since the verified profile is not worth losing over it."""
        _phase("cleaning up ...")
        others = self.agent_universes()
        try:
            output = self.ask(render_cleanup(self, others), Cleanup)
            return Cleanup.model_validate(output, strict=True)
        except (DigitalTwinUniverseError, ValidationError) as error:
            self.notes.append(f"The cleanup turn produced no usable answer ({error}); nothing was cleaned up.")
            return None

    def agent_universes(self) -> list[Universe]:
        """Universes launched from the draft that the tool did not launch itself: the agent's, still up."""
        draft = self.draft.resolve()
        return [
            universe
            for universe in self.universes.list_universes()
            if universe.id not in self.tool_ids and Path(universe.profile_path).resolve().is_relative_to(draft)
        ]


def create_profile(
    universes: Universes,
    check_host: Callable[[], HostReport],
    description: str,
    project: Path | None = None,
    name: str | None = None,
    verify: bool = True,
    keep: bool = False,
    overwrite: bool = False,
    max_attempts: int = 3,
    agent_provider: AgentProvider | None = None,
    model: str | None = None,
    reasoning_effort: ReasoningEffort = DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    timeout_seconds: int = 1800,
    intelligence: Intelligence | None = None,
) -> CreatedProfile:
    if keep and not verify:
        raise DigitalTwinUniverseError(
            "keep-needs-verify",
            "keep=True leaves the verified universe running, and verify=False launches none.",
            "Pass verify=True with keep, or drop keep.",
        )
    if project is not None and not project.is_dir():
        raise DigitalTwinUniverseError(
            "project-not-found", f"{project} is not a directory.", "Pass the repository to profile, or no project."
        )
    deadline = time.monotonic() + timeout_seconds
    docker = check_host()
    if not docker.ok:
        missing = next((item for item in docker.prerequisites if not item.present), None)
        raise DigitalTwinUniverseError(
            "docker-unavailable",
            f"Docker is not usable, so no profile can be validated or launched: {missing.detail if missing else ''}",
            missing.remedy
            if missing and missing.remedy
            else "Run `digital-twin-universe check` for the missing prerequisite.",
        )
    profile_name = name if name is not None else derive_name(description)
    if not re.match(SLUG_PATTERN, profile_name):
        raise DigitalTwinUniverseError(
            "name-invalid",
            f"{profile_name!r} is not a profile name: lowercase letters, digits, and single hyphens only.",
            "Pass `name` in that form; it becomes the Compose project name and the universe id.",
        )
    runner = SubprocessRunner()
    facts = facts_module.project_facts(project, docker, runner, deadline)
    final = facts.root / PROFILE_DIRECTORY / profile_name
    draft = final.with_name(f"{profile_name}{DRAFT_SUFFIX}")
    if final.exists() and not overwrite:
        raise DigitalTwinUniverseError(
            "profile-exists",
            f"{final} already exists.",
            f"Pass overwrite=True to replace it, or choose another name; `digital-twin-universe launch --profile {profile_name}` runs it as is.",
        )
    agent, model = select_intelligence(intelligence, agent_provider, model, DEFAULT_INTELLIGENCE_MODELS)
    agent.preflight()
    shutil.rmtree(draft, ignore_errors=True)
    draft.mkdir(parents=True)
    reference = reference_module.ensure_reference(runner, deadline)
    run = Run(
        universes=universes,
        agent=agent,
        description=description,
        name=profile_name,
        draft=draft,
        facts=facts,
        reference=reference,
        digital_twin_universe=facts_module.digital_twin_universe_command(),
        verify=verify,
        keep=keep,
        max_attempts=max_attempts,
        model=model,
        reasoning_effort=reasoning_effort,
        deadline=deadline,
        watch=UniverseWatch(universes.recorded),
    )
    try:
        return _attempts(run, final)
    except (TimeoutError, subprocess.TimeoutExpired) as error:
        if run.up is not None:
            with suppress(DigitalTwinUniverseError):
                universes.destroy(run.up.id)
        partial = (
            run.result.model_dump_json(indent=2)
            if run.result is not None
            else f"No submission was validated; the draft is at {draft}."
        )
        raise DigitalTwinUniverseError(
            "create-timeout",
            f"Creating the profile exceeded {timeout_seconds}s. Report so far:\n{partial}",
            f"Read the draft at {draft} and `digital-twin-universe list`; raise timeout_seconds before retrying.",
        ) from error


def _attempts(run: Run, final: Path) -> CreatedProfile:
    """The loop: author, accept, validate, verify, clean up, and promote; or leave the draft and say so."""
    failure = ""
    correction = ""
    corrections = 0
    problem: str | None = None
    while run.attempts < run.max_attempts:
        draft, problem = run.author(failure, correction)
        report: ProfileReport | None = None
        if problem is None and draft is not None:
            _phase("validating ...")
            report = run.universes.validate(run.compose)
            run.result = _result(run, draft, report)
            env_missing = [finding for finding in report.errors if finding.code == "env-missing"]
            if env_missing:
                return _failed(run, env_missing[0].remedy, env_missing[0].message)
            if not report.ok:
                failure = "validate-profile reported errors on the submitted draft:\n" + _findings(report)
                correction, corrections = "", 0
                continue
            if run.verify:
                problem = run.evidence_problem(draft)
        if problem is not None:
            if corrections >= MAX_CORRECTIONS or run.attempts >= run.max_attempts:
                raise rejected(problem)
            corrections += 1
            correction, failure = problem, ""
            continue
        if draft is None or run.result is None:
            raise rejected("the submission carried no draft")
        correction, corrections = "", 0
        if not run.verify:
            for check in run.result.checks:
                check.status, check.detail = "skipped", "verify=False: not launched."
            run.result.outcome = "validated"
            return _finish(run, _promote(run, final))
        verdict = run.verify_draft(draft.checks, hold=run.keep)
        run.result.checks, run.up = verdict.checks, verdict.universe
        if verdict.failure is not None:
            if verdict.error is not None and verdict.error.code == "env-missing":
                return _failed(run, verdict.error.remedy, verdict.error.message)
            failure = _with_agent_view(verdict, draft)
            continue
        before = _fingerprint(run.draft)
        cleanup = run.cleanup()
        run.result.cleanup = cleanup
        if cleanup is not None:
            run.result.notes.extend(cleanup.notes)
        if (cleanup is not None and cleanup.profile_changed) or _fingerprint(run.draft) != before:
            if run.up is not None:
                note = run.destroy_universe(run.up.id)
                run.notes += [note] if note is not None else []
                run.up = None
            _phase("validating the cleaned draft ...")
            report = run.universes.validate(run.compose)
            run.result.validation = report
            if not report.ok:
                failure = "your cleanup changed the profile and it no longer validates:\n" + _findings(report)
                continue
            verdict = run.verify_draft(draft.checks, hold=run.keep)
            run.result.checks, run.up = verdict.checks, verdict.universe
            if verdict.failure is not None:
                failure = "your cleanup changed the profile and it no longer passes:\n" + _with_agent_view(
                    verdict, draft
                )
                continue
        run.result.outcome = "created"
        path = _promote(run, final)
        if run.keep and run.up is not None:
            run.universes.relocate(run.up.id, path / COMPOSE_FILE)
        return _finish(run, path)
    if problem is not None or run.result is None:
        raise rejected(problem or "no submission was ever validated")
    run.result.notes.append(
        f"{run.max_attempts} submissions were considered and none passed; the last is at {run.draft}."
    )
    return _failed(
        run,
        f"read {run.compose} and the notes, fix it by hand, and `digital-twin-universe launch --profile {run.draft}`",
        None,
    )


def _result(run: Run, draft: ProfileDraft, report: ProfileReport) -> CreatedProfile:
    """A report for the submission just validated; the loop fills in the verdict, cleanup, and outcome."""
    return CreatedProfile(
        outcome="failed",
        name=run.name,
        path=run.draft,
        summary=draft.summary,
        files=draft.files,
        environment=draft.environment,
        validation=report,
        checks=[check.model_copy() for check in draft.checks],
        universe_id=None,
        urls=[],
        attempts=run.attempts,
        cleanup=None,
        notes=list(draft.notes),
        next="",
    )


def _failed(run: Run, next: str, note: str | None) -> CreatedProfile:
    if run.result is None:
        raise rejected("no submission was ever validated")
    run.result.outcome = "failed"
    run.result.next = next
    if note is not None:
        run.result.notes.append(note)
    return _finish(run, run.draft)


def _finish(run: Run, path: Path) -> CreatedProfile:
    """The report as the caller gets it: where the profile is, what is still up, and the one thing to do next."""
    result = run.result
    if result is None:
        raise rejected("no submission was ever validated")
    result.path = path
    result.attempts = run.attempts
    result.notes.extend(run.notes)
    run.notes.clear()
    result.notes.extend(run.reference.notes)
    result.notes.extend(f"[{finding.code}] {finding.message}" for finding in result.validation.warnings)
    kept = run.keep and result.outcome == "created" and run.up is not None
    if run.up is not None:
        result.universe_id = run.up.id
        result.urls = run.up.urls if kept else []
        if not kept:
            result.notes.append(
                f"Universe {run.up.id} launched by the tool is still up; run `digital-twin-universe destroy --id {run.up.id}`."
            )
    if kept and run.up is not None:
        result.next = f"exec into it with `digital-twin-universe exec --id {run.up.id}`; destroy it with `digital-twin-universe destroy --id {run.up.id}`"
    elif result.outcome in ("created", "validated"):
        result.next = f"launch it with `digital-twin-universe launch --profile {run.name}`"
    leftovers = run.agent_universes()
    result.notes.extend(
        f"Universe {universe.id} was launched by the agent from the draft and left {universe.state}; "
        "the tool never destroys what it did not launch."
        for universe in leftovers
    )
    if leftovers:
        commands = "; ".join(f"`digital-twin-universe destroy --id {universe.id}`" for universe in leftovers)
        result.next = f"destroy what the agent left up with {commands}, then {result.next}"
    return result


def _promote(run: Run, final: Path) -> Path:
    if final.exists():
        shutil.rmtree(final)
    run.draft.rename(final)
    return final


def _with_agent_view(verdict: Verdict, draft: ProfileDraft) -> str:
    """The tool's failure beside the agent's own result for the same checks, so the discrepancy is visible."""
    assert verdict.failure is not None
    agent_view = "\n".join(
        f"- `{check.command}`: {check.status}" + (f" ({check.detail})" if check.detail else "")
        for check in draft.checks
    )
    return f"{verdict.failure}\n\nYour own run of the same checks reported:\n{agent_view}"


def _findings(report: ProfileReport) -> str:
    return "\n".join(f"- [{finding.code}] {finding.message} Remedy: {finding.remedy}" for finding in report.errors)


def _fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in directory.rglob("*") if path.is_file()):
        digest.update(str(path.relative_to(directory)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-LOG_TAIL:]) or "no output"


def _phase(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def rejected(problem: str) -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        "profile-rejected",
        f"The agent's submission was not usable: {problem}",
        "Rerun `digital-twin-universe create-profile` with a more specific description, or write the profile by hand from the examples.",
    )


def derive_name(description: str) -> str:
    """A slug from the description's first words, so `a FastAPI app on port 8000` becomes `fastapi-app-port-8000`."""
    name = ""
    for word in re.findall(r"[a-z0-9]+", description.lower()):
        if word in NAME_STOPWORDS:
            continue
        candidate = f"{name}-{word}" if name else word
        if len(candidate) > NAME_LIMIT:
            break
        name = candidate
    return name


def render_authoring(run: Run, failure: str, correction: str) -> str:
    with AUTHORING_PROMPT.open(encoding="utf-8") as file:
        template = file.read()
    examples = skill_directory() / "examples"
    previous = (
        "## Previous submission\n"
        "Continue this SAME session. Your previous submission was validated and launched by the tool and did not\n"
        f"pass:\n<failure>\n{failure}\n</failure>\n"
        "Fix the cause, prove the draft again from the first step, and submit again.\n"
        if failure
        else ""
    )
    fix = (
        f"## Correction\nYour submission was not accepted: {correction}\n"
        "Fix exactly that, prove the draft again where the fix changed it, and submit again.\n"
        if correction
        else ""
    )
    return (
        template.replace("{description}", run.description.strip())
        .replace("{attempt}", str(run.attempts))
        .replace("{max_attempts}", str(run.max_attempts))
        .replace("{draft}", str(run.draft))
        .replace("{name}", run.name)
        .replace("{digital_twin_universe}", run.digital_twin_universe)
        .replace("{proof}", _proof(run))
        .replace("{root_relative}", os.path.relpath(run.facts.root, run.draft))
        .replace("{reference}", _reference(run.reference))
        .replace("{examples}", str(examples))
        .replace("{facts}", run.facts.model_dump_json(indent=2))
        .replace("{failure}", previous)
        .replace("{correction}", fix)
    )


def _proof(run: Run) -> str:
    validate = f'`{run.digital_twin_universe} validate-profile --profile {run.draft}` until the JSON says `"ok": true`; read each error\'s `remedy`'
    if not run.verify:
        return (
            "\n## Proving it\n\n"
            f"Run {validate}. Stop there: do not launch. Submit with `universe_id: null` and every check\n"
            "`pending`; the checks are what a later launch should be tested with.\n"
        )
    return (
        "\n## Proving it\n\n"
        "You are not done until you have launched the draft, run every check in it, and destroyed it. In order,\n"
        "every time the profile changes:\n\n"
        f"1. {validate}.\n"
        f"2. `{run.digital_twin_universe} launch --profile {run.draft}`; it prints the universe as JSON with its `id`, and Compose's\n"
        "   progress on stderr. On `build-failed` or `unhealthy`, read the error's tail, then\n"
        f"   `docker compose -p <id> logs <service>`, then `{run.digital_twin_universe} destroy --id <id>`, fix, and launch again.\n"
        f"3. Each check: `{run.digital_twin_universe} exec --id <id> --command '<command>'`; the JSON carries `exit_code`, `stdout`,\n"
        "   and `stderr`, and the exit code is the command's own. Record `status` and `detail` from it.\n"
        f"4. `{run.digital_twin_universe} destroy --id <id>`.\n\n"
        "A submission without `universe_id`, or with any check not `passed`, is sent back. A universe you launched\n"
        "and did not destroy is a defect you will be asked about. The tool then launches the draft itself and reruns\n"
        "your checks; only a profile that passes for it is kept.\n"
    )


def _reference(reference: reference_module.Reference) -> str:
    lines: list[str] = []
    if reference.docs is not None:
        lines.append(f"- Compose file reference: `{reference.docs / 'content' / 'reference' / 'compose-file'}`")
        lines.append(f"- Compose manual, how-tos included: `{reference.docs / 'content' / 'manuals' / 'compose'}`")
        lines.append(f"- Writing images and build contexts: `{reference.docs / 'content' / 'manuals' / 'build'}`")
    if reference.dockerfile is not None:
        lines.append(f"- Dockerfile instruction reference: `{reference.dockerfile}`")
    if not lines:
        lines.append("- Absent: the documentation could not be fetched on this machine.")
    lines += [f"- {note}" for note in reference.notes]
    return "\n".join(lines)


def render_cleanup(run: Run, others: list[Universe]) -> str:
    with CLEANUP_PROMPT.open(encoding="utf-8") as file:
        template = file.read()
    listed = "\n".join(f"   - `{universe.id}` ({universe.state})" for universe in others) or "   (none)"
    return (
        template.replace("{universes}", listed)
        .replace("{digital_twin_universe}", run.digital_twin_universe)
        .replace("{draft}", str(run.draft))
    )
