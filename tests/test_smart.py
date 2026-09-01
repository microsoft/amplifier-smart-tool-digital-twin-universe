"""The model-backed capabilities, driven by a stand-in for the model.

The seam is a protocol, so a test supplies its own implementation and every
model-backed path runs end to end with no provider configured and no tokens spent.
That is the same seam a different provider would arrive through.
"""

import dataclasses
import json

import pytest

from amplifier_digital_twin_universe import doctor, install, manager
from amplifier_digital_twin_universe.create import create_profile
from amplifier_digital_twin_universe.errors import GenerationFailedError, NoProviderError
from amplifier_digital_twin_universe.intelligence import ModelRequest, ModelResult

_PROFILE = """base:
  image: ubuntu:24.04
provision:
  setup_cmds:
    - apt-get update
"""


class Scripted:
    """An Intelligence that replies from a script and records what it was asked."""

    implementation = "scripted"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def available_providers(self) -> list[str]:
        return ["scripted"]

    def preflight(self, provider: str | None = None) -> str:
        return provider or "scripted"

    def run(self, request: ModelRequest) -> ModelResult:
        self.prompts.append(request.prompt)
        reply = self.replies.pop(0) if self.replies else ""
        return ModelResult(text=reply, provider="scripted", model="scripted-1", tokens_in=10, tokens_out=20)


class Unconfigured:
    """An Intelligence with nothing configured, which is a refusal and not a fallback."""

    implementation = "unconfigured"

    def available_providers(self) -> list[str]:
        return []

    def preflight(self, provider: str | None = None) -> str:
        raise NoProviderError("No model provider is configured.", "Set ANTHROPIC_API_KEY and run again.")

    def run(self, request: ModelRequest) -> ModelResult:
        raise AssertionError("preflight must refuse before a prompt is ever built")


def _fence(document: dict) -> str:
    return f"```json\n{json.dumps(document)}\n```"


# ------------------------------------------------------------------ refusal


@pytest.mark.parametrize(
    "invoke",
    [
        lambda engine: create_profile("a web service", intelligence=engine),
        lambda engine: install.plan_install(intelligence=engine),
        lambda engine: doctor.diagnose("no network", intelligence=engine),
        lambda engine: manager.manage("list everything", intelligence=engine),
    ],
)
def test_a_model_backed_capability_refuses_rather_than_degrades(invoke) -> None:
    with pytest.raises(NoProviderError) as raised:
        invoke(Unconfigured())
    assert raised.value.remedy


# ----------------------------------------------------------- create-profile


def test_a_drafted_profile_is_accepted_only_after_the_loader_accepts_it() -> None:
    engine = Scripted(f"Here you go.\n```yaml\n{_PROFILE}```")
    draft = create_profile("a container that updates apt", intelligence=engine)
    assert draft.attempts == 1
    assert "ubuntu:24.04" in draft.yaml_text
    assert draft.provider == "scripted"


def test_a_rejected_draft_is_returned_to_the_model_with_the_findings() -> None:
    engine = Scripted(
        "```yaml\ndescription: no image here\n```",
        f"```yaml\n{_PROFILE}```",
    )
    draft = create_profile("a container", intelligence=engine)
    assert draft.attempts == 2
    assert "rejected" in engine.prompts[1]
    assert "base.image" in engine.prompts[1]


def test_a_spent_budget_reports_every_attempt_rather_than_a_bad_profile() -> None:
    engine = Scripted("no fenced block at all", "still nothing")
    with pytest.raises(GenerationFailedError) as raised:
        create_profile("a container", max_attempts=2, intelligence=engine)
    assert len(raised.value.attempts) == 2


def test_the_worked_examples_travel_with_the_request() -> None:
    """A model that has never seen a launching profile writes one that does not."""
    engine = Scripted(f"```yaml\n{_PROFILE}```")
    create_profile("a web service", intelligence=engine)
    assert "# Worked examples" in engine.prompts[0]
    assert "readiness" in engine.prompts[0]


# ------------------------------------------------------------------ install


def test_an_install_plan_carries_the_evidence_it_was_built_from() -> None:
    engine = Scripted(
        _fence(
            {
                "summary": "incus is missing",
                "steps": [
                    {
                        "title": "Install incus",
                        "why": "nothing launches without it",
                        "commands": ["apt install incus"],
                        "verify": "incus version",
                    }
                ],
                "notes": [],
            }
        )
    )
    plan = install.plan_install(goal="test a web service", intelligence=engine)
    assert plan.steps[0].commands == ["apt install incus"]
    assert plan.host.platform
    assert plan.to_dict()["host"]["prerequisites"]


def test_an_install_plan_missing_its_steps_is_sent_back_for_repair() -> None:
    engine = Scripted(_fence({"summary": "incus is missing"}), _fence({"summary": "incus is missing", "steps": []}))
    plan = install.plan_install(intelligence=engine)
    assert plan.steps == []
    assert "'steps' is missing" in engine.prompts[1]


# ------------------------------------------------------------------- doctor


