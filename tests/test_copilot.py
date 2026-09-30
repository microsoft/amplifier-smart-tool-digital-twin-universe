"""Drives the Copilot adapter with the SDK's client faked, so neither gh nor a Copilot subscription is needed."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("copilot")

from copilot import Tool, ToolInvocation

from digital_twin_universe.intelligence import copilot as adapter
from digital_twin_universe.intelligence.copilot import CopilotIntelligence
from digital_twin_universe.intelligence.schemas import AgentRequest, HostWorkspace
from digital_twin_universe.intelligence.submission import (
    MAX_INVALID_SUBMISSIONS,
    SUBMIT_TOOL,
    resubmit_prompt,
    submission_problem,
)

SESSION = "fake-session"
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

# One turn of the fake: given the session's tools by name, it may call them and answers the final message.
Script = Callable[[dict[str, Tool]], str]


class FakeSession:
    def __init__(self, world: "FakeWorld", options: dict[str, Any]) -> None:
        self.session_id = SESSION
        self._world = world
        self._tools = {tool.name: tool for tool in options.get("tools", [])}

    async def send_and_wait(self, prompt: str, timeout: float) -> Any:
        self._world.prompts.append(prompt)
        script = self._world.scripts.pop(0)
        return SimpleNamespace(data=SimpleNamespace(content=script(self._tools)))

    async def abort(self) -> None:
        pass


class FakeWorld:
    """Stands in for CopilotClient and records what the adapter asked of it."""

    def __init__(self, *scripts: Script) -> None:
        self.scripts = list(scripts)
        self.options: list[dict[str, Any]] = []
        self.prompts: list[str] = []

    def client(self, working_directory: str | None, github_token: str) -> Any:
        world = self

        class Client:
            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            async def create_session(self, **options: Any) -> FakeSession:
                world.options.append(options)
                return FakeSession(world, options)

        return Client()


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeWorld]:
    def install(*scripts: Script) -> FakeWorld:
        fake = FakeWorld(*scripts)
        monkeypatch.setattr(adapter, "CopilotClient", fake.client)
        monkeypatch.setattr(CopilotIntelligence, "_github_token", lambda self: "token")
        return fake

    return install


def submits(arguments: dict[str, Any] | None) -> Script:
    def script(tools: dict[str, Tool]) -> str:
        handler = tools[SUBMIT_TOOL].handler
        if arguments is not None and handler is not None:
            handler(ToolInvocation(arguments=arguments))
        return "Done."

    return script


def request(workspace: Path | None = None, **fields: Any) -> AgentRequest:
    return AgentRequest(
        prompt="Do the work.",
        model="gpt-6.1-sol",
        workspace=HostWorkspace(path=workspace) if workspace is not None else None,
        reasoning_effort=fields.pop("reasoning_effort", "high"),
        timeout_seconds=60,
        **fields,
    )


def test_a_workspace_run_gets_the_workspace_tools_and_the_reasoning_effort(
    world: Callable[..., FakeWorld], tmp_path: Path
) -> None:
    fake = world(lambda tools: "read", lambda tools: "wrote")
    intelligence = CopilotIntelligence()

    intelligence.run(request(tmp_path, reasoning_effort="max"))
    result = intelligence.run(request(tmp_path, writable=True))

    assert result.text == "wrote"
    assert result.session_id == SESSION
    assert [options["available_tools"] for options in fake.options] == [
        ["view", "grep", "bash"],
        ["view", "grep", "bash", "edit", "write"],
    ]
    assert fake.options[0]["reasoning_effort"] == "max"
    assert fake.options[0]["working_directory"] == str(tmp_path)


def test_a_turn_without_a_valid_submission_is_prompted_again_until_the_cap(world: Callable[..., FakeWorld]) -> None:
    fake = world(submits({"answer": 42}), *[submits(None)] * MAX_INVALID_SUBMISSIONS)

    result = CopilotIntelligence().run(request(output_schema=SCHEMA))

    never_called = f"the {SUBMIT_TOOL} tool was never called"
    assert result.output is None
    assert result.error == f"No valid submission after {MAX_INVALID_SUBMISSIONS} retries: {never_called}"
    assert fake.options[0]["available_tools"] == [SUBMIT_TOOL]
    invalid = submission_problem({"answer": 42}, SCHEMA)
    assert invalid is not None
    assert fake.prompts[1:] == [resubmit_prompt(invalid)] + [resubmit_prompt(never_called)] * (
        MAX_INVALID_SUBMISSIONS - 1
    )
