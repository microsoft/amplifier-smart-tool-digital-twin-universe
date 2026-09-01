"""What this tool can do, said once.

One record per capability feeds every reader: the terse summary a person gets
from `-h`, the complete listing an agent gets from `--help`, and the per
capability help at both levels. Nothing here restates anything else, so the
renderings cannot disagree with each other.

Each record names the library function behind the capability. That name is what
decides whether the capability is model-backed, so the CLI never keeps a second
list of its own.
"""

from __future__ import annotations

import textwrap
from typing import NamedTuple

from amplifier_digital_twin_universe import MODEL_BACKED_CAPABILITIES

PROG = "amplifier-digital-twin-universe"
TERSE = "Stand up isolated, realistic environments from a declarative profile."

HELP_WIDTH = 78
_NAME_COLUMN = 18
_KIND_COLUMN = 17
_FIELD_INDENT = " " * (2 + _NAME_COLUMN)

OUTPUT_NOTE = (
    'Output: one JSON document on stdout. Failures emit\n{"error": {"code", "message", "remedy"}} and exit non-zero.\n'
)

MODEL_BACKED_NOTE = (
    "This capability is model-backed: it consumes tokens and may return a different\n"
    "answer on a second run. It fails rather than degrades when no model provider is\n"
    "configured.\n"
)

DETERMINISTIC_NOTE = (
    "This capability is deterministic: it returns the same answer every time and\n"
    "runs with no model provider configured.\n"
)

EXIT_SUMMARY = (
    "Exit codes: 0 success, 2 bad invocation, 3 no model provider configured,\n"
    "4 missing prerequisite, 5 the capability ran and failed.\n"
)


class Capability(NamedTuple):
    """What a caller needs to know to invoke one capability."""

    name: str
    library: str
    summary: str
    args: tuple[str, ...]
    returns: str
    exits: tuple[str, ...]
    notes: tuple[str, ...] = ()

    @property
    def model_backed(self) -> bool:
        return self.library in MODEL_BACKED_CAPABILITIES

    @property
    def kind(self) -> str:
        return "model-backed" if self.model_backed else "deterministic"

    @property
    def usage_parts(self) -> list[str]:
        """The usage line as tokens, so wrapping never splits one apart."""
        parts = [f"{PROG} {self.name}"]
        for arg in self.args:
            spec, _, qualifier = arg.partition(" (")
            qualifier = qualifier.rstrip(")")
            if qualifier == "required":
                parts.append(spec)
            elif qualifier == "repeatable":
                parts.append(f"[{spec} ...]")
            else:
                parts.append(f"[{spec}]")
        return parts


_STANDARD_EXITS = (
    "0 the capability succeeded",
    "2 the invocation was bad",
    "4 the container runtime is missing or unreachable",
    "5 the capability ran and failed",
)

_MODEL_EXITS = (
    "0 the capability succeeded",
    "2 the invocation was bad",
    "3 no model provider is configured",
    "4 a prerequisite is missing",
    "5 the capability ran and failed",
)

_MODEL_ARGS = (
    "--max-attempts N (default 3)",
    "--provider NAME",
    "--model NAME",
)

