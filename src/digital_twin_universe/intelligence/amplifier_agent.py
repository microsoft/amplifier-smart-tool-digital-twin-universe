"""Amplifier Agent implementation of the intelligence interface."""

import asyncio
import contextlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any

from amplifier_agent import (
    Agent,
    AgentError,
    AgentOptions,
    Session,
    TextPart,
    Tool,
    ToolContext,
    ToolFailed,
    Turn,
    TurnInput,
    TurnResult,
    create_agent,
)
from liquid import render

from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.intelligence.submission import (
    MAX_INVALID_SUBMISSIONS,
    SUBMIT_TOOL,
    resubmit_prompt,
    submission_problem,
)
from digital_twin_universe.schemas import DEFAULT_INTELLIGENCE_MODELS, DigitalTwinUniverseError

READ_ONLY_TOOLS = ["read_file", "glob", "grep", "bash"]
WRITE_TOOLS = ["write_file", "edit_file"]
PROVIDERS_DOCUMENTATION = "https://github.com/microsoft/amplifier-agent/blob/v1/docs/providers.md"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
# The runtime rejects arguments that break a tool's declared schema before the handler runs, and ends the turn.
# Declaring any object and validating in the handler turns a bad submission into a failure the model can retry.
PERMISSIVE_SCHEMA: dict[str, Any] = {"$schema": JSON_SCHEMA_DIALECT, "type": "object"}
SUBMIT_DESCRIPTION = """Submit your final answer. Call it exactly once, when you are done.
The arguments must be one JSON object matching this JSON schema:
{{ schema }}"""

# create_agent captures the process's working directory for every built-in tool, so the chdir that points it at
# a workspace has to be exclusive across the threads running agents side by side.
WORKING_DIRECTORY_LOCK = threading.Lock()


class AmplifierAgentIntelligence:
    """Runs agents in process through Amplifier Agent, against the provider named in the model."""

    def __init__(self, model: str = DEFAULT_INTELLIGENCE_MODELS["amplifier-agent"]) -> None:
        self.implementation = f"amplifier-agent {version('amplifier-agent')}"
        self._model = model

    def preflight(self) -> None:
        provider, model = parse_model(self._model)
        try:
            asyncio.run(_probe(provider, model))
        except AgentError as error:
            raise DigitalTwinUniverseError(
                "amplifier-agent-unavailable",
                f"Amplifier Agent cannot run {self._model}: {describe(error)}",
                f"Set up the provider and its credentials as {PROVIDERS_DOCUMENTATION} describes, or pass another "
                "model as <provider>/<model>.",
            ) from None

    def run(self, request: AgentRequest) -> AgentResult:
        try:
            provider, model = parse_model(request.model)
        except DigitalTwinUniverseError as error:
            return AgentResult(error=str(error))
        if request.workspace is not None:
            return asyncio.run(self._run(request, provider, model, request.workspace.path))
        # A plain completion has no tools, but the agent still captures a directory; never the caller's.
        with tempfile.TemporaryDirectory() as scratch:
            return asyncio.run(self._run(request, provider, model, Path(scratch)))

    async def _run(self, request: AgentRequest, provider: str, model: str, directory: Path) -> AgentResult:
        submitted: dict[str, Any] | None = None
        tools: list[Tool | str] = []
        if request.workspace is not None:
            # bash is not sandboxed to the workspace; the caller's prompt bounds the agent
            # and the caller validates before anything the agent wrote is kept.
            tools = [*READ_ONLY_TOOLS, *(WRITE_TOOLS if request.writable else [])]
        schema = request.output_schema
        if schema is not None:

            async def capture(arguments: dict[str, Any], context: ToolContext) -> str:
                nonlocal submitted
                problem = submission_problem(arguments, schema)
                if problem is not None:
                    raise ToolFailed(problem)
                submitted = arguments
                return "Submission received."

            tools.append(
                Tool(
                    name=SUBMIT_TOOL,
                    description=render(SUBMIT_DESCRIPTION, schema=json.dumps(schema, indent=2)),
                    input_schema=PERMISSIVE_SCHEMA,
                    handler=capture,
                )
            )
        options = AgentOptions(
            provider=provider,
            model=model,
            tools=tools,
            storage=storage_directory(),
            approvals="allow",
            # The default, "stop", ends the turn on any failed tool call, including a rejected submission.
            tool_error_policy="continue",
        )
        deadline = time.monotonic() + request.timeout_seconds
        timed_out = f"The agent did not finish within {request.timeout_seconds} seconds."
        # Carried out of the try so the failure paths can name the session the caller could resume.
        session_id: str | None = None
        try:
            async with (
                await create_agent_in(options, directory) as agent,
                await _session(agent, request.resume) as session,
            ):
                session_id = session.info.session_id
                prompt = request.prompt
                invalid = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return AgentResult(error=timed_out, session_id=session_id)
                    result = await run_turn(session, prompt, remaining)
                    if result is None:
                        return AgentResult(error=timed_out, session_id=session_id)
                    text = "".join(part.text for part in result.content or [])
                    if result.state != "success":
                        error = describe(result.error) if result.error else f"The turn ended {result.state}."
                        return AgentResult(text=text, error=error, session_id=session_id)
                    if schema is None:
                        return AgentResult(text=text, session_id=session_id)
                    problem = submission_problem(submitted, schema)
                    if problem is None:
                        return AgentResult(output=submitted, text=text, session_id=session_id)
                    if invalid >= MAX_INVALID_SUBMISSIONS:
                        return AgentResult(
                            text=text,
                            error=f"No valid submission after {invalid} retries: {problem}",
                            session_id=session_id,
                        )
                    invalid += 1
                    submitted = None
                    prompt = resubmit_prompt(problem)
        except AgentError as error:
            return AgentResult(error=describe(error), session_id=session_id)
        except Exception as error:  # a runtime failure is the caller's data, not a crash
            return AgentResult(error=f"{type(error).__name__}: {error}", session_id=session_id)


