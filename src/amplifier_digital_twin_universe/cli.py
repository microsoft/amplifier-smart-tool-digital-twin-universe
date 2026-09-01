"""Thin CLI over the library.

Argument parsing, I/O conventions, and structured output live here. Domain logic
does not: anything the CLI can do, the library can do, and every handler below is
one library call plus the shaping of its result. Results go to stdout as JSON;
failures go to stdout as a JSON error envelope with a non-zero exit code.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import shlex
import sys
from typing import Any, NoReturn

from amplifier_digital_twin_universe.catalog import (
    BY_NAME,
    PROG,
    TERSE,
    Capability,
    capability_help,
    full_help,
    terse_help,
)
from amplifier_digital_twin_universe.create import DEFAULT_MAX_ATTEMPTS, create_profile
from amplifier_digital_twin_universe.doctor import diagnose
from amplifier_digital_twin_universe.environments import (
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_FILE_TIMEOUT_SECONDS,
    check_readiness,
    destroy,
    launch,
    list_environments,
    pull_files,
    push_files,
    run,
    status,
    update,
)
from amplifier_digital_twin_universe.errors import (
    GenerationFailedError,
    MissingPrerequisiteError,
    NoProviderError,
    SmartToolError,
)
from amplifier_digital_twin_universe.install import plan_install
from amplifier_digital_twin_universe.manager import manage
from amplifier_digital_twin_universe.manifest import ManifestError, load_manifest
from amplifier_digital_twin_universe.prerequisites import probe
from amplifier_digital_twin_universe.profiles import validate_profile

EXIT_OK = 0
EXIT_BAD_INVOCATION = 2
EXIT_NO_PROVIDER = 3
EXIT_MISSING_PREREQUISITE = 4
EXIT_FAILED = 5

_EXIT_FOR_ERROR = {
    NoProviderError: EXIT_NO_PROVIDER,
    MissingPrerequisiteError: EXIT_MISSING_PREREQUISITE,
}


# ----------------------------------------------------------------- output


def _emit(document: Any) -> None:
    """Write exactly one JSON document to stdout, newline-terminated."""
    json.dump(document, sys.stdout, sort_keys=True, default=str)
    sys.stdout.write("\n")


def _emit_error(code: str, message: str, remedy: str, exit_code: int, **extra: Any) -> int:
    _emit({"error": {"code": code, "message": message, "remedy": remedy, **extra}})
    return exit_code


def _attempt(operation: Callable[[], Any], *, failed: Callable[[Any], bool] | None = None) -> int:
    """Run one library call and shape whatever comes back into the output contract."""
    try:
        result = operation()
    except GenerationFailedError as exc:
        return _emit_error(exc.code, exc.message, exc.remedy, EXIT_FAILED, attempts=exc.attempts)
    except SmartToolError as exc:
        return _emit_error(exc.code, exc.message, exc.remedy, _EXIT_FOR_ERROR.get(type(exc), EXIT_FAILED))
    except ValueError as exc:
        return _emit_error(
            "bad_invocation",
            str(exc),
            f"Run '{PROG} --help' for the arguments each capability takes.",
            EXIT_BAD_INVOCATION,
        )
    _emit({"result": result})
    return EXIT_FAILED if failed is not None and failed(result) else EXIT_OK


class _EnvelopeParser(argparse.ArgumentParser):
    """argparse parser that reports bad invocations as JSON envelopes on stdout.

    argparse's own handling prints a usage dump and exits 2. The exit code is the
    part a caller can rely on, so it is kept; the dump is replaced with the same
    envelope every other failure emits, carrying a code to branch on and a remedy
    to act on.
    """

    def error(self, message: str) -> NoReturn:
        _emit_error(
            "bad_invocation",
            message,
            f"Run '{PROG} --help' for the full list of capabilities and their arguments.",
            EXIT_BAD_INVOCATION,
        )
        raise SystemExit(EXIT_BAD_INVOCATION)


# ------------------------------------------------------------- deterministic


def cmd_manifest(_args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest()
    except ManifestError as exc:
        return _emit_error(
            "manifest_unreadable",
            str(exc),
            "Reinstall the tool so its manifest ships with the package.",
            EXIT_FAILED,
        )
    _emit({"result": manifest.to_dict()})
    return EXIT_OK


def cmd_check(_args: argparse.Namespace) -> int:
    return _attempt(lambda: probe().to_dict())


def cmd_validate_profile(args: argparse.Namespace) -> int:
    try:
        yaml_text = _read_source(args.file)
    except OSError as exc:
        return _emit_error(
            "unreadable_input",
            str(exc),
            "Pass a readable path to --file, or '-' to read the profile from stdin.",
            EXIT_BAD_INVOCATION,
        )
    # Sources resolve against the profile's own directory, as they do at launch.
    base_dir = None if args.file == "-" else Path(args.file).resolve().parent
    report = validate_profile(yaml_text, _parse_vars(args.var), base_dir=base_dir)
    _emit({"result": report.to_dict()})
    return EXIT_OK if report.valid else EXIT_FAILED


def cmd_launch(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: launch(
            args.profile,
            variables=_parse_vars(args.var),
            name=args.name,
            hostname=args.hostname,
            max_environments=args.max_environments,
        )
    )


def cmd_exec(args: argparse.Namespace) -> int:
    command = shlex.split(args.command)
    if not command:
        return _emit_error(
            "bad_invocation",
            "--command is empty.",
            "Pass one shell command line, such as --command 'uname -a'.",
            EXIT_BAD_INVOCATION,
        )
    return _attempt(lambda: run(args.id, command, timeout=args.timeout))


def cmd_status(args: argparse.Namespace) -> int:
    return _attempt(lambda: status(args.id))


def cmd_list(_args: argparse.Namespace) -> int:
    return _attempt(list_environments)


def cmd_check_readiness(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: check_readiness(args.id, skip_access_check=args.skip_access_check),
        failed=lambda result: result.get("ready") is False,
    )


def cmd_update(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: update(
            args.id,
            variables=_parse_vars(args.var),
            skip_readiness=args.skip_readiness,
        )
    )


def cmd_file_push(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: push_files(
            args.id,
            args.source,
            args.destination,
            recursive=args.recursive,
            mode=args.mode,
            uid=args.uid,
            gid=args.gid,
            timeout=args.timeout,
        )
    )


def cmd_file_pull(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: pull_files(
            args.id,
            args.source,
            args.destination,
            recursive=args.recursive,
            timeout=args.timeout,
        )
    )


def cmd_destroy(args: argparse.Namespace) -> int:
    return _attempt(lambda: destroy(args.id))


# -------------------------------------------------------------- model-backed


def cmd_create_profile(args: argparse.Namespace) -> int:
    context, failure = _read_context(args.context_file)
    if failure is not None:
        return failure

    def draft() -> dict[str, Any]:
        result = create_profile(
            args.description,
            context=context,
            variables=_parse_vars(args.var),
            max_attempts=args.max_attempts,
            provider=args.provider,
            model=args.model,
        )
        document = result.to_dict()
        if args.out:
            # A capability that produces an artifact identifies it rather than
            # embedding it in a message.
            document["path"] = _write_artifact(args.out, result.yaml_text)
        return document

    return _attempt(draft)


def cmd_install(args: argparse.Namespace) -> int:
    context, failure = _read_context(args.context_file)
    if failure is not None:
        return failure
    return _attempt(
        lambda: plan_install(
            goal=args.goal,
            context=context,
            max_attempts=args.max_attempts,
            provider=args.provider,
            model=args.model,
        ).to_dict()
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    context, failure = _read_context(args.context_file)
    if failure is not None:
        return failure
    return _attempt(
        lambda: diagnose(
            args.symptom,
            environment_id=args.id,
            context=context,
            max_attempts=args.max_attempts,
            provider=args.provider,
            model=args.model,
        ).to_dict()
    )


def cmd_manage(args: argparse.Namespace) -> int:
    return _attempt(
        lambda: manage(
            args.request,
            confirmed=args.confirmed,
            max_attempts=args.max_attempts,
            provider=args.provider,
            model=args.model,
        ).to_dict(),
        failed=lambda result: not result.get("complete", True),
    )


# ------------------------------------------------------------------- inputs


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _read_context(path: str | None) -> tuple[str | None, int | None]:
    """The context payload, or the exit code that says why it could not be read."""
    if not path:
        return None, None
    try:
        return _read_source(path), None
    except OSError as exc:
        return None, _emit_error(
            "unreadable_input",
            str(exc),
            "Pass a readable path to --context-file.",
            EXIT_BAD_INVOCATION,
        )


def _write_artifact(path: str, content: str) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return str(out)


def _parse_vars(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(
                _emit_error(
                    "bad_invocation",
                    f"--var expects KEY=VALUE, got {pair!r}.",
                    "Pass each variable as --var NAME=value.",
                    EXIT_BAD_INVOCATION,
                )
            )
        out[key] = value
    return out


def _parse_timeout(value: str) -> int | None:
    """Seconds, or None when the caller asked for no timeout at all."""
    normalized = value.strip().lower()
    if normalized in ("none", "null"):
        return None
    try:
        return int(normalized)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer number of seconds or 'none', got {value!r}") from None


# -------------------------------------------------------------------- help


class _TerseHelpAction(argparse.Action):
    """`-h` prints the short summary; `--help` prints the complete listing."""

    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser: argparse.ArgumentParser, *_rest: Any) -> NoReturn:
        sys.stdout.write(terse_help())
        raise SystemExit(EXIT_OK)


class _CapabilityHelpAction(argparse.Action):
    """`--help` on a capability: everything needed to call that capability."""

    def __init__(self, option_strings: list[str], dest: str, capability: Capability, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, **kwargs)
        self.capability = capability

    def __call__(self, parser: argparse.ArgumentParser, *_rest: Any) -> NoReturn:
        sys.stdout.write(capability_help(self.capability))
        raise SystemExit(EXIT_OK)


# ------------------------------------------------------------------ parser


def _add(sub: argparse._SubParsersAction, name: str, handler: Callable[..., int]) -> argparse.ArgumentParser:
    """Register one capability with the same two-level help the top level has."""
    capability = BY_NAME[name]
    label = f"[{capability.kind}] {capability.summary}"
    parser = sub.add_parser(name, help=label, description=label, add_help=False)
    parser.add_argument("-h", action="help", help="Terse summary for a person.")
    parser.add_argument(
        "--help",
        action=_CapabilityHelpAction,
        capability=capability,
        help="Everything an agent needs to call this capability.",
    )
    parser.set_defaults(func=handler)
    return parser


def _add_environment_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", metavar="ID", required=True, help="The environment id, as reported by `list`.")


def _add_vars(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--var", action="append", metavar="KEY=VALUE", help="Launch variable. Repeatable.")


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-attempts",
        metavar="N",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Draft-and-repair budget (default {DEFAULT_MAX_ATTEMPTS}).",
    )
    parser.add_argument("--provider", metavar="NAME", help="Model provider to use.")
    parser.add_argument("--model", metavar="NAME", help="Model to use.")


def build_parser() -> argparse.ArgumentParser:
    parser = _EnvelopeParser(
        prog=PROG,
        description=TERSE,
        epilog=full_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", action=_TerseHelpAction, help="Terse summary for a person.")
    parser.add_argument(
        "--help",
        action="help",
        help="Complete capability listing for an agent deciding how to call this tool.",
    )

    sub = parser.add_subparsers(dest="capability")

    _add(sub, "manifest", cmd_manifest)
    _add(sub, "check", cmd_check)

    p_validate = _add(sub, "validate-profile", cmd_validate_profile)
    p_validate.add_argument(
        "--file", metavar="PATH|-", required=True, help="Path to the profile YAML, or '-' for stdin."
    )
    _add_vars(p_validate)

    p_launch = _add(sub, "launch", cmd_launch)
    p_launch.add_argument("--profile", metavar="PATH", required=True, help="Path to the profile YAML to launch.")
    _add_vars(p_launch)
    p_launch.add_argument("--name", metavar="NAME", help="Name the environment instead of generating one.")
    p_launch.add_argument("--hostname", metavar="NAME", help="Register NAME.local for the environment over mDNS.")
    p_launch.add_argument(
        "--max-environments",
        metavar="N",
        type=int,
        help="Refuse to launch beyond N concurrent environments. 0 removes the ceiling.",
    )

    p_exec = _add(sub, "exec", cmd_exec)
    _add_environment_id(p_exec)
    p_exec.add_argument("--command", metavar="TEXT", required=True, help="One shell command line to run inside.")
    p_exec.add_argument(
        "--timeout",
        metavar="SECS|none",
        type=_parse_timeout,
        default=DEFAULT_EXEC_TIMEOUT_SECONDS,
        help=f"Seconds to allow, or 'none' (default {DEFAULT_EXEC_TIMEOUT_SECONDS}).",
    )

    p_status = _add(sub, "status", cmd_status)
    _add_environment_id(p_status)

    _add(sub, "list", cmd_list)

    p_readiness = _add(sub, "check-readiness", cmd_check_readiness)
    _add_environment_id(p_readiness)
    p_readiness.add_argument(
        "--skip-access-check",
        action="store_true",
        help="Evaluate only the in-environment checks, not host-side port reachability.",
    )

    p_update = _add(sub, "update", cmd_update)
    _add_environment_id(p_update)
    _add_vars(p_update)
    p_update.add_argument("--skip-readiness", action="store_true", help="Do not re-run readiness checks afterwards.")

    p_push = _add(sub, "file-push", cmd_file_push)
    _add_environment_id(p_push)
    p_push.add_argument("--source", action="append", required=True, metavar="PATH", help="Host path. Repeatable.")
    p_push.add_argument("--destination", metavar="PATH", required=True, help="Path inside the environment.")
    p_push.add_argument("--recursive", action="store_true", help="Copy directories and their contents.")
    p_push.add_argument("--mode", metavar="MODE", help="Permission bits to set, such as 0644.")
    p_push.add_argument("--uid", metavar="UID", type=int, help="Owner to set inside the environment.")
    p_push.add_argument("--gid", metavar="GID", type=int, help="Group to set inside the environment.")
    p_push.add_argument(
        "--timeout",
        metavar="SECS",
        type=int,
        default=DEFAULT_FILE_TIMEOUT_SECONDS,
        help=f"Seconds to allow per transfer (default {DEFAULT_FILE_TIMEOUT_SECONDS}).",
    )

    p_pull = _add(sub, "file-pull", cmd_file_pull)
    _add_environment_id(p_pull)
    p_pull.add_argument("--source", action="append", required=True, metavar="PATH", help="Path inside. Repeatable.")
    p_pull.add_argument("--destination", metavar="PATH", required=True, help="Host path to write to.")
    p_pull.add_argument("--recursive", action="store_true", help="Copy directories and their contents.")
    p_pull.add_argument(
        "--timeout",
        metavar="SECS",
        type=int,
        default=DEFAULT_FILE_TIMEOUT_SECONDS,
        help=f"Seconds to allow per transfer (default {DEFAULT_FILE_TIMEOUT_SECONDS}).",
    )

    p_destroy = _add(sub, "destroy", cmd_destroy)
    _add_environment_id(p_destroy)

    p_create = _add(sub, "create-profile", cmd_create_profile)
    p_create.add_argument("--description", metavar="TEXT", required=True, help="What you want to stand up and test.")
    p_create.add_argument("--context-file", metavar="PATH", help="Extra material for the model, read into the payload.")
    _add_vars(p_create)
    p_create.add_argument("--out", metavar="PATH", help="Write the profile YAML here and report the path.")
    _add_model_options(p_create)

    p_install = _add(sub, "install", cmd_install)
    p_install.add_argument("--goal", metavar="TEXT", help="What you want to use the environments for.")
    p_install.add_argument(
        "--context-file", metavar="PATH", help="Extra material for the model, read into the payload."
    )
    _add_model_options(p_install)

    p_doctor = _add(sub, "doctor", cmd_doctor)
    p_doctor.add_argument("--symptom", metavar="TEXT", help="What you observed, in your own words.")
    p_doctor.add_argument("--id", metavar="ID", help="Measure this environment as well as the host.")
    p_doctor.add_argument("--context-file", metavar="PATH", help="Extra material for the model, read into the payload.")
    _add_model_options(p_doctor)

    p_manage = _add(sub, "manage", cmd_manage)
    p_manage.add_argument("--request", metavar="TEXT", required=True, help="What you want done, in your own words.")
    p_manage.add_argument(
        "--confirmed",
        action="store_true",
        help="Run the planned steps. Authorizes this invocation only.",
    )
    _add_model_options(p_manage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "capability", None):
        return _emit_error(
            "no_capability",
            "No capability was named.",
            f"Run '{PROG} --help' to see the available capabilities.",
            EXIT_BAD_INVOCATION,
        )
    try:
        return int(args.func(args))
    except Exception as exc:
        # A failure nothing anticipated is still a failure a caller has to see as
        # data. The traceback goes to stderr for a person; the envelope goes to
        # stdout for whatever is parsing it.
        import traceback

        traceback.print_exc()
        return _emit_error(
            "unexpected",
            f"{type(exc).__name__}: {exc}",
            "This is a defect in the tool. Report it with the traceback on stderr.",
            EXIT_FAILED,
        )


if __name__ == "__main__":
    raise SystemExit(main())
