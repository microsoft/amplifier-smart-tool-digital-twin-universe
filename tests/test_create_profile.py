"""The create-profile contract with a canned intelligence and fake universes.

The fake agent writes real files into the draft the way the real one does, so `validate-profile` runs for real
(hence `needs_docker`, for `docker compose config`); launching, executing, and destroying are recorded instead.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
from typing import Any

import pytest
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.capabilities.create_profile import create as create_module
from digital_twin_universe.capabilities.create_profile import facts, reference
from digital_twin_universe.capabilities.install.facts import SubprocessRunner
from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.profile import EXAMPLES_DIRECTORY, XDtu
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.cli import app
from digital_twin_universe.intelligence import interface
from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODELS,
    DEFAULT_INTELLIGENCE_REASONING_EFFORT,
    Check,
    CheckStatus,
    Cleanup,
    Destroyed,
    DigitalTwinUniverseError,
    ExecResult,
    HostReport,
    Prerequisite,
    ProfileDraft,
    Universe,
    Url,
)

pytestmark = pytest.mark.needs_docker

NAME = "probe"
AGENT_ID = f"dtu-{NAME}-a9e1"
HELLO = """\
name: {name}
x-dtu:
  description: A twin that does nothing
services:
  twin:
    image: alpine:3.20
    user: nobody
    command: sleep infinity
    healthcheck:
      test: [CMD, "true"]
"""
Turn = Callable[[AgentRequest], dict[str, Any]]
MakeRepository = Callable[[str, dict[str, str]], Path]


def host_report(ok: bool = True) -> HostReport:
    return HostReport(
        platform="linux",
        ok=ok,
        docker_version="29.0" if ok else None,
        compose_version="5.0" if ok else None,
        prerequisites=[
            Prerequisite(
                name="docker-cli",
                present=ok,
                detail="present" if ok else "missing",
                remedy=None if ok else "Run `digital-twin-universe install`.",
            )
        ],
    )


def check(command: str = "echo dtu-ok", expect: str | None = "dtu-ok", status: CheckStatus = "passed") -> Check:
    return Check(command=command, expect_stdout=expect, status=status, detail=None)


def draft(
    files: list[str] | None = None, universe_id: str | None = AGENT_ID, checks: list[Check] | None = None
) -> dict[str, Any]:
    return ProfileDraft(
        summary="An Alpine twin with nothing installed.",
        files=files if files is not None else ["compose.yaml"],
        environment=[],
        universe_id=universe_id,
        checks=checks if checks is not None else [check()],
        notes=["nothing to install"],
    ).model_dump()


def cleanup(profile_changed: bool = False, destroyed: list[str] | None = None) -> dict[str, Any]:
    return Cleanup(destroyed=destroyed or [], removed=[], profile_changed=profile_changed, notes=[]).model_dump()


@dataclass
class FakeUniverses:
    """Records a launch the way the real one does, so the watcher and `relocate` see the same state directory."""

    launches: list[Path] = field(default_factory=list)
    executions: list[tuple[str, str]] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    answers: dict[str, ExecResult] = field(default_factory=dict)
    launch_error: DigitalTwinUniverseError | None = None
    destroy_error: DigitalTwinUniverseError | None = None
    counter: int = 0

    def launch(self, profile: str | Path, timeout_seconds: int = 600) -> Universe:
        self.launches.append(Path(profile))
        if self.launch_error is not None:
            raise self.launch_error
        self.counter += 1
        record = UniverseRecord(
            id=f"dtu-{NAME}-t00{self.counter}",
            name=NAME,
            description=None,
            profile_path=Path(profile),
            twin_machine="twin",
            created_at=state.now(),
        )
        state.write(record)
        return self.universe(record)

    def universe(self, record: UniverseRecord) -> Universe:
        return Universe(
            id=record.id,
            name=record.name,
            description=record.description,
            profile_path=record.profile_path,
            twin_machine=record.twin_machine,
            state="running",
            services=[],
            urls=[Url(url="http://localhost:8410/", port=8410, path="/", label=None)],
            state_path=record.state_path,
            created_at=record.created_at,
        )

    def execute(
        self, id: str, command: str, user: str | None = None, workdir: str | None = None, timeout_seconds: int = 300
    ) -> ExecResult:
        self.executions.append((id, command))
        return self.answers.get(
            command, ExecResult(exit_code=0, stdout=f"{command.removeprefix('echo ')}\n", stderr="")
        )

    def destroy(self, id: str) -> Destroyed:
        if self.destroy_error is not None:
            raise self.destroy_error
        self.destroyed.append(id)
        state.remove(id)
        return Destroyed(id=id, removed=[])

    def list_universes(self) -> list[Universe]:
        return [self.universe(record) for record in state.read_all()]


@dataclass
class FakeIntelligence:
    """Each turn is a function of the request that may write into the draft and returns the submission."""

    turns: list[Turn]
    implementation: str = "fake"
    requests: list[AgentRequest] = field(default_factory=list)
    preflights: int = 0

    def preflight(self) -> None:
        self.preflights += 1

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if not self.turns:
            pytest.fail(f"the agent was asked more often than scripted; last prompt:\n{request.prompt[-2000:]}")
        return AgentResult(output=self.turns.pop(0)(request), session_id="session-1")


@dataclass
class Harness:
    project: Path
    universes: FakeUniverses
    draft: Path
    final: Path

    def write(self, request: AgentRequest, content: str = HELLO, name: str = NAME, into: Path | None = None) -> None:
        target = into if into is not None else self.draft
        target.mkdir(parents=True, exist_ok=True)
        (target / "compose.yaml").write_text(content.replace("{name}", name))

    def launched_by_agent(self, id: str = AGENT_ID) -> None:
        """What the real agent's `bash` leaves behind: a record that exists for a while, then does not."""
        record = UniverseRecord(
            id=id,
            name=NAME,
            description=None,
            profile_path=self.draft / "compose.yaml",
            twin_machine="twin",
            created_at=state.now(),
        )
        state.write(record)
        time.sleep(create_module.WATCH_INTERVAL * 4)
        state.remove(id)

    def author(self, **overrides: Any) -> Turn:
        def turn(request: AgentRequest) -> dict[str, Any]:
            self.write(request)
            if overrides.get("universe_id", AGENT_ID) is not None:
                self.launched_by_agent(overrides.get("universe_id", AGENT_ID))
            return draft(**overrides)

        return turn

    def create(self, agent: FakeIntelligence, **kwargs: Any) -> Any:
        kwargs.setdefault("project", self.project)
        kwargs.setdefault("name", NAME)
        return lib.create_profile("an alpine twin", intelligence=agent, **kwargs)