CAPABILITIES = (
    Capability(
        name="manifest",
        library="load_manifest",
        summary="Emit this tool's manifest as structured data.",
        args=(),
        returns='{"result": {smart_tool_format, name, version, description, use_cases[], platforms[], requires[]}}',
        exits=(
            "0 the manifest was emitted",
            "2 the invocation was bad",
            "5 the manifest could not be read",
        ),
    ),
    Capability(
        name="check",
        library="probe",
        summary="Report what this host can do right now.",
        args=(),
        returns=(
            '{"result": {platform, supported, ready, can_launch, prerequisites[], '
            "model_providers[], environments, notes[]}}"
        ),
        exits=(
            "0 the host was measured",
            "2 the invocation was bad",
            "5 the host could not be measured",
        ),
        notes=("Reads only. Nothing is installed, configured, or started.",),
    ),
    Capability(
        name="validate-profile",
        library="validate_profile",
        summary="Check whether a profile document is launchable.",
        args=("--file PATH|- (required)", "--var KEY=VALUE (repeatable)"),
        returns='{"result": {valid, name, description, errors[], warnings[], unresolved_variables[]}}',
        exits=(
            "0 the profile is launchable",
            "2 the invocation was bad or the profile could not be read",
            "5 the profile is not launchable",
        ),
    ),
    Capability(
        name="launch",
        library="launch",
        summary="Stand up an environment from a profile.",
        args=(
            "--profile PATH (required)",
            "--var KEY=VALUE (repeatable)",
            "--name NAME",
            "--hostname NAME",
            "--max-environments N",
        ),
        returns='{"result": {id, name, profile, status, created_at, access[]?, container_ip?, mock_services[]?, info[]}}',
        exits=_STANDARD_EXITS,
        notes=(
            (
                "Blocks until provisioning finishes, which is minutes for a profile that "
                "installs a toolchain. Nothing reaps environments, so destroy what you launch."
            ),
        ),
    ),
    Capability(
        name="exec",
        library="run",
        summary="Run one command inside an environment.",
        args=("--id ID (required)", "--command TEXT (required)", "--timeout SECS|none (default 600)"),
        returns='{"result": {id, command, exit_code, stdout, stderr}}',
        exits=(
            "0 the command ran; its own exit status is in the result",
            "2 the invocation was bad",
            "4 the container runtime is missing or unreachable",
            "5 the command could not be run",
        ),
        notes=(
            (
                "--command is one shell command line, split into arguments and run under a "
                "login shell inside the environment. For an interactive shell, use the "
                "engine's own `amplifier-digital-twin exec` instead."
            ),
        ),
    ),
    Capability(
        name="status",
        library="status",
        summary="Report one environment's state and access URLs.",
        args=("--id ID (required)",),
        returns='{"result": {id, profile, status, created_at, hostname?, access[]?}}',
        exits=_STANDARD_EXITS,
    ),
    Capability(
        name="list",
        library="list_environments",
        summary="List every environment this tool manages on this host.",
        args=(),
        returns='{"result": [{id, profile, status, created_at, hostname?, access[]?}]}',
        exits=_STANDARD_EXITS,
        notes=("Scoped to the machine. An environment another session launched appears here too.",),
    ),
    Capability(
        name="check-readiness",
        library="check_readiness",
        summary="Evaluate an environment's readiness checks once.",
        args=("--id ID (required)", "--skip-access-check"),
        returns='{"result": {ready, message, checks{}?, access{}?}}',
        exits=(
            "0 the environment is ready, or declares no readiness checks",
            "2 the invocation was bad",
            "4 the container runtime is missing or unreachable",
            "5 the environment is not ready",
        ),
        notes=("ready is null when the profile declares no checks, which is not the same as failing them.",),
    ),
    Capability(
        name="update",
        library="update",
        summary="Re-run an environment's update commands in place.",
        args=("--id ID (required)", "--var KEY=VALUE (repeatable)", "--skip-readiness"),
        returns='{"result": {id, profile, status, pypi_refreshed, cmds_run, readiness{}?}}',
        exits=_STANDARD_EXITS,
        notes=("The commands come from the profile snapshot taken at launch, not from the host copy.",),
    ),
    Capability(
        name="file-push",
        library="push_files",
        summary="Copy host paths into an environment.",
        args=(
            "--id ID (required)",
            "--source PATH (required, repeatable)",
            "--destination PATH (required)",
            "--recursive",
            "--mode MODE",
            "--uid UID",
            "--gid GID",
            "--timeout SECS (default 120)",
        ),
        returns='{"result": {id, sources[], destination, transferred}}',
        exits=_STANDARD_EXITS,
        notes=("A directory source keeps its own name under the destination, as `cp -r` does.",),
    ),
    Capability(
        name="file-pull",
        library="pull_files",
        summary="Copy environment paths out to the host.",
        args=(
            "--id ID (required)",
            "--source PATH (required, repeatable)",
            "--destination PATH (required)",
            "--recursive",
            "--timeout SECS (default 120)",
        ),
        returns='{"result": {id, sources[], destination, transferred}}',
        exits=_STANDARD_EXITS,
        notes=("Environments are ephemeral. Anything worth keeping leaves this way before teardown.",),
    ),
    Capability(
        name="destroy",
        library="destroy",
        summary="Tear down an environment and everything launched beside it.",
        args=("--id ID (required)",),
        returns='{"result": {id, destroyed}}',
        exits=_STANDARD_EXITS,
        notes=("Nothing inside survives. Pull anything worth keeping first.",),
    ),
    Capability(
        name="create-profile",
        library="create_profile",
        summary="Draft a launchable profile from a description.",
        args=(
            "--description TEXT (required)",
            "--context-file PATH",
            "--var KEY=VALUE (repeatable)",
            "--out PATH",
            *_MODEL_ARGS,
        ),
        returns='{"result": {yaml, name, description, attempts, warnings[], unresolved_variables[], usage{}, path?}}',
        exits=(
            "0 a profile was drafted",
            "2 the invocation was bad or the context file could not be read",
            "3 no model provider is configured",
            "4 a prerequisite is missing",
            "5 the draft budget was spent without a clean parse",
        ),
        notes=(
            (
                "Each draft is validated by the engine's own profile loader and repaired "
                "until it parses cleanly or the budget is spent."
            ),
        ),
    ),
    Capability(
        name="install",
        library="plan_install",
        summary="Plan what this host needs to launch environments.",
        args=("--goal TEXT", "--context-file PATH", *_MODEL_ARGS),
        returns='{"result": {ready, summary, steps[], notes[], host{}, usage{}}}',
        exits=_MODEL_EXITS,
        notes=(
            (
                "Proposes only. Nothing is installed, configured, or started, and the host "
                "evidence the plan was built from is returned with it."
            ),
        ),
    ),
    Capability(
        name="doctor",
        library="diagnose",
        summary="Diagnose a host or environment problem and name the fix.",
        args=("--symptom TEXT", "--id ID", "--context-file PATH", *_MODEL_ARGS),
        returns='{"result": {summary, findings[], evidence{}, usage{}}}',
        exits=_MODEL_EXITS,
        notes=(
            (
                "Evidence is measured before the model sees anything, and returned with the "
                "diagnosis so the reading can be checked against it. Repairs nothing."
            ),
        ),
    ),
    Capability(
        name="manage",
        library="manage",
        summary="Turn a request in words into deterministic actions.",
        args=("--request TEXT (required)", "--confirmed", *_MODEL_ARGS),
        returns='{"result": {request, summary, steps[], mutating, confirmed, executed, complete, usage{}}}',
        exits=(
            "0 the plan was produced, or ran to completion",
            "2 the invocation was bad",
            "3 no model provider is configured",
            "4 a prerequisite is missing",
            "5 a step failed, so the plan did not complete",
        ),
        notes=(
            (
                "Planning changes nothing. A plan that mutates anything runs only with "
                "--confirmed, which authorizes that one invocation and nothing else."
            ),
            "Every step is one of this tool's own deterministic capabilities, validated before it runs.",
        ),
    ),
)

