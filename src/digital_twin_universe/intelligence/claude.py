"""Claude Agent SDK implementation of the intelligence interface."""

import asyncio
from importlib.metadata import version
from pathlib import Path
import time
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from digital_twin_universe.intelligence.interface import state_directory
from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.intelligence.submission import (
    MAX_INVALID_SUBMISSIONS,
    reanswer_prompt,
    submission_problem,
)
from digital_twin_universe.schemas import DigitalTwinUniverseError

AUTHENTICATION_DOCUMENTATION = "https://code.claude.com/docs/en/agent-sdk/quickstart"
READ_ONLY_TOOLS = ["Read", "Glob", "Grep", "Bash"]
WRITE_TOOLS = ["Edit", "Write"]


class ClaudeIntelligence:
    """Runs agents through the Claude Code runtime the SDK bundles, with the user's Claude Code settings."""

    def __init__(self) -> None:
        self.implementation = f"claude-agent-sdk {version('claude-agent-sdk')}"

    def preflight(self) -> None:
        try:
            account = asyncio.run(_account())
        except Exception as error:  # whatever stops the runtime from starting, the remedy is the same
            raise DigitalTwinUniverseError(
                "claude-unavailable",
                f"The Claude Code runtime did not start: {type(error).__name__}: {error}",
                "Check that ~/.claude/settings.json is valid, and reinstall digital-twin-universe with the [claude] "
                "extra, which brings the runtime.",
            ) from None
        if not signed_in(account):
            raise DigitalTwinUniverseError(
                "claude-not-signed-in",
                "The Claude Agent SDK has no credentials.",
                "Set ANTHROPIC_API_KEY to a Claude Console API key, or set CLAUDE_CODE_USE_BEDROCK, "
                "CLAUDE_CODE_USE_ANTHROPIC_AWS, CLAUDE_CODE_USE_VERTEX, or CLAUDE_CODE_USE_FOUNDRY to 1 with that "
                f"cloud's credentials, as {AUTHENTICATION_DOCUMENTATION} describes.",
            )

    def run(self, request: AgentRequest) -> AgentResult:
        return asyncio.run(self._run(request))

    async def _run(self, request: AgentRequest) -> AgentResult:
        tools: list[str]
        if request.workspace is not None:
            cwd = request.workspace.path.resolve()
            tools = READ_ONLY_TOOLS + WRITE_TOOLS if request.writable else READ_ONLY_TOOLS
        else:
            # Never the caller's directory. One fixed directory keeps every plain session in a single
            # ~/.claude/projects entry instead of one per run.
            cwd = scratch_directory()
            cwd.mkdir(parents=True, exist_ok=True)
            tools = []
        schema = request.output_schema
        # The shell is not sandboxed to the workspace; the caller's prompt bounds the agent and the caller
        # validates before anything the agent wrote is kept.
        options = ClaudeAgentOptions(
            cwd=cwd,
            model=request.model,
            tools=tools,
            permission_mode="bypassPermissions",
            effort=request.reasoning_effort,
            resume=request.resume,
            output_format={"type": "json_schema", "schema": schema} if schema is not None else None,
        )
        deadline = time.monotonic() + request.timeout_seconds
        timed_out = f"The agent did not finish within {request.timeout_seconds} seconds."
        # Carried out of the try so the failure paths can name the session the caller could resume.
        session_id = request.resume
        client = ClaudeSDKClient(options=options)
        try:
            await client.connect()
            # Not `async with`: the client's __aexit__ is typed as able to suppress errors, so ty would see a path
            # that returns nothing.
            try:
                prompt = request.prompt
                invalid = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return AgentResult(error=timed_out, session_id=session_id)
                    result, finished = await run_turn(client, prompt, remaining)
                    session_id = result.session_id
                    if not finished:
                        return AgentResult(error=timed_out, session_id=session_id)
                    text = result.result or ""
                    if result.is_error:
                        return AgentResult(text=text, error=describe(result), session_id=session_id)
                    if schema is None:
                        return AgentResult(text=text, session_id=session_id)
                    output, problem = parse_answer(result.structured_output, schema)
                    if problem is None:
                        return AgentResult(output=output, text=text, session_id=session_id)
                    if invalid >= MAX_INVALID_SUBMISSIONS:
                        return AgentResult(
                            text=text,
                            error=f"No valid submission after {invalid} retries: {problem}",
                            session_id=session_id,
                        )
                    invalid += 1
                    prompt = reanswer_prompt(problem)
            finally:
                await client.disconnect()
        except Exception as error:  # an SDK or runtime failure is the caller's data, not a crash
            return AgentResult(error=f"{type(error).__name__}: {error}", session_id=session_id)


async def run_turn(client: ClaudeSDKClient, prompt: str, timeout: float) -> tuple[ResultMessage, bool]:
    """The turn's result, and whether it finished before the deadline rather than being interrupted."""
    await client.query(prompt)
    completed = asyncio.create_task(_result(client))
    try:
        return await asyncio.wait_for(asyncio.shield(completed), timeout), True
    except TimeoutError:
        await client.interrupt()
        # Draining to the result leaves the session usable for a later resume, and names it.
        return await completed, False


def parse_answer(output: Any, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The structured output when it conforms to `schema`, or why it is not acceptable."""
    if output is None:
        return None, "the answer was not given as structured output"
    if not isinstance(output, dict):
        return None, "the structured output is not a JSON object"
    problem = submission_problem(output, schema)
    return (output, None) if problem is None else (None, problem)


def describe(result: ResultMessage) -> str:
    """Why a run that ended in error failed, in the runtime's own words where it gave any."""
    return "; ".join(result.errors or []) or result.result or f"The run ended {result.subtype}."


def signed_in(account: dict[str, Any]) -> bool:
    """Whether the account Claude Code resolved at startup can make requests."""
    # A cloud provider authenticates with that cloud's credentials and reports no Anthropic key or token.
    if account.get("apiProvider", "firstParty") != "firstParty":
        return True
    return bool(account.get("apiKeySource")) or account.get("tokenSource", "none") != "none"


def scratch_directory() -> Path:
    """Where plain completions run, one directory so their sessions share a single ~/.claude/projects entry."""
    return state_directory("claude")


async def _result(client: ClaudeSDKClient) -> ResultMessage:
    async for message in client.receive_response():
        if isinstance(message, ResultMessage):
            return message
    raise RuntimeError("Claude Code ended the turn without a result.")


async def _account() -> dict[str, Any]:
    directory = scratch_directory()
    directory.mkdir(parents=True, exist_ok=True)
    async with ClaudeSDKClient(options=ClaudeAgentOptions(cwd=directory, tools=[])) as client:
        info = await client.get_server_info()
    return (info or {}).get("account") or {}
