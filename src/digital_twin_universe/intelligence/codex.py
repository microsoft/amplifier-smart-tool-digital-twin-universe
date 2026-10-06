"""OpenAI Codex SDK implementation of the intelligence interface."""

import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import tempfile
import time
from typing import Any

from openai_codex import ApprovalMode, AsyncCodex, AsyncThread, Sandbox, TurnResult
from openai_codex.types import GetAccountResponse, ReasoningEffort, TurnStatus

from digital_twin_universe.intelligence.schemas import AgentRequest, AgentResult
from digital_twin_universe.intelligence.submission import MAX_INVALID_SUBMISSIONS, reanswer_prompt, submission_problem
from digital_twin_universe.schemas import DigitalTwinUniverseError

SIGN_IN_DOCUMENTATION = "https://developers.openai.com/codex/auth"
# Keywords whose values are schemas, by how they hold them; walking only these leaves instance data such as
# `default` and `enum` untouched.
SUBSCHEMA_KEYWORDS = {"items", "additionalProperties", "not", "if", "then", "else", "contains"}
SUBSCHEMA_LIST_KEYWORDS = {"anyOf", "oneOf", "allOf", "prefixItems"}
SUBSCHEMA_MAP_KEYWORDS = {"properties", "patternProperties", "$defs", "definitions"}


class CodexIntelligence:
    """Runs agents through a Codex runtime on this machine, with the user's Codex sign-in and configuration."""

    def __init__(self) -> None:
        self.implementation = f"openai-codex {version('openai-codex')}"

    def preflight(self) -> None:
        try:
            account = asyncio.run(_account())
        except Exception as error:  # whatever stops the runtime from starting, the remedy is the same
            raise DigitalTwinUniverseError(
                "codex-unavailable",
                f"The Codex runtime did not start: {type(error).__name__}: {error}",
                "Check that ~/.codex/config.toml is valid, and reinstall digital-twin-universe with the [codex] extra, "
                "which brings the runtime.",
            ) from None
        if account.account is None and account.requires_openai_auth:
            raise DigitalTwinUniverseError(
                "codex-not-signed-in",
                "Codex is not signed in.",
                f"Install the Codex CLI and run `codex login`, with ChatGPT or an API key as {SIGN_IN_DOCUMENTATION} describes.",
            )

    def run(self, request: AgentRequest) -> AgentResult:
        if request.workspace is not None:
            return asyncio.run(self._run(request, request.workspace.path))
        # A plain completion still runs in a directory Codex can read and write; never the caller's.
        with tempfile.TemporaryDirectory() as scratch:
            return asyncio.run(self._run(request, Path(scratch)))

    async def _run(self, request: AgentRequest, directory: Path) -> AgentResult:
        cwd = str(directory.resolve())
        # The shell and file tools are not sandboxed to the workspace; the caller's prompt bounds the agent
        # and the caller validates before anything the agent wrote is kept.
        thread_options: dict[str, Any] = {
            "cwd": cwd,
            "model": request.model,
            "sandbox": Sandbox.full_access,
            "approval_mode": ApprovalMode.deny_all,
            # A writable thread in a project without a trust level otherwise records the project as trusted in
            # the user's ~/.codex/config.toml. An entry for the exact cwd, resolved as Codex compares it, takes
            # precedence over one for the repository root.
            "config": {"projects": {cwd: {"trust_level": "trusted"}}},
        }
        schema = request.output_schema
        strict = to_strict(schema) if schema is not None else None
        deadline = time.monotonic() + request.timeout_seconds
        timed_out = f"The agent did not finish within {request.timeout_seconds} seconds."
        # Carried out of the try so the failure paths can name the session the caller could resume.
        session_id: str | None = None
        try:
            async with AsyncCodex() as codex:
                if request.resume is not None:
                    thread = await codex.thread_resume(request.resume, **thread_options)
                else:
                    thread = await codex.thread_start(**thread_options)
                session_id = thread.id
                prompt = request.prompt
                invalid = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return AgentResult(error=timed_out, session_id=session_id)
                    result = await run_turn(
                        thread, prompt, ReasoningEffort(request.reasoning_effort), strict, remaining
                    )
                    if result is None:
                        return AgentResult(error=timed_out, session_id=session_id)
                    text = result.final_response or ""
                    if result.status != TurnStatus.completed:
                        error = result.error.message if result.error else f"The turn ended {result.status.value}."
                        return AgentResult(text=text, error=error, session_id=session_id)
                    if schema is None:
                        return AgentResult(text=text, session_id=session_id)
                    output, problem = parse_answer(result.final_response, schema)
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
        except Exception as error:  # an SDK or runtime failure is the caller's data, not a crash
            return AgentResult(error=f"{type(error).__name__}: {error}", session_id=session_id)