@pytest.fixture
def harness(state_root: Path, make_repository: MakeRepository, monkeypatch: pytest.MonkeyPatch) -> Harness:
    project = make_repository("hello-cli", {"README.md": "# hello\n", "pyproject.toml": "[project]\nname='x'\n"})
    universes = FakeUniverses()
    monkeypatch.setattr(lib, "check", host_report)
    monkeypatch.setattr(lib, "launch", universes.launch)
    monkeypatch.setattr(lib, "execute", universes.execute)
    monkeypatch.setattr(lib, "destroy", universes.destroy)
    monkeypatch.setattr(lib, "list_universes", universes.list_universes)
    monkeypatch.setattr(create_module, "WATCH_INTERVAL", 0.02)
    monkeypatch.setattr(reference, "REFERENCE_ROOT", state_root.parent / "reference")
    monkeypatch.setattr(
        reference,
        "ensure_reference",
        lambda runner, deadline: reference.Reference(
            docs=None, dockerfile=None, fetched_at=None, notes=["no docs in tests"]
        ),
    )
    final = project / ".agents" / "digital-twin-universe" / NAME
    return Harness(project, universes, final.with_name(f"{NAME}.draft"), final)


def test_validate_only_promotes_without_launching(harness: Harness) -> None:
    agent = FakeIntelligence([harness.author(universe_id=None, checks=[check(status="pending")])])

    result = harness.create(agent, verify=False)

    assert result.outcome == "validated"
    assert result.path == harness.final
    assert (harness.final / "compose.yaml").is_file()
    assert not harness.draft.exists()
    assert result.attempts == 1
    assert result.validation.ok
    assert [c.status for c in result.checks] == ["skipped"]
    assert result.next == f"launch it with `digital-twin-universe launch --profile {NAME}`"
    assert "no docs in tests" in result.notes
    assert "[runs-as-root]" not in " ".join(result.notes)
    assert not harness.universes.launches
    request = agent.requests[0]
    assert request.workspace is not None
    assert request.workspace.path == harness.project.resolve()
    assert request.writable
    assert request.output_schema == ProfileDraft.model_json_schema()
    assert request.model == DEFAULT_INTELLIGENCE_MODELS["copilot"]
    assert request.reasoning_effort == DEFAULT_INTELLIGENCE_REASONING_EFFORT
    assert "Stop there: do not launch" in request.prompt
    assert str(harness.draft) in request.prompt
    assert '"root":' in request.prompt