BY_NAME = {capability.name: capability for capability in CAPABILITIES}

MODEL_BACKED_LINE = (
    "Model-backed capabilities: "
    + ", ".join(capability.name for capability in CAPABILITIES if capability.model_backed)
    + ". These consume\ntokens and may return a different answer on a second run. They fail rather than\n"
    "degrade when no provider is configured. Every other capability is deterministic\n"
    "and runs with no model provider configured.\n"
)


def terse_help() -> str:
    """The short summary a person reads."""
    lines = "".join(f"  {capability.name:<19}{capability.summary}\n" for capability in CAPABILITIES)
    return f"{PROG} -- {TERSE}\n\nCapabilities:\n{lines}\nRun '{PROG} --help' for the complete listing.\n"


def full_help() -> str:
    """The complete listing an agent reads before deciding how to call this tool."""
    return (
        "Capabilities:\n"
        + "\n".join(_compact_block(capability) for capability in CAPABILITIES)
        + "\n"
        + MODEL_BACKED_LINE
        + "\n"
        + OUTPUT_NOTE
        + "\n"
        + EXIT_SUMMARY
    )


def capability_help(capability: Capability) -> str:
    """One capability in full, for an agent deciding how to call it."""
    lines = [f"{PROG} {capability.name} -- [{capability.kind}] {capability.summary}", ""]
    lines += _pack(capability.usage_parts, "usage: ", " " * 7)
    lines += ["", "Arguments:"]
    lines += [f"  {arg}" for arg in capability.args] or ["  none"]
    lines += ["", "Library:"]
    lines += [f"  amplifier_digital_twin_universe.{capability.library}"]
    lines += ["", "Returns:"]
    lines += _wrap(capability.returns, "  ", "    ")
    lines += ["", "Exit codes:"]
    lines += [f"  {code}" for code in capability.exits]
    for note in capability.notes:
        lines += ["", *_wrap(note)]
    disclosure = MODEL_BACKED_NOTE if capability.model_backed else DETERMINISTIC_NOTE
    lines += ["", *disclosure.rstrip("\n").split("\n")]
    lines += ["", *OUTPUT_NOTE.rstrip("\n").split("\n")]
    return "\n".join(lines) + "\n"


def _compact_block(capability: Capability) -> str:
    kind = f"[{capability.kind}]"
    lines = [f"  {capability.name:<{_NAME_COLUMN}}{kind:<{_KIND_COLUMN}}{capability.summary}"]
    lines += _field("args", _tokens(capability.args, ",") or ["none"])
    lines += _field("returns", capability.returns.split(" "))
    lines += _field("exit", _tokens(capability.exits, ";"))
    for note in capability.notes:
        lines += _wrap(note, _FIELD_INDENT)
    return "\n".join(lines) + "\n"


def _wrap(text: str, indent: str = "", continuation: str | None = None) -> list[str]:
    return textwrap.wrap(
        text,
        width=HELP_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent if continuation is None else continuation,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _pack(tokens: list[str], indent: str, continuation: str) -> list[str]:
    """Fill lines with whole tokens. Tokens carry spaces, so wrapping cannot split one."""
    lines: list[str] = []
    current = indent
    for token in tokens:
        if current in (indent, continuation):
            current += token
        elif len(current) + 1 + len(token) <= HELP_WIDTH:
            current += f" {token}"
        else:
            lines.append(current)
            current = continuation + token
    lines.append(current)
    return lines


def _tokens(items: tuple[str, ...], separator: str) -> list[str]:
    """Items as wrap tokens, each carrying the separator that follows it."""
    return [f"{item}{separator}" for item in items[:-1]] + list(items[-1:])


def _field(label: str, tokens: list[str]) -> list[str]:
    """A labelled field in the top-level listing, wrapped under its own label."""
    first, *rest = tokens
    return _pack([f"{label}: {first}", *rest], _FIELD_INDENT, _FIELD_INDENT + " " * (len(label) + 2))
