"""Drives the Amplifier Agent adapter with the SDK's agent faked, so no provider or credentials are needed."""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("amplifier_agent")

from amplifier_agent import (
    AgentError,
    AgentOptions,
    Event,
    SessionRecord,
    TextPart,
    Tool,
    ToolContext,
    ToolFailed,
    TurnInput,
    TurnResult,
)

from digital_twin_universe.intelligence import amplifier_agent as adapter
from digital_twin_universe.intelligence.amplifier_agent import AmplifierAgentIntelligence, parse_model
from digital_twin_universe.intelligence.schemas import AgentRequest, HostWorkspace
from digital_twin_universe.intelligence.submission import MAX_INVALID_SUBMISSIONS, SUBMIT_TOOL, resubmit_prompt
from digital_twin_universe.schemas import DigitalTwinUniverseError

MODEL = "openai/gpt-6-sol"
SESSION = "fake-session"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
ERROR = AgentError("selector_rejected", "selection", "The model is not available.", "Select another model.")

# One turn of the fake: given the agent's tools by name and the prompt, it may call them and answers a result,
# or None to hang until cancelled.
Script = Callable[[dict[str, Tool], str], Any]


def success(*deltas: str) -> TurnResult:
    return TurnResult(state="success", content=[TextPart(text=delta) for delta in deltas])


async def submit(tools: dict[str, Tool], arguments: dict[str, Any]) -> str:
    return await tools[SUBMIT_TOOL].handler(arguments, ToolContext(call_id="call"))


class FakeTurn:
    def __init__(self, result: TurnResult | None) -> None:
        self.result = result
        self.cancelled = asyncio.Event()

    async def events(self) -> AsyncIterator[Event]:
        if self.result is None:
            await self.cancelled.wait()
            self.result = TurnResult(state="cancelled")
        yield Event("turn-events/1", SESSION, "turn", 1, "terminal", self.result)

    async def cancel(self) -> None:
        self.cancelled.set()


class FakeSession:
    def __init__(self, world: "FakeWorld", session_id: str) -> None:
        self.info = SessionRecord(session_id=session_id, persistence="durable")
        self._world = world

    async def start_turn(self, input: TurnInput) -> FakeTurn:
        prompt = "".join(part.text for part in input.content)
        self._world.prompts.append(prompt)
        script = self._world.scripts.pop(0)
        turn = FakeTurn(await script(self._world.tools, prompt))
        self._world.turns.append(turn)
        return turn

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class FakeAgent:
    def __init__(self, world: "FakeWorld") -> None:
        self._world = world

    async def create_session(self) -> FakeSession:
        return FakeSession(self._world, SESSION)

    async def resume_session(self, session_id: str) -> FakeSession:
        self._world.resumed.append(session_id)
        return FakeSession(self._world, session_id)

    async def close(self) -> None:
        self._world.closed += 1

    async def __aenter__(self) -> "FakeAgent":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()


class FakeWorld:
    """Stands in for create_agent and records what the adapter asked of it."""

    def __init__(self, *scripts: Script, error: AgentError | None = None) -> None:
        self.scripts = list(scripts)
        self.error = error
        self.options: list[AgentOptions] = []
        self.directories: list[Path] = []
        self.locked: list[bool] = []
        self.prompts: list[str] = []
        self.turns: list[FakeTurn] = []
        self.resumed: list[str] = []
        self.closed = 0
        self.tools: dict[str, Tool] = {}

    async def create_agent(self, options: AgentOptions) -> FakeAgent:
        self.options.append(options)
        self.directories.append(Path.cwd())
        self.locked.append(adapter.WORKING_DIRECTORY_LOCK.locked())
        self.tools = {tool.name: tool for tool in options.tools or [] if isinstance(tool, Tool)}
        if self.error is not None:
            raise self.error
        return FakeAgent(self)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeWorld]:
    def install(*scripts: Script, error: AgentError | None = None) -> FakeWorld:
        fake = FakeWorld(*scripts, error=error)
        monkeypatch.setattr(adapter, "create_agent", fake.create_agent)
        return fake

    return install