def test_verified_profile_is_launched_checked_destroyed_cleaned_and_promoted(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = FakeIntelligence([harness.author(), lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert result.path == harness.final
    assert result.universe_id is None
    assert result.cleanup == Cleanup(destroyed=[], removed=[], profile_changed=False, notes=[])
    assert [c.status for c in result.checks] == ["passed"]
    assert harness.universes.launches == [harness.draft / "compose.yaml"]
    assert harness.universes.executions == [("dtu-probe-t001", "echo dtu-ok")]
    assert harness.universes.destroyed == ["dtu-probe-t001"]
    assert lib.list_universes() == []
    assert all(request.resume in (None, "session-1") for request in agent.requests)
    assert agent.requests[1].resume == "session-1"
    assert "(none)" in agent.requests[1].prompt
    err = capsys.readouterr().err
    assert [line.split(" ")[0] for line in err.splitlines()] == [
        "authoring",
        "validating",
        "launching",
        "checking",
        "destroying",
        "cleaning",
    ]


def test_an_escape_from_the_draft_is_corrected_once_then_rejected(harness: Harness) -> None:
    outside = harness.project / "outside.txt"

    def escape(request: AgentRequest) -> dict[str, Any]:
        harness.write(request)
        outside.write_text("x")
        return draft(files=["compose.yaml", "../../../outside.txt"], universe_id=None)

    agent = FakeIntelligence([escape, escape])

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent, verify=False)

    assert caught.value.code == "profile-rejected"
    assert "draft-escaped" in caught.value.message
    assert len(agent.requests) == 2
    assert "Your submission was not accepted: draft-escaped" in agent.requests[1].prompt
    assert harness.draft.is_dir()
    assert not harness.final.exists()


def test_a_wrong_name_is_sent_back_and_fixed_on_the_second_submission(harness: Harness) -> None:
    def wrong(request: AgentRequest) -> dict[str, Any]:
        harness.write(request, name="something-else")
        return draft(universe_id=None)

    agent = FakeIntelligence([wrong, harness.author(universe_id=None)])

    result = harness.create(agent, verify=False)

    assert result.outcome == "validated"
    assert result.attempts == 2
    assert "must declare `name: probe`" in agent.requests[1].prompt


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"universe_id": None}, "universe_id is null"),
        ({"checks": [check(status="failed")]}, "every check must be passed"),
        ({"checks": []}, "checks is empty"),
    ],
)
def test_missing_evidence_is_a_correction_and_the_tool_never_launches(
    harness: Harness, overrides: dict[str, Any], missing: str
) -> None:
    agent = FakeIntelligence([harness.author(**overrides), harness.author(), lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert result.attempts == 2
    assert missing in agent.requests[1].prompt
    assert len(harness.universes.launches) == 1


def test_a_universe_id_the_tool_never_saw_appear_is_not_evidence(harness: Harness) -> None:
    def invented(request: AgentRequest) -> dict[str, Any]:
        harness.write(request)
        return draft(universe_id="dtu-probe-ffff")

    agent = FakeIntelligence([invented, invented])

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent)

    assert caught.value.code == "profile-rejected"
    assert "not a universe that appeared on this machine" in caught.value.message
    assert not harness.universes.launches


def test_a_check_that_fails_for_the_tool_goes_back_with_both_results(harness: Harness) -> None:
    harness.universes.answers["echo dtu-ok"] = ExecResult(exit_code=0, stdout="something else\n", stderr="")

    def fixed(request: AgentRequest) -> dict[str, Any]:
        harness.universes.answers.clear()
        return harness.author()(request)

    agent = FakeIntelligence([harness.author(), fixed, lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert result.attempts == 2
    prompt = agent.requests[1].prompt
    assert "these checks did not pass for it" in prompt
    assert "exit 0: something else" in prompt
    assert "Your own run of the same checks reported:\n- `echo dtu-ok`: passed" in prompt
    assert harness.universes.destroyed == ["dtu-probe-t001", "dtu-probe-t002"]


def test_a_launch_failure_is_a_failed_attempt_and_the_tool_destroys_what_it_started(harness: Harness) -> None:
    harness.universes.launch_error = DigitalTwinUniverseError(
        "unhealthy", "Container x is unhealthy.", "Fix the healthcheck."
    )
    agent = FakeIntelligence([harness.author(), harness.author(), harness.author()])

    result = harness.create(agent)

    assert result.outcome == "failed"
    assert result.path == harness.draft
    assert harness.draft.is_dir()
    assert not harness.final.exists()
    assert result.attempts == 3
    assert [c.status for c in result.checks] == ["skipped"]
    assert "launch failed [unhealthy]" in agent.requests[1].prompt
    assert "3 submissions were considered" in " ".join(result.notes)
    assert str(harness.draft) in result.next


def test_env_missing_ends_the_run_with_the_variable_to_export(harness: Harness) -> None:
    demanding = HELLO.replace("user: nobody", "environment:\n      DTU_TEST_TOKEN: ${DTU_TEST_TOKEN:?export it}")

    def turn(request: AgentRequest) -> dict[str, Any]:
        harness.write(request, content=demanding)
        return draft(universe_id=None, checks=[check(status="pending")])

    agent = FakeIntelligence([turn])

    result = harness.create(agent)

    assert result.outcome == "failed"
    assert "DTU_TEST_TOKEN" in result.next
    assert result.attempts == 1
    assert not harness.universes.launches


def test_cleanup_that_changes_the_profile_is_verified_again(harness: Harness) -> None:
    def cleaned(request: AgentRequest) -> dict[str, Any]:
        harness.write(request, content=HELLO.replace("A twin that does nothing", "Cleaned"))
        return cleanup(profile_changed=True)

    agent = FakeIntelligence([harness.author(), cleaned])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert len(harness.universes.launches) == 2
    assert harness.universes.destroyed == ["dtu-probe-t001", "dtu-probe-t002"]
    assert "Cleaned" in (harness.final / "compose.yaml").read_text()


def test_a_silent_profile_change_during_cleanup_is_detected_and_a_break_is_a_failed_attempt(harness: Harness) -> None:
    def breaks_it(request: AgentRequest) -> dict[str, Any]:
        harness.write(request, content="# touched during cleanup\n" + HELLO)
        harness.universes.launch_error = DigitalTwinUniverseError("unhealthy", "broken by cleanup", "fix it")
        return cleanup(profile_changed=False)

    def repaired(request: AgentRequest) -> dict[str, Any]:
        harness.universes.launch_error = None
        return harness.author()(request)

    agent = FakeIntelligence([harness.author(), breaks_it, repaired, lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert result.attempts == 2
    assert len(harness.universes.launches) == 3
    assert "your cleanup changed the profile and it no longer passes" in agent.requests[2].prompt
    assert "broken by cleanup" in agent.requests[2].prompt


def test_profile_exists_refuses_without_overwrite_and_a_stale_draft_is_cleared(harness: Harness) -> None:
    harness.final.mkdir(parents=True)
    (harness.final / "compose.yaml").write_text("old")
    harness.draft.mkdir()
    (harness.draft / "leftover.txt").write_text("stale")
    agent = FakeIntelligence([harness.author(universe_id=None)])

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent, verify=False)
    assert caught.value.code == "profile-exists"
    assert not agent.requests
    assert (harness.draft / "leftover.txt").exists()

    result = harness.create(agent, verify=False, overwrite=True)

    assert result.outcome == "validated"
    assert (harness.final / "compose.yaml").read_text() != "old"
    assert not (harness.final / "leftover.txt").exists()


def test_keep_leaves_the_tools_universe_up_and_repoints_its_record(harness: Harness) -> None:
    agent = FakeIntelligence([harness.author(), lambda request: cleanup()])

    result = harness.create(agent, keep=True)

    assert result.outcome == "created"
    assert result.universe_id == "dtu-probe-t001"
    assert [url.url for url in result.urls] == ["http://localhost:8410/"]
    assert not harness.universes.destroyed
    assert state.read("dtu-probe-t001").profile_path.resolve() == (harness.final / "compose.yaml").resolve()
    assert (
        result.next
        == "exec into it with `digital-twin-universe exec --id dtu-probe-t001`; destroy it with `digital-twin-universe destroy --id dtu-probe-t001`"
    )


def test_keep_with_a_profile_changing_cleanup_keeps_the_second_universe(harness: Harness) -> None:
    def cleaned(request: AgentRequest) -> dict[str, Any]:
        harness.write(request, content=HELLO.replace("A twin that does nothing", "Cleaned"))
        return cleanup(profile_changed=True)

    agent = FakeIntelligence([harness.author(), cleaned])

    result = harness.create(agent, keep=True)

    assert result.universe_id == "dtu-probe-t002"
    assert harness.universes.destroyed == ["dtu-probe-t001"]
    assert [record.id for record in state.read_all()] == ["dtu-probe-t002"]


def test_keep_needs_verify_before_anything_runs(harness: Harness) -> None:
    agent = FakeIntelligence([])
    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent, keep=True, verify=False)
    assert caught.value.code == "keep-needs-verify"
    assert agent.preflights == 0


def test_a_universe_that_existed_before_the_run_is_neither_evidence_nor_the_agents_to_clean(harness: Harness) -> None:
    stranger = UniverseRecord(
        id="dtu-probe-0bad",
        name=NAME,
        description=None,
        profile_path=harness.project / "elsewhere" / "compose.yaml",
        twin_machine="twin",
        created_at=state.now(),
    )
    state.write(stranger)
    state.write(stranger.model_copy(update={"id": AGENT_ID, "profile_path": harness.draft / "compose.yaml"}))

    def cites_the_old_one(request: AgentRequest) -> dict[str, Any]:
        harness.write(request)
        return draft()

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(FakeIntelligence([cites_the_old_one, cites_the_old_one]))
    assert caught.value.code == "profile-rejected"
    state.remove(AGENT_ID)

    def cleanup_turn(request: AgentRequest) -> dict[str, Any]:
        assert "(none)" in request.prompt
        assert "dtu-probe-0bad" not in request.prompt
        return cleanup()

    result = harness.create(FakeIntelligence([harness.author(), cleanup_turn]))

    assert result.outcome == "created"
    assert harness.universes.destroyed == ["dtu-probe-t001"]
    assert [record.id for record in state.read_all()] == ["dtu-probe-0bad"]
    assert "dtu-probe-0bad" not in result.next


def test_a_leftover_from_the_draft_lands_in_notes_and_next(harness: Harness) -> None:
    def leaves_it(request: AgentRequest) -> dict[str, Any]:
        harness.write(request)
        state.write(
            UniverseRecord(
                id=AGENT_ID,
                name=NAME,
                description=None,
                profile_path=harness.draft / "compose.yaml",
                twin_machine="twin",
                created_at=state.now(),
            )
        )
        time.sleep(create_module.WATCH_INTERVAL * 4)
        return draft()

    agent = FakeIntelligence([leaves_it, lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert AGENT_ID not in harness.universes.destroyed
    assert result.next.startswith(
        f"destroy what the agent left up with `digital-twin-universe destroy --id {AGENT_ID}`, then launch it"
    )
    assert any(AGENT_ID in note and "never destroys" in note for note in result.notes)
    assert [record.id for record in state.read_all()] == [AGENT_ID]


def test_a_failed_destroy_reports_the_universe_and_the_command(harness: Harness) -> None:
    harness.universes.destroy_error = DigitalTwinUniverseError("docker-unavailable", "Docker went away.", "Run check.")
    agent = FakeIntelligence([harness.author(), lambda request: cleanup()])

    result = harness.create(agent)

    assert result.outcome == "created"
    assert result.universe_id == "dtu-probe-t001"
    assert result.urls == []
    assert any(
        "could not be destroyed" in note and "digital-twin-universe destroy --id dtu-probe-t001" in note
        for note in result.notes
    )


def test_docker_must_be_usable_before_the_model_is_loaded(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib, "check", lambda: host_report(False))
    agent = FakeIntelligence([])
    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent)
    assert caught.value.code == "docker-unavailable"
    assert caught.value.remedy == "Run `digital-twin-universe install`."
    assert agent.preflights == 0


def test_project_must_exist_and_name_must_be_a_slug(harness: Harness) -> None:
    agent = FakeIntelligence([])
    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent, project=harness.project / "missing")
    assert caught.value.code == "project-not-found"
    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(agent, name="Not A Slug")
    assert caught.value.code == "name-invalid"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("a FastAPI app on port 8000 using Postgres", "fastapi-app-port-8000-postgres"),
        ("OpenAI Codex CLI, with my API key", "openai-codex-cli-my-api-key"),
        ("!!!", ""),
    ],
)
def test_names_are_derived_from_the_description(description: str, expected: str) -> None:
    assert create_module.derive_name(description) == expected
    assert len(create_module.derive_name("word " * 100)) <= create_module.NAME_LIMIT


def test_deadline_covers_the_agent(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def slow(request: AgentRequest) -> dict[str, Any]:
        clock[0] = 200.0
        harness.write(request)
        return draft(universe_id=None)

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(FakeIntelligence([slow]), verify=False, timeout_seconds=5)
    assert caught.value.code == "create-timeout"
    assert "No submission was validated" in caught.value.message


def test_a_model_error_is_rejected_not_worked_around(harness: Harness) -> None:
    @dataclass
    class Failing(FakeIntelligence):
        def run(self, request: AgentRequest) -> AgentResult:
            return AgentResult(error="Copilot model unavailable", session_id="session-1")

    with pytest.raises(DigitalTwinUniverseError) as caught:
        harness.create(Failing([]), verify=False)
    assert caught.value.code == "profile-rejected"
    assert "Copilot model unavailable" in caught.value.message


def test_prompt_agrees_with_the_schema_the_validator_and_the_examples() -> None:
    prompt = create_module.AUTHORING_PROMPT.read_text(encoding="utf-8")
    validate_source = (Path(create_module.__file__).parents[1] / "universe" / "validate.py").read_text(encoding="utf-8")

    for name in XDtu.model_fields:
        assert f"{name}:" in prompt, name
    for code in sorted(set(re.findall(r'code="([a-z-]+)"', validate_source))):
        assert f"`{code}`" in prompt, code
    for example in sorted(path.name for path in EXAMPLES_DIRECTORY.iterdir() if path.is_dir()):
        assert f"`{example}/`" in prompt, example
    create_source = Path(create_module.__file__).read_text(encoding="utf-8")
    for placeholder in re.findall(r"(?<!\$)\{([a-z_]+)\}", prompt):
        assert f'"{{{placeholder}}}"' in create_source, placeholder


def test_project_facts_carry_env_names_never_values(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DTU_TEST_API_KEY", "sk-very-secret-value")
    monkeypatch.setenv("DTU_TEST_PLAIN", "ignored")
    gathered = facts.project_facts(harness.project, host_report(), SubprocessRunner(), time.monotonic() + 30)

    dumped = gathered.model_dump_json()
    assert "DTU_TEST_API_KEY" in gathered.credential_env_names
    assert "DTU_TEST_PLAIN" not in gathered.credential_env_names
    assert "sk-very-secret-value" not in dumped
    assert gathered.root == harness.project.resolve()
    assert gathered.project == Path()
    assert gathered.manifests == ["pyproject.toml"]
    assert gathered.readme == "README.md"
    assert "README.md" in gathered.top_level


def test_project_facts_without_a_project_read_nothing(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    gathered = facts.project_facts(None, host_report(), SubprocessRunner(), time.monotonic() + 30)
    assert gathered.project is None
    assert gathered.top_level == []
    assert gathered.manifests == []
    assert gathered.root == tmp_path.resolve()


@dataclass
class RecordingRunner:
    calls: list[list[str]] = field(default_factory=list)
    results: list[ExecResult] = field(default_factory=list)
    on_call: Callable[[list[str]], None] | None = None

    def run(
        self, argv: list[str], timeout_seconds: float, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> ExecResult:
        self.calls.append(argv)
        if self.on_call is not None:
            self.on_call(argv)
        return self.results.pop(0) if self.results else ExecResult(exit_code=0, stdout="", stderr="")


class FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self) -> "FakePage":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_reference_is_cloned_sparsely_then_left_alone_then_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "reference"
    docs = root / reference.DOCS_DIRECTORY
    runner = RecordingRunner(
        on_call=lambda argv: docs.mkdir(parents=True, exist_ok=True) if argv[1] == "clone" else None
    )
    monkeypatch.setattr(reference, "urlopen", lambda request, timeout, context: FakePage("# Dockerfile reference"))
    deadline = time.monotonic() + 60

    first = reference.ensure_reference(runner, deadline, root)

    assert first.docs == docs
    assert first.dockerfile == root / reference.DOCKERFILE_REFERENCE
    assert first.notes == []
    assert first.fetched_at is not None
    assert runner.calls[0][:6] == ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse"]
    assert runner.calls[1] == ["git", "-C", str(docs), "sparse-checkout", "set", *reference.SPARSE_PATHS]
    assert (root / reference.DOCKERFILE_REFERENCE).read_text() == "# Dockerfile reference"

    runner.calls.clear()
    second = reference.ensure_reference(runner, deadline, root)
    assert second == first
    assert not runner.calls

    (root / reference.STAMP).write_text("2000-01-01T00:00:00+00:00")
    third = reference.ensure_reference(runner, deadline, root)
    assert runner.calls == [["git", "-C", str(docs), "pull", "--ff-only", "-q"]]
    assert third.notes == []
    assert third.fetched_at is not None
    assert third.fetched_at.year > 2000


def test_reference_without_git_or_network_reports_absent_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reference.shutil, "which", lambda name: None)

    def offline(request: Any, timeout: float, context: Any) -> FakePage:
        raise reference.URLError("offline")

    monkeypatch.setattr(reference, "urlopen", offline)
    runner = RecordingRunner()

    absent = reference.ensure_reference(runner, time.monotonic() + 60, tmp_path / "reference")

    assert absent.docs is None
    assert absent.dockerfile is None
    assert not runner.calls
    assert any("git is not installed" in note for note in absent.notes)
    assert any("could not be fetched" in note for note in absent.notes)


def test_a_failed_clone_leaves_no_half_clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "reference"
    runner = RecordingRunner(results=[ExecResult(exit_code=128, stdout="", stderr="could not resolve host")])
    monkeypatch.setattr(reference, "urlopen", lambda request, timeout, context: FakePage("# Dockerfile reference"))

    result = reference.ensure_reference(runner, time.monotonic() + 60, root)

    assert result.docs is None
    assert result.dockerfile is not None
    assert "could not resolve host" in result.notes[0]
    assert not (root / reference.STAMP).exists()


@pytest.mark.parametrize(("outcome", "exit_code"), [("created", 0), ("validated", 0), ("failed", 1)])
def test_cli_prints_json_and_exits_by_outcome(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, outcome: str, exit_code: int
) -> None:
    agent = FakeIntelligence([harness.author(universe_id=None)])
    report = harness.create(agent, verify=False)
    report.outcome = outcome
    calls: list[tuple[object, ...]] = []

    def create(*args: object) -> object:
        calls.append(args)
        return report

    monkeypatch.setattr(lib, "create_profile", create)
    result = CliRunner().invoke(
        app,
        [
            "create-profile",
            "--description",
            "an app",
            "--project",
            str(harness.project),
            "--name",
            "app",
            "--keep",
            "--overwrite",
            "--max-attempts",
            "2",
            "--agent-provider",
            "amplifier-agent",
            "--model",
            DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"],
            "--reasoning-effort",
            "medium",
            "--timeout-seconds",
            "50",
        ],
    )
    assert result.exit_code == exit_code, result.output
    assert json.loads(result.stdout)["outcome"] == outcome
    assert calls == [
        (
            "an app",
            harness.project,
            "app",
            True,
            True,
            True,
            2,
            "amplifier-agent",
            DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"],
            "medium",
            50,
        )
    ]


def test_cli_refuses_keep_without_verify(harness: Harness) -> None:
    result = CliRunner().invoke(app, ["create-profile", "--description", "x", "--keep", "--no-verify"])
    assert result.exit_code == 2
    assert not harness.universes.launches


def test_cli_progress_stays_off_stdout(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = FakeIntelligence([harness.author(), lambda request: cleanup()])
    monkeypatch.setattr(interface, "installed", lambda agent_provider: True)
    monkeypatch.setattr(interface, "resolve_intelligence", lambda agent_provider, model: agent)

    result = CliRunner().invoke(
        app, ["create-profile", "--description", "an alpine twin", "--project", str(harness.project), "--name", NAME]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["outcome"] == "created"
    assert "launching ..." in result.stderr
