"""The submit tool every implementation answers a structured request through, and how a submission is judged."""

from typing import Any

import jsonschema

SUBMIT_TOOL = "submit"
MAX_INVALID_SUBMISSIONS = 2


def submission_problem(submitted: dict[str, Any] | None, schema: dict[str, Any]) -> str | None:
    """Why the submission is not acceptable, or None when it is."""
    if submitted is None:
        return f"the {SUBMIT_TOOL} tool was never called"
    try:
        jsonschema.validate(submitted, schema)
    except jsonschema.ValidationError as error:
        return f"the submission does not match the schema ({error.message})"
    return None


def resubmit_prompt(problem: str) -> str:
    """What the agent is told when its turn ended without an acceptable submission."""
    return (
        f"Your answer was not accepted: {problem}. Call the {SUBMIT_TOOL} tool now with an answer matching its schema."
    )
