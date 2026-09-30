"""Installation contract, entirely offline: canned intelligence, captured pages, and a recording host runner."""

from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request

import pytest
from typer.testing import CliRunner

from digital_twin_universe import lib
from digital_twin_universe.capabilities.install import docs, facts, run
from digital_twin_universe.capabilities.install import install as install_module
from digital_twin_universe.cli import app
from digital_twin_universe.intelligence import interface
from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.schemas import (
    DEFAULT_INTELLIGENCE_MODEL,
    DEFAULT_INTELLIGENCE_MODELS,
    DigitalTwinUniverseError,
    ExecResult,
    HostReport,
    InstallMethod,
    InstallOutcome,
    InstallPlan,
    InstallStep,
    Platform,
    Prerequisite,
)

REAL_RUNNER = facts.SubprocessRunner
HOST_FACTS = facts.host_facts
FETCH_DOCS = docs.fetch_docs
UBUNTU = f"{docs.ENGINE}ubuntu/"


def host_report(ok: bool = False, platform: Platform = "linux") -> HostReport:
    return HostReport(
        platform=platform,
        ok=ok,
        docker_version="29.0" if ok else None,
        compose_version="5.0" if ok else None,
        prerequisites=[Prerequisite(name="docker-cli", present=ok, detail="present" if ok else "missing")],
    )


def step(
    command: str = "sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce",
    unattended: bool = True,
    source: str = UBUNTU,
) -> InstallStep:
    return InstallStep(
        title=command,
        commands=[command],
        source=source,
        unattended=unattended,
        status="pending" if unattended else "manual",
        reason=None if unattended else "Needs a new login",
    )


def plan(steps: list[InstallStep] | None = None, method: InstallMethod | None = "docker-engine-apt") -> InstallPlan:
    return InstallPlan(
        summary="Install the missing Docker Engine packages.",
        method=method,
        steps=steps if steps is not None else [step()],
        next="log out and back in, then run `digital-twin-universe check`",
        notes=["Use -y, DEBIAN_FRONTEND=noninteractive and sudo -n to avoid prompts."],
    )


def fixture_pages(host: facts.HostFacts) -> dict[str, str]:
    return {source: f"# Page {source}" for source in docs.page_urls(host)}


@dataclass
class CommandCall:
    argv: list[str]
    timeout: float
    env: dict[str, str] | None
    cwd: Path | None


@dataclass
class RecordingRunner:
    calls: list[CommandCall] = field(default_factory=list)
    results: list[ExecResult | subprocess.TimeoutExpired] = field(default_factory=list)

    def run(
        self, argv: list[str], timeout_seconds: float, env: dict[str, str] | None = None, cwd: Path | None = None
    ) -> ExecResult:
        self.calls.append(CommandCall(argv, timeout_seconds, env, cwd))
        result = self.results.pop(0) if self.results else ExecResult(exit_code=0, stdout="", stderr="")
        if isinstance(result, subprocess.TimeoutExpired):
            raise result
        return result


@dataclass
class FakeIntelligence:
    implementation: str = "fake"
    requests: list[AgentRequest] = field(default_factory=list)
    results: list[AgentResult] = field(
        default_factory=lambda: [AgentResult(output=plan().model_dump(), session_id="session-1")]
    )
    preflights: int = 0
    preflight_error: DigitalTwinUniverseError | None = None
    on_run: Callable[[], None] | None = None

    def preflight(self) -> None:
        self.preflights += 1
        if self.preflight_error is not None:
            raise self.preflight_error

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if self.on_run is not None:
            self.on_run()
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]

    def plans(self, *plans: InstallPlan) -> None:
        self.results = [AgentResult(output=value.model_dump(), session_id="session-1") for value in plans]