def request(workspace: Path | None = None, **fields: Any) -> AgentRequest:
    return AgentRequest(
        prompt="Do the work.",
        model=fields.pop("model", MODEL),
        workspace=HostWorkspace(path=workspace) if workspace is not None else None,
        reasoning_effort=fields.pop("reasoning_effort", "high"),
        timeout_seconds=fields.pop("timeout_seconds", 60),
        **fields,
    )


def answers(result: TurnResult | None) -> Script:
    async def script(tools: dict[str, Tool], prompt: str) -> TurnResult | None:
        return result

    return script


def submits(*submissions: dict[str, Any]) -> Script:
    """Submits each in turn, as a model retrying after a rejected submission would, then ends the turn."""

    async def script(tools: dict[str, Tool], prompt: str) -> TurnResult:
        for arguments in submissions:
            try:
                await submit(tools, arguments)
            except ToolFailed:
                continue
        return success("Submitted.")

    return script


@pytest.mark.parametrize(
    ("model", "selector"),
    [
        ("openai/gpt-6-sol", ("openai", "gpt-6-sol")),
        ("azure-openai/deployments/gpt-6", ("azure-openai", "deployments/gpt-6")),
    ],
)
def test_the_model_names_the_provider_before_the_first_slash(model: str, selector: tuple[str, str]) -> None:
    assert parse_model(model) == selector


@pytest.mark.parametrize("model", ["gpt-6-sol", "/gpt-6-sol", "openai/"])
def test_a_model_without_a_provider_names_the_expected_form(model: str) -> None:
    with pytest.raises(DigitalTwinUniverseError) as failure:
        parse_model(model)

    assert failure.value.code == "model-invalid"
    assert "<provider>/<model>" in failure.value.message
    assert model in failure.value.message


def test_a_plain_completion_has_no_tools_and_never_sees_the_callers_directory(
    world: Callable[..., FakeWorld],
) -> None:
    fake = world(answers(success("Hello", ", ", "world.")))
    caller = Path.cwd()

    result = AmplifierAgentIntelligence().run(request())

    assert result.error is None
    assert result.text == "Hello, world."
    assert result.session_id == SESSION
    options = fake.options[0]
    assert options.tools == []
    assert (options.provider, options.model) == ("openai", "gpt-6-sol")
    assert fake.directories[0] != caller
    assert not fake.directories[0].exists()
    assert Path.cwd() == caller


def test_a_workspace_run_is_read_only_unless_writable_and_restores_the_directory(
    world: Callable[..., FakeWorld], tmp_path: Path
) -> None:
    fake = world(answers(success("read")), answers(success("wrote")))
    caller = Path.cwd()
    intelligence = AmplifierAgentIntelligence()

    intelligence.run(request(tmp_path))
    intelligence.run(request(tmp_path, writable=True))

    assert [options.tools for options in fake.options] == [
        ["read_file", "glob", "grep", "bash"],
        ["read_file", "glob", "grep", "bash", "write_file", "edit_file"],
    ]
    assert fake.directories == [tmp_path, tmp_path]
    assert fake.locked == [True, True]
    assert Path.cwd() == caller
    assert not adapter.WORKING_DIRECTORY_LOCK.locked()
    for options in fake.options:
        assert options.approvals == "allow"
        assert options.tool_error_policy == "continue"
        assert Path(str(options.storage)).is_absolute()
        assert not {"web_fetch", "web_search", "delegate"} & {str(tool) for tool in options.tools or []}


def test_an_invalid_submission_fails_the_tool_call_so_the_model_retries_in_the_same_turn(
    world: Callable[..., FakeWorld],
) -> None:
    failures: list[str] = []

    async def script(tools: dict[str, Tool], prompt: str) -> TurnResult:
        try:
            await submit(tools, {"answer": 42})
        except ToolFailed as failure:
            failures.append(str(failure))
        assert await submit(tools, {"answer": "42"}) == "Submission received."
        return success("Submitted.")

    fake = world(script)

    result = AmplifierAgentIntelligence().run(request(output_schema=SCHEMA))

    assert result.error is None
    assert result.output == {"answer": "42"}
    assert len(fake.prompts) == 1
    assert len(failures) == 1
    assert "does not match the schema" in failures[0]
    tool = fake.tools[SUBMIT_TOOL]
    assert tool.input_schema == {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}
    assert '"additionalProperties": false' in tool.description


