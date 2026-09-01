"""The environment lifecycle, over the Digital Twin Universe engine.

Deterministic. Runs with no model provider configured.

Every function here is a thin adapter over one `amplifier_bundle_digital_twin_universe.engine`
call. The engine owns the behavior; this module owns the contract: a profile path
that resolves the same way at validation time and at launch time, a ceiling on how
many environments one host accumulates, and failures that carry a code and a remedy
instead of a bare `RuntimeError`.

Progress narration from the engine goes to stderr, where humans read it. Results
go to the caller as data.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
from typing import Any

from amplifier_digital_twin_universe.errors import (
    EnvironmentLimitError,
    EnvironmentNotFoundError,
    MissingPrerequisiteError,
    OperationFailedError,
    OperationTimedOutError,
    ProfileInvalidError,
    ProfileNotFoundError,
)

DEFAULT_MAX_ENVIRONMENTS = 15
MAX_ENVIRONMENTS_ENV_VAR = "AMPLIFIER_DTU_MAX_ENVIRONMENTS"
DEFAULT_EXEC_TIMEOUT_SECONDS = 600
DEFAULT_FILE_TIMEOUT_SECONDS = 120

__all__ = [
    "check_readiness",
    "destroy",
    "launch",
    "list_environments",
    "pull_files",
    "push_files",
    "run",
    "status",
    "update",
]


def launch(
    profile: str | Path,
    *,
    variables: dict[str, str] | None = None,
    name: str | None = None,
    hostname: str | None = None,
    max_environments: int | None = None,
) -> dict[str, Any]:
    """Stand up one environment from a profile and return how to reach it.

    Deterministic. Requires `incus`.

    `profile` is a path to a profile document. Blocks until provisioning finishes,
    which is minutes for a profile that installs a toolchain.

    Returns the environment id, its status, the profile name, and, when the profile
    declares them, the access URLs, the container IP, and the mock services running
    beside it.
    """
    path = resolve_profile(profile)
    _enforce_limit(max_environments)
    return _call(
        f"launching {path.name}",
        _engine().launch,
        str(path),
        dict(variables or {}),
        name=name,
        hostname=hostname,
    )


def run(
    environment_id: str,
    command: list[str],
    *,
    timeout: int | None = DEFAULT_EXEC_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one command inside an environment and capture what it produced.

    Deterministic. Requires `incus`.

    The command runs under a login shell, so it sees the environment the profile
    provisioned. A non-zero exit status is a result, not a failure of this call:
    it is reported as `exit_code` alongside the captured output.
    """
    if not command:
        raise ValueError("command must not be empty")
    return _call(
        f"running a command in {environment_id}",
        _engine().exec_command,
        environment_id,
        list(command),
        timeout=timeout,
    )


def status(environment_id: str) -> dict[str, Any]:
    """One environment: its state, profile, creation time, and access URLs.

    Deterministic. Requires `incus`.
    """
    return _call(f"reading the status of {environment_id}", _engine().status, environment_id)


def list_environments() -> list[dict[str, Any]]:
    """Every environment this tool manages on this host.

    Deterministic. Requires `incus`.

    Scoped to the machine, not to a session or a user. An environment another
    session launched appears here too.
    """
    return _call("listing environments", _engine().list_environments)


def check_readiness(environment_id: str, *, skip_access_check: bool = False) -> dict[str, Any]:
    """Evaluate an environment's readiness checks once.

    Deterministic. Requires `incus`.

    `ready` is `true`, `false`, or `null`. Null means the profile declares no
    readiness checks, which is not the same as failing them. Blocks for as long as
    the profile's access-port verification budget allows.
    """
    return _call(
        f"checking the readiness of {environment_id}",
        _engine().check_readiness,
        environment_id,
        skip_access_check=skip_access_check,
    )


def update(
    environment_id: str,
    *,
    variables: dict[str, str] | None = None,
    skip_readiness: bool = False,
) -> dict[str, Any]:
    """Re-run a running environment's update commands in place.

    Deterministic. Requires `incus`.

    The commands come from the profile snapshot taken at launch, so an environment
    updates against the profile it was built from even if the host copy has since
    moved or changed.
    """
    return _call(
        f"updating {environment_id}",
        _engine().update,
        environment_id,
        dict(variables or {}),
        skip_readiness=skip_readiness,
    )