@dataclass
class Harness:
    host: facts.HostFacts
    runner: RecordingRunner = field(default_factory=RecordingRunner)
    agent: FakeIntelligence = field(default_factory=FakeIntelligence)
    checks: list[HostReport] = field(default_factory=lambda: [host_report()])
    fetches: int = 0
    verifications: int = 0
    verify_error: DigitalTwinUniverseError | None = None

    def check(self) -> HostReport:
        return self.checks.pop(0) if len(self.checks) > 1 else self.checks[0]

    def gather(
        self, docker: HostReport, accept_license: bool, runner: facts.Runner, deadline: float
    ) -> facts.HostFacts:
        return self.host.model_copy(update={"docker_partial": docker, "accept_license": accept_license})

    def fetch(self, host: facts.HostFacts, deadline: float) -> dict[str, str]:
        self.fetches += 1
        return fixture_pages(host)

    def verify(self) -> None:
        self.verifications += 1
        if self.verify_error is not None:
            raise self.verify_error


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    host = facts.HostFacts(
        platform="linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_CODENAME": "noble"},
        wsl=False,
        systemd=True,
        sudo_noninteractive=True,
        euid_root=False,
        wsl_version=None,
        elevated=False,
        windows_build=None,
        docker_partial=host_report(),
        accept_license=False,
    )
    harness = Harness(host)
    monkeypatch.setattr(install_module, "PLAN_PATH", tmp_path / "install" / "plan.json")
    monkeypatch.setattr(interface, "installed", lambda agent_provider: True)
    monkeypatch.setattr(interface, "resolve_intelligence", lambda agent_provider, model: harness.agent)
    monkeypatch.setattr(lib, "check", harness.check)
    monkeypatch.setattr(lib, "_verify_universe", harness.verify)
    monkeypatch.setattr(facts, "host_facts", harness.gather)
    monkeypatch.setattr(facts, "SubprocessRunner", lambda: harness.runner)
    monkeypatch.setattr(docs, "fetch_docs", harness.fetch)
    return harness


def test_ready_never_calls_preflight_fetch_or_runner(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.checks = [host_report(True)]
    monkeypatch.setattr(interface, "resolve_intelligence", lambda agent_provider, model: pytest.fail("loaded"))

    report = lib.install(apply=True)

    assert report.outcome == "ready"
    assert report.next == "nothing"
    assert not report.steps
    assert not report.docs
    assert not harness.runner.calls
    assert not harness.agent.requests
    assert not harness.fetches
    assert not install_module.PLAN_PATH.exists()


def test_planned_is_tool_free_structured_and_runs_nothing(harness: Harness) -> None:
    report = lib.install(intelligence=harness.agent, model=DEFAULT_INTELLIGENCE_MODEL, reasoning_effort="medium")

    assert report.outcome == "planned"
    assert report.next == "rerun with --yes"
    assert harness.agent.preflights == 1
    assert not harness.runner.calls
    request = harness.agent.requests[0]
    assert request.workspace is None
    assert not request.writable
    assert request.output_schema == InstallPlan.model_json_schema()
    assert request.model == DEFAULT_INTELLIGENCE_MODEL
    assert request.reasoning_effort == "medium"
    assert '"VERSION_CODENAME": "noble"' in request.prompt
    assert fixture_pages(harness.host)[UBUNTU] in request.prompt
    assert "accept_license is false" in request.prompt
    assert "Never include --accept-license" in request.prompt
    assert "no tools or workspace" in request.prompt
    assert "Stop automation at the first manual step" in request.prompt
    with install_module.PLAN_PATH.open(encoding="utf-8") as file:
        saved = json.load(file)
    assert saved["facts_hash"] == install_module.facts_hash(harness.host)
    assert saved["session_id"] == "session-1"
    assert saved["plan"]["steps"] == [value.model_dump() for value in report.steps]


def test_yes_runs_in_order_and_stops_at_first_manual(harness: Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.agent.plans(plan([step("first"), step("second"), step("# log out", False), step("never")]))

    report = lib.install(apply=True, intelligence=harness.agent)

    assert report.outcome == "action-required"
    assert [value.status for value in report.steps] == ["done", "done", "manual", "skipped"]
    assert [call.argv[-1] for call in harness.runner.calls] == ["first", "second"]
    assert all(call.argv[:2] == ["sh", "-ec"] for call in harness.runner.calls)
    assert all(call.env and call.env["DEBIAN_FRONTEND"] == "noninteractive" for call in harness.runner.calls)
    assert all(0 < call.timeout <= 1200 for call in harness.runner.calls)
    assert len({call.cwd for call in harness.runner.calls}) == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err.splitlines() == ["[1/4] first ...", "[2/4] second ..."]


def test_failed_step_repairs_same_session_once_and_preserves_history(harness: Harness) -> None:
    harness.agent.plans(
        plan([step("completed"), step("fails"), step("obsolete")]), plan([step("repair"), step("never")])
    )
    harness.runner.results = [
        ExecResult(exit_code=0, stdout="", stderr=""),
        ExecResult(exit_code=2, stdout="\n".join(str(index) for index in range(50)), stderr="repository error"),
        ExecResult(exit_code=3, stdout="", stderr="repair failed"),
    ]

    report = lib.install(apply=True, intelligence=harness.agent)

    assert report.outcome == "failed"
    assert [call.argv[-1] for call in harness.runner.calls] == ["completed", "fails", "repair"]
    assert [value.status for value in report.steps] == ["done", "failed", "failed", "skipped"]
    assert len(harness.agent.requests) == 2
    assert harness.agent.requests[1].resume == "session-1"
    assert "repository error" in harness.agent.requests[1].prompt
    assert harness.agent.requests[1].workspace is None
    assert report.steps[1].reason
    assert len(report.steps[1].reason.splitlines()) == run.LOG_TAIL


def test_repair_success_can_report_installed(harness: Harness) -> None:
    harness.agent.plans(plan([step("fails")]), plan([step("repair")]))
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="retry me")]
    harness.checks = [host_report(), host_report(True)]

    report = lib.install(apply=True, intelligence=harness.agent)

    assert report.outcome == "installed"
    assert report.docker.ok
    assert report.next == "nothing"
    assert [value.status for value in report.steps] == ["failed", "done", "done"]
    assert harness.verifications == 1
    assert "universe" in report.steps[-1].title