async def run_turn(
    thread: AsyncThread,
    prompt: str,
    effort: ReasoningEffort,
    output_schema: dict[str, Any] | None,
    timeout: float,
) -> TurnResult | None:
    """The turn's result, or None when it ran out of time and was interrupted."""
    handle = await thread.turn(prompt, effort=effort, output_schema=output_schema)
    completed = asyncio.create_task(handle.run())
    try:
        return await asyncio.wait_for(asyncio.shield(completed), timeout)
    except TimeoutError:
        await handle.interrupt()
        # Draining to the completed event leaves the thread usable for a later resume.
        await completed
        return None


def parse_answer(text: str | None, schema: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The final message as output conforming to `schema`, or why it is not acceptable."""
    try:
        answer = json.loads(text or "")
    except json.JSONDecodeError as error:
        return None, f"the final message is not JSON ({error.msg})"
    if not isinstance(answer, dict):
        return None, "the final message is not a JSON object"
    answer = without_added_nulls(answer, schema, schema)
    problem = submission_problem(answer, schema)
    return (answer, None) if problem is None else (None, problem)


def to_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` in the form the API enforces: every property required, no others allowed.

    A property that was optional becomes nullable instead, so the model can still leave it out by answering null;
    `without_added_nulls` maps those nulls back to absent keys.
    """
    strict: dict[str, Any] = {}
    for keyword, value in schema.items():
        if keyword in SUBSCHEMA_KEYWORDS and isinstance(value, dict):
            strict[keyword] = to_strict(value)
        elif keyword in SUBSCHEMA_LIST_KEYWORDS and isinstance(value, list):
            strict[keyword] = [to_strict(item) if isinstance(item, dict) else item for item in value]
        elif keyword in SUBSCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            strict[keyword] = {
                name: to_strict(item) if isinstance(item, dict) else item for name, item in value.items()
            }
        else:
            strict[keyword] = value
    if strict.get("type") == "object" or "properties" in strict:
        properties: dict[str, Any] = strict.get("properties", {})
        required = set(strict.get("required", []))
        strict["properties"] = {
            name: item if name in required else _nullable(item) for name, item in properties.items()
        }
        strict["required"] = list(properties)
        strict["additionalProperties"] = False
    return strict


def without_added_nulls(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """`value` without the nulls `to_strict` let the model give for properties `schema` leaves optional."""
    if "$ref" in schema:
        schema = _resolve(root, schema["$ref"])
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", []))
        return {
            name: without_added_nulls(item, properties.get(name, {}), root)
            for name, item in value.items()
            if item is not None or name not in properties or name in required
        }
    if isinstance(value, list):
        items = schema.get("items", {})
        return [without_added_nulls(item, items if isinstance(items, dict) else {}, root) for item in value]
    return value


def _nullable(schema: Any) -> dict[str, Any]:
    if isinstance(schema, dict) and {"type": "null"} in schema.get("anyOf", []):
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _resolve(root: dict[str, Any], reference: str) -> dict[str, Any]:
    node: Any = root
    for part in reference.removeprefix("#/").split("/"):
        node = node[part]
    return node


async def _account() -> GetAccountResponse:
    async with AsyncCodex() as codex:
        return await codex.account()