def test_a_turn_without_a_valid_submission_is_prompted_again_until_the_cap(world: Callable[..., FakeWorld]) -> None:
    fake = world(answers(success("I forgot.")), *[submits({"answer": 42})] * MAX_INVALID_SUBMISSIONS)

    result = AmplifierAgentIntelligence().run(request(output_schema=SCHEMA))

    assert result.output is None
    assert result.error is not None
    assert f"No valid submission after {MAX_INVALID_SUBMISSIONS} retries" in result.error
    assert result.session_id == SESSION
    # A rejected submission fails its tool call and is never kept, so the turn ends without one.
    assert fake.prompts[1:] == [resubmit_prompt(f"the {SUBMIT_TOOL} tool was never called")] * MAX_INVALID_SUBMISSIONS


def test_a_turn_past_the_deadline_is_cancelled_and_drained(world: Callable[..., FakeWorld]) -> None:
    fake = world(answers(None))

    result = AmplifierAgentIntelligence().run(request(timeout_seconds=1))

    assert result.error == "The agent did not finish within 1 seconds."
    assert result.session_id == SESSION
    assert fake.turns[0].cancelled.is_set()
    assert fake.turns[0].result is not None
    assert fake.turns[0].result.state == "cancelled"
    assert fake.closed == 1


@pytest.mark.parametrize(
    ("scripts", "error", "session_id"),
    [
        ([answers(TurnResult(state="failure", error=ERROR))], None, SESSION),
        ([], ERROR, None),
    ],
    ids=["failed-turn", "cannot-start"],
)
def test_an_agent_failure_carries_its_message_and_remedy(
    world: Callable[..., FakeWorld], scripts: list[Script], error: AgentError | None, session_id: str | None
) -> None:
    world(*scripts, error=error)

    result = AmplifierAgentIntelligence().run(request())

    assert result.error == "selector_rejected: The model is not available. Select another model."
    assert result.session_id == session_id


def test_a_model_without_a_provider_fails_the_run_and_the_preflight_before_any_agent_starts(
    world: Callable[..., FakeWorld],
) -> None:
    fake = world()

    result = AmplifierAgentIntelligence().run(request(model="gpt-6-sol"))
    with pytest.raises(DigitalTwinUniverseError):
        AmplifierAgentIntelligence("gpt-6-sol").preflight()

    assert result.error is not None
    assert "<provider>/<model>" in result.error
    assert fake.options == []


def test_a_resumed_run_continues_the_named_session(world: Callable[..., FakeWorld]) -> None:
    fake = world(answers(success("again")))

    result = AmplifierAgentIntelligence().run(request(resume="earlier-session"))

    assert fake.resumed == ["earlier-session"]
    assert result.session_id == "earlier-session"


def test_preflight_starts_and_closes_an_agent_without_tools_and_names_the_remedy_when_it_fails(
    world: Callable[..., FakeWorld],
) -> None:
    fake = world()

    AmplifierAgentIntelligence("anthropic/claude-opus-5").preflight()

    options = fake.options[0]
    assert (options.provider, options.model, options.tools) == ("anthropic", "claude-opus-5", [])
    assert fake.closed == 1

    world(error=AgentError("engine_unavailable", "lifecycle", "The provider is not installed.", "Install it."))

    with pytest.raises(DigitalTwinUniverseError) as failure:
        AmplifierAgentIntelligence("github-copilot/gpt-6.1-sol").preflight()

    assert failure.value.code == "amplifier-agent-unavailable"
    assert "github-copilot/gpt-6.1-sol" in failure.value.message
    assert "The provider is not installed. Install it." in failure.value.message
    assert adapter.PROVIDERS_DOCUMENTATION in failure.value.remedy


def test_sessions_live_in_the_platform_state_directory_under_the_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(adapter.sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert adapter.storage_directory() == tmp_path / "digital-twin-universe" / "amplifier-agent"