def test_installed_is_only_claimed_when_a_universe_runs(harness: Harness) -> None:
    harness.checks = [host_report(), host_report(True)]
    harness.verify_error = DigitalTwinUniverseError("unhealthy", "The twin never became healthy.", "Read its logs.")
    report = lib.install(apply=True, intelligence=harness.agent)
    assert report.outcome == "failed"
    assert report.docker.ok
    assert report.steps[-1].status == "failed"
    assert "never became healthy" in (report.steps[-1].reason or "")


def test_no_verification_without_a_change(harness: Harness) -> None:
    harness.checks = [host_report(True)]
    assert lib.install(apply=True).outcome == "ready"
    harness.checks = [host_report()]
    harness.agent.plans(plan([step("# log out", False)]))
    assert lib.install(intelligence=harness.agent).outcome == "planned"
    assert lib.install(apply=True, intelligence=harness.agent).outcome == "action-required"
    assert harness.verifications == 0


def test_no_session_id_reports_failure_without_starting_another_agent(harness: Harness) -> None:
    harness.agent.results = [AgentResult(output=plan().model_dump())]
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="failed")]
    report = lib.install(apply=True, intelligence=harness.agent)
    assert report.outcome == "failed"
    assert len(harness.agent.requests) == 1
    assert "no session_id" in report.notes[-1]