def push_files(
    environment_id: str,
    sources: list[str],
    destination: str,
    *,
    recursive: bool = False,
    create_dirs: bool = True,
    mode: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
    timeout: int = DEFAULT_FILE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Copy host paths into an environment.

    Deterministic. Requires `incus`.

    A directory source is walked whether or not `recursive` is set, and its own
    name is preserved under the destination. `timeout` bounds each underlying
    transfer, not the whole tree.
    """
    if not sources:
        raise ValueError("sources must not be empty")
    _call(
        f"pushing files into {environment_id}",
        _engine().file_push,
        environment_id,
        list(sources),
        destination,
        recursive=recursive,
        create_dirs=create_dirs,
        mode=mode,
        uid=uid,
        gid=gid,
        timeout=timeout,
    )
    return {
        "id": environment_id,
        "sources": list(sources),
        "destination": destination,
        "transferred": True,
    }


def pull_files(
    environment_id: str,
    sources: list[str],
    destination: str,
    *,
    recursive: bool = False,
    create_dirs: bool = True,
    timeout: int = DEFAULT_FILE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Copy environment paths out to the host.

    Deterministic. Requires `incus`.

    Environments are ephemeral, so anything worth keeping leaves this way before
    teardown.
    """
    if not sources:
        raise ValueError("sources must not be empty")
    _call(
        f"pulling files from {environment_id}",
        _engine().file_pull,
        environment_id,
        list(sources),
        destination,
        recursive=recursive,
        create_dirs=create_dirs,
        timeout=timeout,
    )
    return {
        "id": environment_id,
        "sources": list(sources),
        "destination": destination,
        "transferred": True,
    }


def destroy(environment_id: str) -> dict[str, Any]:
    """Tear down an environment and everything launched beside it.

    Deterministic. Requires `incus`.

    Stops the mock sidecars, releases the mDNS hostname, and deletes the container.
    Nothing inside survives.
    """
    return _call(f"destroying {environment_id}", _engine().destroy, environment_id)


def resolve_profile(profile: str | Path) -> Path:
    """The profile document a name refers to, or a failure naming what is missing.

    Deterministic. Requires no model provider.
    """
    path = Path(profile).expanduser()
    if path.is_file():
        return path.resolve()
    raise ProfileNotFoundError(
        f"No profile document at {path}.",
        "Pass the path to a profile YAML file. Profile names shipped inside the "
        "engine's repository do not resolve from an installed package.",
    )


def max_environments(requested: int | None = None) -> int:
    """The concurrent environment ceiling. Zero means no ceiling.

    Precedence: the argument, then `AMPLIFIER_DTU_MAX_ENVIRONMENTS`, then the
    default of 15.
    """
    if requested is not None:
        return requested
    configured = os.environ.get(MAX_ENVIRONMENTS_ENV_VAR, "").strip()
    if not configured:
        return DEFAULT_MAX_ENVIRONMENTS
    try:
        return int(configured)
    except ValueError as exc:
        raise ValueError(f"{MAX_ENVIRONMENTS_ENV_VAR} must be an integer, got {configured!r}") from exc


def _enforce_limit(requested: int | None) -> None:
    ceiling = max_environments(requested)
    if ceiling <= 0:
        return
    live = len(list_environments())
    if live >= ceiling:
        raise EnvironmentLimitError(
            f"{live} environment(s) are already running and the ceiling is {ceiling}.",
            "Destroy an environment you no longer need, or raise the ceiling with "
            f"{MAX_ENVIRONMENTS_ENV_VAR}. Nothing reaps environments automatically.",
        )


def _engine() -> Any:
    from amplifier_bundle_digital_twin_universe import engine

    return engine


def _call(activity: str, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one engine call, translating whatever it raises."""
    try:
        return operation(*args, **kwargs)
    except Exception as exc:
        raise _as_smart_tool_error(exc, activity) from exc


def _as_smart_tool_error(exc: Exception, activity: str) -> Exception:
    from amplifier_bundle_digital_twin_universe.incus import IncusError

    text = str(exc)

    if isinstance(exc, subprocess.TimeoutExpired):
        return OperationTimedOutError(
            f"Timed out {activity}.",
            "Raise the timeout, or check the environment with `status` and `check-readiness`.",
        )
    if isinstance(exc, FileNotFoundError):
        return ProfileNotFoundError(f"Failed {activity}: {text}", "Pass the path to a profile YAML file.")
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return ProfileInvalidError(
            f"The profile is not launchable: {text}",
            "Run `validate-profile --file <path>` to see every finding at once.",
        )
    if text.startswith("Environment not found"):
        return EnvironmentNotFoundError(
            text,
            "Run `list` to see the environments on this host. An id from another machine does not resolve here.",
        )
    if isinstance(exc, IncusError) or "incus" in text.lower():
        return MissingPrerequisiteError(
            f"Failed {activity}: {text}",
            "Confirm the container runtime with `check`. If incus is installed but "
            "unreachable, add yourself to the incus-admin group and start a new login session.",
        )
    if isinstance(exc, RuntimeError):
        return OperationFailedError(f"Failed {activity}: {text}", "Run `doctor` with this message as the symptom.")
    return exc
