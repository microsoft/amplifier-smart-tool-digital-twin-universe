"""Drives the Claude adapter with the SDK's client faked, so neither credentials nor a model are needed."""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import ClaudeAgentOptions, CLIConnectionError, ResultMessage

from digital_twin_universe.intelligence import claude as adapter
from digital_twin_universe.intelligence import interface
from digital_twin_universe.intelligence.claude import (
    READ_ONLY_TOOLS,
    WRITE_TOOLS,
    ClaudeIntelligence,
    parse_answer,
    scratch_directory,
)
from digital_twin_universe.intelligence.schemas import AgentRequest, HostWorkspace
from digital_twin_universe.intelligence.submission import MAX_INVALID_SUBMISSIONS, reanswer_prompt
from digital_twin_universe.schemas import DigitalTwinUniverseError

SESSION = "fake-session"
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def finished(
    text: str | None = None,
    structured_output: Any = None,
    subtype: str = "success",
    is_error: bool = False,
    errors: list[str] | None = None,
    api_error_status: int | None = None,
    session_id: str = SESSION,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=text,
        structured_output=structured_output,
        errors=errors,
        api_error_status=api_error_status,
    )


class FakeWorld:
    """Stands in for ClaudeSDKClient and records what the adapter asked of it."""

    def __init__(self, *results: ResultMessage | Exception | None, account: dict[str, Any] | None = None) -> None:
        self.results = list(results)
        self.account = account
        self.options: list[ClaudeAgentOptions] = []
        self.prompts: list[str] = []
        self.interrupted = asyncio.Event()
        self.directories_existed: list[bool] = []
        self.disconnected = 0

    def client(self, options: ClaudeAgentOptions) -> Any:
        world = self
        world.options.append(options)
        world.directories_existed.append(Path(str(options.cwd)).is_dir())

        class Client:
            def __init__(self) -> None:
                self.pending: ResultMessage | None = None

            async def __aenter__(self) -> "Client":
                return self

            async def __aexit__(self, *exc: object) -> None:
                pass

            async def connect(self) -> None:
                pass

            async def disconnect(self) -> None:
                world.disconnected += 1

            async def get_server_info(self) -> dict[str, Any]:
                return {"account": world.account}

            async def query(self, prompt: str) -> None:
                world.prompts.append(prompt)
                result = world.results.pop(0)
                if isinstance(result, Exception):
                    raise result
                self.pending = result

            async def receive_response(self) -> AsyncIterator[Any]:
                if self.pending is None:
                    await world.interrupted.wait()
                    self.pending = finished(subtype="error_during_execution", is_error=True)
                yield self.pending

            async def interrupt(self) -> None:
                world.interrupted.set()

        return Client()


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., FakeWorld]:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(interface.sys, "platform", "linux")

    def install(*results: ResultMessage | Exception | None, account: dict[str, Any] | None = None) -> FakeWorld:
        fake = FakeWorld(*results, account=account)
        monkeypatch.setattr(adapter, "ClaudeSDKClient", fake.client)
        return fake

    return install


def request(workspace: Path | None = None, **fields: Any) -> AgentRequest:
    return AgentRequest(
        prompt="Do the work.",
        model="claude-opus-5-5",
        workspace=HostWorkspace(path=workspace) if workspace is not None else None,
        reasoning_effort=fields.pop("reasoning_effort", "high"),
        timeout_seconds=fields.pop("timeout_seconds", 60),
        **fields,
    )


@pytest.mark.parametrize(
    ("writable", "tools"), [(False, READ_ONLY_TOOLS), (True, READ_ONLY_TOOLS + WRITE_TOOLS)], ids=["read", "write"]
)
def test_a_workspace_run_gets_the_tools_its_access_allows_without_permission_prompts(
    world: Callable[..., FakeWorld], tmp_path: Path, writable: bool, tools: list[str]
) -> None:
    fake = world(finished("done"))

    result = ClaudeIntelligence().run(request(tmp_path, writable=writable, reasoning_effort="max"))

    assert result.error is None
    assert result.text == "done"
    assert result.session_id == SESSION
    options = fake.options[0]
    assert options.cwd == tmp_path.resolve()
    assert options.tools == tools
    assert options.model == "claude-opus-5-5"
    assert options.permission_mode == "bypassPermissions"
    assert options.effort == "max"
    assert options.resume is None
    assert options.output_format is None
    assert options.setting_sources is None


def test_a_plain_completion_has_no_tools_and_runs_in_one_stable_scratch_directory_never_the_callers(
    world: Callable[..., FakeWorld], tmp_path: Path
) -> None:
    fake = world(finished("Hello."), finished("Again."))
    caller = Path.cwd().resolve()

    first = ClaudeIntelligence().run(request())
    second = ClaudeIntelligence().run(request(resume=first.session_id))

    assert first.text == "Hello."
    assert second.session_id == SESSION
    assert [options.tools for options in fake.options] == [[], []]
    assert fake.options[0].cwd == fake.options[1].cwd == scratch_directory()
    assert scratch_directory() == tmp_path / "state" / "digital-twin-universe" / "claude"
    assert scratch_directory() != caller
    assert fake.directories_existed == [True, True]
    assert fake.options[1].resume == SESSION