@pytest.mark.parametrize(
    "source",
    [
        "https://evil.invalid/install",
        "https://docs.docker.com.evil.invalid/install",
        "https://docs.docker.com@evil.invalid/install",
        "http://docs.docker.com/engine/install/ubuntu/",
        "https://docs.docker.com/engine/install/unprovided/",
    ],
)
def test_invalid_sources_are_rejected_after_retries(harness: Harness, source: str) -> None:
    harness.agent.plans(plan([step(source=source)]))
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(apply=True, intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"
    assert len(harness.agent.requests) == 3
    assert all(request.resume == "session-1" for request in harness.agent.requests[1:])
    assert not harness.runner.calls
    assert not install_module.PLAN_PATH.exists()


@pytest.mark.parametrize("commands", [[], [""], ["  "]])
def test_empty_commands_are_rejected(harness: Harness, commands: list[str]) -> None:
    invalid = step()
    invalid.commands = commands
    harness.agent.plans(plan([invalid]))
    with pytest.raises(DigitalTwinUniverseError, match="nonempty commands") as caught:
        lib.install(intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"


def test_one_structured_plan_not_multiple_or_text(harness: Harness) -> None:
    harness.agent.results = [AgentResult(output={"plans": [plan().model_dump()]}, session_id="session-1")]
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"
    assert not harness.runner.calls


def test_validation_correction_can_succeed(harness: Harness) -> None:
    harness.agent.plans(plan([step(source="https://invalid.example")]), plan())
    report = lib.install(intelligence=harness.agent)
    assert report.outcome == "planned"
    assert "Your plan was rejected" in harness.agent.requests[1].prompt


@pytest.mark.parametrize("accept_license", [False, True])
def test_license_flag_is_enforced_on_commands_and_prompt(harness: Harness, accept_license: bool) -> None:
    harness.host.platform = "macos"
    harness.checks = [host_report(platform="macos")]
    harness.agent.plans(
        plan(
            [
                step(
                    "sudo -n /Volumes/Docker/Docker.app/Contents/MacOS/install --user=$USER --accept-license",
                    source=docs.MACOS,
                ),
                step("open -a Docker", source=docs.MACOS),
            ],
            "docker-desktop-macos",
        )
    )
    if accept_license:
        report = lib.install(accept_license=True, intelligence=harness.agent)
        assert "--accept-license" in report.steps[0].commands[0]
        assert "accept_license is true" in harness.agent.requests[0].prompt
    else:
        with pytest.raises(DigitalTwinUniverseError, match="explicit accept_license=true"):
            lib.install(intelligence=harness.agent)
        assert "accept_license is false" in harness.agent.requests[0].prompt
    assert not harness.runner.calls


def test_method_must_fit_the_platform(harness: Harness) -> None:
    harness.agent.plans(plan(method="docker-desktop-macos"))
    with pytest.raises(DigitalTwinUniverseError, match="does not fit a linux host"):
        lib.install(intelligence=harness.agent)


def test_saved_plan_reuse_costs_no_second_model_call(harness: Harness) -> None:
    planned = lib.install(intelligence=harness.agent)
    harness.checks = [host_report(), host_report(True)]
    report = lib.install(apply=True, intelligence=harness.agent)
    assert report.outcome == "installed"
    assert len(harness.agent.requests) == 1
    assert harness.fetches == 2
    assert [value.commands for value in report.steps[:-1]] == [value.commands for value in planned.steps]


def test_cached_plan_failure_resumes_original_session(harness: Harness) -> None:
    lib.install(intelligence=harness.agent)
    harness.agent.plans(plan([step("repair")]))
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="cached command failed")]
    harness.checks = [host_report(), host_report(True)]
    report = lib.install(apply=True, intelligence=harness.agent)
    assert report.outcome == "installed"
    assert harness.agent.requests[-1].resume == "session-1"
    assert "cached command failed" in harness.agent.requests[-1].prompt
    assert fixture_pages(harness.host)[UBUNTU] in harness.agent.requests[-1].prompt


def test_saved_plan_records_the_agent_provider_and_its_default_model(harness: Harness) -> None:
    lib.install(agent_provider="amplifier-agent")

    assert harness.agent.requests[0].model == DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"]
    with install_module.PLAN_PATH.open(encoding="utf-8") as file:
        assert json.load(file)["agent_provider"] == "amplifier-agent"


def test_saved_plan_from_another_agent_provider_is_reused_without_its_session(harness: Harness) -> None:
    planned = lib.install(agent_provider="copilot")
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="cached command failed")]

    report = lib.install(apply=True, agent_provider="amplifier-agent")

    assert [value.commands for value in report.steps] == [value.commands for value in planned.steps]
    assert report.outcome == "failed"
    assert len(harness.agent.requests) == 1
    assert "another agent provider" in report.notes[-1]