def test_a_diagnosis_returns_the_measurements_beside_the_reading() -> None:
    engine = Scripted(
        _fence(
            {
                "summary": "the runtime is unreachable",
                "findings": [
                    {
                        "issue": "no environment launches",
                        "cause": "the daemon does not answer this user",
                        "confidence": "high",
                        "remedy": ["join the incus-admin group"],
                        "commands": ["sudo usermod -aG incus-admin $USER"],
                    }
                ],
            }
        )
    )
    diagnosis = doctor.diagnose("nothing launches", evidence={"host": {"platform": "linux"}}, intelligence=engine)
    assert diagnosis.findings[0].confidence == "high"
    assert diagnosis.evidence == {"host": {"platform": "linux"}}


def test_a_finding_with_an_invented_confidence_is_sent_back_for_repair() -> None:
    bad = {"issue": "i", "cause": "c", "confidence": "certain", "remedy": ["r"]}
    good = {"issue": "i", "cause": "c", "confidence": "low", "remedy": ["r"]}
    engine = Scripted(
        _fence({"summary": "s", "findings": [bad]}),
        _fence({"summary": "s", "findings": [good]}),
    )
    diagnosis = doctor.diagnose("something", evidence={}, intelligence=engine)
    assert diagnosis.findings[0].confidence == "low"
    assert "confidence must be one of" in engine.prompts[1]


def test_a_failed_measurement_becomes_evidence_rather_than_an_aborted_diagnosis(monkeypatch) -> None:
    from amplifier_digital_twin_universe.errors import MissingPrerequisiteError
    from amplifier_digital_twin_universe.prerequisites import HostReport

    def explode() -> None:
        raise MissingPrerequisiteError("incus is unreachable.", "Install incus.")

    monkeypatch.setattr("amplifier_digital_twin_universe.environments.list_environments", explode)
    host = HostReport(
        platform="linux",
        supported=True,
        ready=True,
        can_launch=True,
        prerequisites=[],
        model_providers=[],
        environments=None,
        notes=[],
    )
    evidence = doctor.gather_evidence(host=host)
    assert evidence["environments"]["error"]["code"] == "missing_prerequisite"


# ------------------------------------------------------------------- manage


def test_a_plan_changes_nothing_until_it_is_confirmed(monkeypatch) -> None:
    monkeypatch.setattr(
        "amplifier_digital_twin_universe.manager.environments.destroy",
        lambda _id: pytest.fail("an unconfirmed plan must not run"),
    )
    engine = Scripted(
        _fence(
            {
                "summary": "tear down the stopped one",
                "steps": [{"action": "destroy", "arguments": {"environment_id": "dtu-1"}, "why": "it is stopped"}],
            }
        )
    )
    plan = manager.manage("tear down the stopped one", intelligence=engine)
    assert plan.mutating is True
    assert plan.executed is False
    assert plan.steps[0].ran is False


def test_a_confirmed_plan_runs_each_step_through_the_deterministic_capability() -> None:
    engine = Scripted(
        _fence(
            {
                "summary": "look at one environment",
                "steps": [{"action": "status", "arguments": {"environment_id": "dtu-1"}, "why": "the request asks"}],
            }
        )
    )
    recorded: list[str] = []
    original = manager.ACTIONS["status"]
    patched = dataclasses.replace(
        original, run=lambda environment_id: recorded.append(environment_id) or {"id": environment_id}
    )
    manager.ACTIONS["status"] = patched
    try:
        plan = manager.manage("show me dtu-1", confirmed=True, intelligence=engine)
    finally:
        manager.ACTIONS["status"] = original

    assert recorded == ["dtu-1"]
    assert plan.executed is True
    assert plan.complete is True
    assert plan.steps[0].ok is True


def test_a_confirmed_plan_stops_at_the_first_failure() -> None:
    engine = Scripted(
        _fence(
            {
                "summary": "two steps",
                "steps": [
                    {"action": "status", "arguments": {"environment_id": "gone"}, "why": "first"},
                    {"action": "status", "arguments": {"environment_id": "dtu-2"}, "why": "second"},
                ],
            }
        )
    )
    from amplifier_digital_twin_universe.errors import EnvironmentNotFoundError

    def explode(environment_id: str) -> None:
        raise EnvironmentNotFoundError(f"Environment not found: {environment_id}", "Run list.")

    original = manager.ACTIONS["status"]
    manager.ACTIONS["status"] = dataclasses.replace(original, run=explode)
    try:
        plan = manager.manage("look at both", confirmed=True, intelligence=engine)
    finally:
        manager.ACTIONS["status"] = original

    assert plan.complete is False
    assert plan.steps[0].error["code"] == "environment_not_found"
    assert plan.steps[1].ran is False


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        ({"action": "rm -rf /", "arguments": {}, "why": "w"}, "is not one of"),
        ({"action": "destroy", "arguments": {}, "why": "w"}, "missing the required parameter"),
        ({"action": "status", "arguments": {"environment_id": "a", "force": True}, "why": "w"}, "has no parameter"),
        ({"action": "run", "arguments": {"environment_id": "a", "command": "ls"}, "why": "w"}, "list of argv strings"),
    ],
)
def test_a_step_outside_the_registry_is_rejected_before_anything_runs(step, expected) -> None:
    """The registry is the fence. A planner cannot widen it by asking."""
    findings = manager._check({"summary": "s", "steps": [step]})
    assert any(expected in finding for finding in findings), findings


def test_every_registered_action_declares_whether_it_mutates() -> None:
    reads = {name for name, action in manager.ACTIONS.items() if not action.mutating}
    assert reads == {"list_environments", "status", "check_readiness"}