def test_a_resumed_run_continues_the_named_session(world: Callable[..., FakeWorld], tmp_path: Path) -> None:
    fake = world(finished("again", session_id="earlier-session"))

    result = ClaudeIntelligence().run(request(tmp_path, resume="earlier-session"))

    assert fake.options[0].resume == "earlier-session"
    assert result.session_id == "earlier-session"


def test_a_turn_past_the_deadline_is_interrupted_and_drained(world: Callable[..., FakeWorld]) -> None:
    fake = world(None)

    result = ClaudeIntelligence().run(request(timeout_seconds=1))

    assert result.error == "The agent did not finish within 1 seconds."
    assert result.session_id == SESSION
    assert fake.interrupted.is_set()
    assert fake.disconnected == 1


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (
            finished(subtype="error_max_structured_output_retries", is_error=True, errors=["Too many retries."]),
            "Too many retries.",
        ),
        (
            finished("API Error: 401 API key is invalid.", is_error=True, api_error_status=401),
            "API Error: 401 API key is invalid.",
        ),
        (finished(subtype="error_max_turns", is_error=True), "The run ended error_max_turns."),
    ],
    ids=["errors", "api-error", "subtype-only"],
)
def test_a_run_that_ended_in_error_carries_why_and_the_session(
    world: Callable[..., FakeWorld], result: ResultMessage, error: str
) -> None:
    world(result)

    outcome = ClaudeIntelligence().run(request(output_schema=SCHEMA))

    assert outcome.error == error
    assert outcome.output is None
    assert outcome.session_id == SESSION


def test_a_runtime_failure_is_the_result_not_a_crash(world: Callable[..., FakeWorld]) -> None:
    fake = world(CLIConnectionError("Claude Code exited."))

    result = ClaudeIntelligence().run(request(resume="earlier-session"))

    assert result.error == "CLIConnectionError: Claude Code exited."
    assert result.session_id == "earlier-session"
    assert fake.disconnected == 1
    assert fake.prompts == ["Do the work."]


def test_structured_output_is_asked_for_with_the_original_schema(world: Callable[..., FakeWorld]) -> None:
    fake = world(finished("Answered.", {"answer": "42"}))

    result = ClaudeIntelligence().run(request(output_schema=SCHEMA))

    assert result.error is None
    assert result.output == {"answer": "42"}
    assert result.text == "Answered."
    assert fake.options[0].output_format == {"type": "json_schema", "schema": SCHEMA}
    assert len(fake.prompts) == 1


def test_an_unacceptable_answer_is_asked_for_again_until_the_cap(world: Callable[..., FakeWorld]) -> None:
    fake = world(finished("I could not."), *[finished(structured_output={"answer": 42})] * MAX_INVALID_SUBMISSIONS)

    result = ClaudeIntelligence().run(request(output_schema=SCHEMA))

    _, missing = parse_answer(None, SCHEMA)
    _, mismatched = parse_answer({"answer": 42}, SCHEMA)
    assert missing is not None
    assert mismatched is not None
    assert result.output is None
    assert result.error == f"No valid submission after {MAX_INVALID_SUBMISSIONS} retries: {mismatched}"
    assert result.session_id == SESSION
    assert fake.prompts[1:] == [reanswer_prompt(missing)] + [reanswer_prompt(mismatched)] * (
        MAX_INVALID_SUBMISSIONS - 1
    )


def test_an_answer_accepted_after_a_retry_is_the_output(world: Callable[..., FakeWorld]) -> None:
    world(finished(structured_output=["answer"]), finished(structured_output={"answer": "42"}))

    result = ClaudeIntelligence().run(request(output_schema=SCHEMA))

    assert result.error is None
    assert result.output == {"answer": "42"}


@pytest.mark.parametrize(
    "account",
    [
        {"tokenSource": "none", "apiKeySource": "ANTHROPIC_API_KEY", "apiProvider": "firstParty"},
        {"apiProvider": "bedrock"},
    ],
    ids=["api-key", "cloud-provider"],
)
def test_preflight_passes_with_an_api_key_or_a_cloud_provider(
    world: Callable[..., FakeWorld], account: dict[str, Any]
) -> None:
    world(account=account)

    ClaudeIntelligence().preflight()


def test_preflight_names_every_documented_way_to_authenticate_when_there_are_no_credentials(
    world: Callable[..., FakeWorld],
) -> None:
    world(account={"tokenSource": "none", "apiProvider": "firstParty"})

    with pytest.raises(DigitalTwinUniverseError) as failure:
        ClaudeIntelligence().preflight()

    assert failure.value.code == "claude-not-signed-in"
    assert "ANTHROPIC_API_KEY" in failure.value.remedy
    assert "CLAUDE_CODE_USE_BEDROCK" in failure.value.remedy
    assert adapter.AUTHENTICATION_DOCUMENTATION in failure.value.remedy


def test_preflight_names_the_runtime_when_it_cannot_start(
    world: Callable[..., FakeWorld], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Unstartable:
        def __init__(self, options: ClaudeAgentOptions) -> None:
            pass

        async def __aenter__(self) -> None:
            raise FileNotFoundError("claude")

        async def __aexit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(adapter, "ClaudeSDKClient", Unstartable)

    with pytest.raises(DigitalTwinUniverseError) as failure:
        ClaudeIntelligence().preflight()

    assert failure.value.code == "claude-unavailable"
    assert "FileNotFoundError" in failure.value.message
    assert "[claude]" in failure.value.remedy