def test_saved_plan_from_the_same_agent_provider_resumes_its_session(harness: Harness) -> None:
    lib.install(agent_provider="amplifier-agent")
    harness.agent.plans(plan([step("repair")]))
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="cached command failed")]
    harness.checks = [host_report(), host_report(True)]

    report = lib.install(apply=True, agent_provider="amplifier-agent")

    assert report.outcome == "installed"
    assert harness.agent.requests[-1].resume == "session-1"


@pytest.mark.parametrize("change", ["facts", "license"])
def test_changed_facts_or_consent_replaces_saved_plan(harness: Harness, change: str) -> None:
    lib.install(intelligence=harness.agent)
    if change == "facts":
        harness.host.machine = "arm64"
    harness.agent.plans(plan([step("replacement")]))
    harness.checks = [host_report(), host_report(True)]
    report = lib.install(apply=True, accept_license=change == "license", intelligence=harness.agent)
    assert report.steps[0].commands == ["replacement"]
    assert len(harness.agent.requests) == harness.fetches == 2
    with install_module.PLAN_PATH.open(encoding="utf-8") as file:
        assert json.load(file)["plan"]["steps"][0]["commands"] == ["replacement"]


@pytest.mark.parametrize(
    ("platform", "release", "expected"),
    [
        ("windows", {}, [docs.WINDOWS, docs.WSL, docs.FAQ, docs.MICROSOFT_WSL]),
        ("macos", {}, [docs.MACOS, docs.FAQ]),
        ("linux", {"ID": "ubuntu"}, ["ubuntu", "debian"]),
        ("linux", {"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}, ["ubuntu", "debian"]),
        ("linux", {"ID": "fedora"}, ["fedora", "rhel", "centos"]),
        ("linux", {"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, ["fedora", "rhel", "centos"]),
        ("linux", {"ID": "unknown"}, list(docs.DISTROS)),
    ],
)
def test_page_selection(harness: Harness, platform: Platform, release: dict[str, str], expected: list[str]) -> None:
    harness.host.platform = platform
    harness.host.os_release = release
    urls = docs.page_urls(harness.host)
    assert urls == (
        [docs.ENGINE, docs.POSTINSTALL, docs.COMPOSE, *(f"{docs.ENGINE}{name}/" for name in expected)]
        if platform == "linux"
        else expected
    )


def test_wsl_linux_gets_engine_pages_plus_conflict_warning(harness: Harness) -> None:
    harness.host.wsl = True
    assert docs.page_urls(harness.host)[-1] == docs.WSL
    lib.install(intelligence=harness.agent)
    assert "including inside WSL 2" in harness.agent.requests[0].prompt
    assert "Engine inside WSL conflicts" in harness.agent.requests[0].prompt


@pytest.mark.parametrize(
    ("outcome", "exit_code"),
    [
        ("ready", 0),
        ("planned", 1),
        ("installed", 0),
        ("action-required", 1),
        ("failed", 1),
    ],
)
def test_cli_json_and_exit_by_outcome(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, outcome: InstallOutcome, exit_code: int
) -> None:
    report = lib.install(intelligence=harness.agent)
    report.outcome = outcome
    calls: list[tuple[object, ...]] = []

    def install(*args: object) -> object:
        calls.append(args)
        return report

    monkeypatch.setattr(lib, "install", install)
    result = CliRunner().invoke(
        app,
        [
            "install",
            "--yes",
            "--accept-license",
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
    assert result.exit_code == exit_code
    assert json.loads(result.stdout)["outcome"] == outcome
    assert calls == [(True, True, "amplifier-agent", DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"], "medium", 50)]


def test_cli_progress_does_not_pollute_json(harness: Harness) -> None:
    harness.checks = [host_report(), host_report(True)]
    result = CliRunner().invoke(app, ["install", "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["outcome"] == "installed"
    assert "[1/1]" in result.stderr
    assert "[2/2]" in result.stderr


@pytest.mark.parametrize("code", ["gh-missing", "gh-not-signed-in"])
def test_preflight_names_configuration_before_docs_or_execution(harness: Harness, code: str) -> None:
    harness.agent.preflight_error = DigitalTwinUniverseError(
        code, "No GitHub authentication.", "Install gh and run `gh auth login` with Copilot access."
    )
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(intelligence=harness.agent)
    assert caught.value.code == code
    assert "gh auth login" in caught.value.remedy
    assert not harness.fetches
    assert not harness.runner.calls
    assert not harness.agent.requests


class PageResponse(BytesIO):
    def __init__(self, text: str, url: str, content_type: str = "text/markdown") -> None:
        super().__init__(text.encode())
        self.url = url
        self.headers = {"Content-Type": content_type}


def test_fetches_actual_markdown_routes_with_deadline(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.host.platform = "windows"
    pages = fixture_pages(harness.host)
    calls: list[str] = []

    def fetch(request: Request, timeout: float, context: ssl.SSLContext) -> PageResponse:
        calls.append(request.full_url)
        source = next(url for url in pages if docs.markdown_url(url) == request.full_url)
        assert 0 < timeout <= 30
        assert request.get_header("Accept") == "text/markdown"
        return PageResponse(pages[source], request.full_url)

    monkeypatch.setattr(docs, "urlopen", fetch)
    assert FETCH_DOCS(harness.host, time.monotonic() + 60) == pages
    assert calls == [docs.markdown_url(source) for source in pages]
    assert calls[0].endswith("windows-install.md")
    assert calls[-1].endswith("?accept=text/markdown")


def test_docs_unreachable_names_all_manual_urls(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: Request, timeout: float, context: ssl.SSLContext) -> PageResponse:
        raise URLError("offline")

    monkeypatch.setattr(docs, "urlopen", fail)
    monkeypatch.setattr(docs, "fetch_docs", FETCH_DOCS)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(intelligence=harness.agent)
    assert caught.value.code == "docs-unreachable"
    assert all(url in caught.value.remedy for url in docs.page_urls(harness.host))
    assert not harness.agent.requests
    assert not harness.runner.calls


@pytest.mark.parametrize(
    ("text", "content_type", "url"),
    [
        ("", "text/markdown", UBUNTU),
        ("<html>error</html>", "text/html", UBUNTU),
        ("# docs", "text/markdown", "https://untrusted.invalid/redirect"),
    ],
)
def test_bad_docs_never_become_a_plan(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, text: str, content_type: str, url: str
) -> None:
    monkeypatch.setattr(docs, "urlopen", lambda *args, **kwargs: PageResponse(text, url, content_type))
    with pytest.raises(DigitalTwinUniverseError) as caught:
        FETCH_DOCS(harness.host, time.monotonic() + 60)
    assert caught.value.code == "docs-unreachable"


def test_deadline_includes_agent_time(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    harness.agent.on_run = lambda: clock.__setitem__(0, 102.0)
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(timeout_seconds=1, intelligence=harness.agent)
    assert caught.value.code == "install-timeout"
    assert "Report so far" in caught.value.message
    assert not harness.runner.calls


def test_command_timeout_reports_partial_completion(harness: Harness) -> None:
    harness.agent.plans(plan([step("completed"), step("timeout"), step("never")]))
    harness.runner.results = [ExecResult(exit_code=0, stdout="", stderr=""), subprocess.TimeoutExpired("timeout", 10)]
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(apply=True, intelligence=harness.agent)
    assert caught.value.code == "install-timeout"
    partial = json.loads(caught.value.message.split("Report so far:\n", 1)[1])
    assert [value["status"] for value in partial["steps"]] == ["done", "failed", "skipped"]
    assert len(harness.agent.requests) == 1


def test_polling_waits_until_check_passes(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    harness.checks = [host_report(), host_report(), host_report(), host_report(True)]
    sleeps: list[float] = []
    monkeypatch.setattr(run.time, "sleep", sleeps.append)
    assert lib.install(apply=True, intelligence=harness.agent).outcome == "installed"
    assert len(sleeps) == 2


def test_polling_stops_at_whole_run_deadline(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(apply=True, timeout_seconds=3, intelligence=harness.agent)
    assert caught.value.code == "install-timeout"
    assert clock[0] == 103.0
    assert '"status": "done"' in caught.value.message


def test_real_runner_closes_stdin_and_captures_output(tmp_path: Path) -> None:
    result = REAL_RUNNER().run(
        [
            sys.executable,
            "-c",
            "import os, sys; print(repr(sys.stdin.read())); print(os.environ['INSTALL_TEST']); print('diagnostic', file=sys.stderr)",
        ],
        5,
        env={**os.environ, "INSTALL_TEST": "noninteractive"},
        cwd=tmp_path,
    )
    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["''", "noninteractive"]
    assert result.stderr.strip() == "diagnostic"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group termination")
def test_real_runner_times_out_shell_children(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        REAL_RUNNER().run(["sh", "-c", "sleep 20"], 0.1, cwd=tmp_path)
    assert time.monotonic() - started < 5


@pytest.mark.skipif(os.name == "nt", reason="POSIX host facts")
def test_host_facts_gathers_linux_without_model(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda path: path == Path("/etc/os-release"))
    monkeypatch.setattr(facts.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(facts.platform, "release", lambda: "6.6-microsoft-standard-WSL2")
    monkeypatch.setattr(facts.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        facts.platform,
        "freedesktop_os_release",
        lambda: {"ID": "linuxmint", "ID_LIKE": "ubuntu", "UBUNTU_CODENAME": "noble", "IGNORED": "value"},
    )
    gathered = HOST_FACTS(host_report(), False, harness.runner, time.monotonic() + 60)
    assert gathered.wsl
    assert gathered.machine == "arm64"
    assert gathered.os_release == {"ID": "linuxmint", "ID_LIKE": "ubuntu", "UBUNTU_CODENAME": "noble"}
    assert gathered.sudo_noninteractive
    assert not gathered.euid_root
    assert harness.runner.calls[0].argv == ["sudo", "-n", "true"]


@pytest.mark.parametrize("wsl_present", [False, True])
def test_windows_facts_include_wsl_elevation_and_build(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, wsl_present: bool
) -> None:
    harness.runner.results = [
        ExecResult(exit_code=0 if wsl_present else 1, stdout="WSL version: 2.6.1", stderr=""),
        ExecResult(exit_code=0, stdout="True\n", stderr=""),
    ]
    monkeypatch.setattr(facts.platform, "version", lambda: "10.0.22631")
    gathered = HOST_FACTS(host_report(platform="windows"), False, harness.runner, time.monotonic() + 60)
    assert gathered.wsl_version == ("WSL version: 2.6.1" if wsl_present else None)
    assert gathered.elevated
    assert gathered.windows_build == "10.0.22631"
    assert harness.runner.calls[0].argv == ["wsl", "--version"]
    assert "-NonInteractive" in harness.runner.calls[1].argv


def test_wsl_utf16_output_is_decoded() -> None:
    assert facts._decode("WSL version: 2.6.1".encode("utf-16-le")) == "WSL version: 2.6.1"


@pytest.mark.parametrize(
    ("platform", "method", "source", "startup"),
    [
        ("macos", "docker-desktop-macos", docs.MACOS, "open -a Docker"),
        (
            "windows",
            "docker-desktop-windows-per-user",
            docs.WINDOWS,
            'Start-Process "$env:LOCALAPPDATA\\Programs\\DockerDesktop\\Docker Desktop.exe"',
        ),
        ("linux", "docker-engine-dnf", f"{docs.ENGINE}fedora/", "sudo -n systemctl enable --now docker"),
    ],
)
def test_steps_run_in_the_platform_shell(
    harness: Harness, platform: Platform, method: InstallMethod, source: str, startup: str
) -> None:
    harness.host.platform = platform
    harness.host.os_release = {"ID": "fedora"}
    harness.checks = [host_report(platform=platform), host_report(True, platform)]
    harness.agent.plans(plan([step(startup, source=source)], method))

    report = lib.install(apply=True, accept_license=True, intelligence=harness.agent)

    assert report.outcome == "installed"
    assert startup in harness.runner.calls[0].argv[-1]
    assert report.steps[0].commands == [startup]
    assert harness.verifications == 1
    if platform == "windows":
        assert harness.runner.calls[0].argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
        assert "$LASTEXITCODE" in harness.runner.calls[0].argv[-1]


def test_desktop_without_license_leaves_terms_manual(harness: Harness) -> None:
    harness.host.platform = "macos"
    harness.checks = [host_report(platform="macos")]
    proposed = plan(
        [
            step("sudo -n /Volumes/Docker/Docker.app/Contents/MacOS/install --user=$USER", source=docs.MACOS),
            step("open -a Docker", source=docs.MACOS),
            step("# open Docker Desktop and accept the terms", False, docs.MACOS),
        ],
        "docker-desktop-macos",
    )
    proposed.next = "open Docker Desktop and accept the terms"
    harness.agent.plans(proposed)

    report = lib.install(apply=True, intelligence=harness.agent)

    assert report.outcome == "action-required"
    assert report.next == proposed.next
    assert [value.status for value in report.steps] == ["done", "done", "manual"]
    assert all("--accept-license" not in command for value in report.steps for command in value.commands)


@pytest.mark.parametrize(
    "command",
    [
        "sudo apt-get install -y docker-ce",
        "newgrp docker",
        "curl https://get.docker.com | sh",
        "# do nothing",
    ],
)
def test_unsafe_unattended_commands_are_rejected(harness: Harness, command: str) -> None:
    harness.agent.plans(plan([step(command)]))
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(apply=True, intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"
    assert not harness.runner.calls


def test_unknown_distribution_can_return_all_manual_plan(harness: Harness) -> None:
    harness.host.os_release = {"ID": "unknown"}
    proposed = plan([step("# Follow https://docs.docker.com/engine/install/binaries/", False, docs.ENGINE)], None)
    harness.agent.plans(proposed)
    report = lib.install(apply=True, intelligence=harness.agent)
    assert report.outcome == "action-required"
    assert report.method is None
    assert not harness.runner.calls


def test_repair_plan_cannot_accept_license_without_consent(harness: Harness) -> None:
    harness.agent.plans(plan([step("fails")]), plan([step("installer --accept-license")]))
    harness.runner.results = [ExecResult(exit_code=1, stdout="", stderr="original failure")]
    with pytest.raises(DigitalTwinUniverseError) as caught:
        lib.install(apply=True, intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"
    assert "original failure" in caught.value.message
    assert len(harness.agent.requests) == 2
    assert len(harness.runner.calls) == 1


def test_tampered_cached_plan_is_revalidated(harness: Harness) -> None:
    lib.install(intelligence=harness.agent)
    with install_module.PLAN_PATH.open(encoding="utf-8") as file:
        saved = json.load(file)
    saved["plan"]["steps"][0]["commands"] = ["installer --accept-license"]
    with install_module.PLAN_PATH.open("w", encoding="utf-8") as file:
        json.dump(saved, file)
    with pytest.raises(DigitalTwinUniverseError, match="Invalid saved plan"):
        lib.install(apply=True, intelligence=harness.agent)
    assert not harness.runner.calls
    assert len(harness.agent.requests) == 1


def test_model_error_never_falls_back_to_a_deterministic_plan(harness: Harness) -> None:
    harness.agent.results = [AgentResult(error="Copilot model unavailable", session_id="session-1")]
    with pytest.raises(DigitalTwinUniverseError, match="Copilot model unavailable") as caught:
        lib.install(apply=True, intelligence=harness.agent)
    assert caught.value.code == "plan-rejected"
    assert not harness.runner.calls


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell")
def test_shell_step_preserves_variables_and_stops_on_command_failure(tmp_path: Path) -> None:
    proposed = step("VALUE=kept")
    proposed.commands = ["VALUE=kept", 'test "$VALUE" = kept', "exit 7", "echo should-not-run"]
    run.run_step(proposed, 1, 1, "linux", REAL_RUNNER(), time.monotonic() + 5, tmp_path)
    assert proposed.status == "failed"
    assert proposed.reason == "Exit 7: no output"


@pytest.mark.parametrize("args", [["--timeout-seconds", "0"], ["--reasoning-effort", "invalid"]])
def test_cli_rejects_bad_arguments(harness: Harness, args: list[str]) -> None:
    result = CliRunner().invoke(app, ["install", *args])
    assert result.exit_code == 2
    assert not harness.agent.requests