def parse_model(model: str) -> tuple[str, str]:
    """Split `<provider>/<model>` on the first slash; the model id itself may hold more."""
    provider, _, name = model.partition("/")
    if not provider or not name:
        raise DigitalTwinUniverseError(
            "model-invalid",
            "The amplifier-agent agent provider takes the model as <provider>/<model>, for instance "
            f"{DEFAULT_INTELLIGENCE_MODELS['amplifier-agent']}; got '{model}'.",
            f"Pass the model in that form; the providers are listed at {PROVIDERS_DOCUMENTATION}",
        )
    return provider, name


def storage_directory() -> Path:
    """The platform's per-user state location, where durable sessions live so a later run can resume one."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "digital-twin-universe" / "amplifier-agent"


def describe(error: AgentError) -> str:
    return f"{error.code}: {error.message} {error.remedy}"


async def create_agent_in(options: AgentOptions, directory: Path) -> Agent:
    """An agent whose built-in tools work in `directory`, leaving the process's working directory as it was."""
    with WORKING_DIRECTORY_LOCK, contextlib.chdir(directory):
        return await create_agent(options)


async def run_turn(session: Session, prompt: str, timeout: float) -> TurnResult | None:
    """The turn's result, or None when it ran out of time and was cancelled."""
    turn = await session.start_turn(TurnInput(content=[TextPart(text=prompt)]))
    terminal = asyncio.create_task(_terminal(turn))
    try:
        return await asyncio.wait_for(asyncio.shield(terminal), timeout)
    except TimeoutError:
        await turn.cancel()
        # Draining to the terminal event leaves the session usable for a later resume.
        await terminal
        return None


async def _terminal(turn: Turn) -> TurnResult:
    async for event in turn.events():
        if event.type == "terminal":
            return event.payload
    raise RuntimeError("The turn's event stream ended without a terminal event.")


async def _session(agent: Agent, resume: str | None) -> Session:
    return await agent.resume_session(resume) if resume is not None else await agent.create_session()


async def _probe(provider: str, model: str) -> None:
    agent = await create_agent(AgentOptions(provider=provider, model=model, tools=[], storage=storage_directory()))
    await agent.close()
