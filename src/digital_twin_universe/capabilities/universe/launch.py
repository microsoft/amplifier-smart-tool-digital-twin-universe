"""Launch: from a profile to a running, ready universe."""

from contextlib import suppress
import hashlib
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from typing import Any

from python_on_whales import ClientNotFoundError, DockerClient
from python_on_whales.exceptions import DockerException
from python_on_whales.utils import stream_stdout_and_stderr
import yaml

from digital_twin_universe.capabilities.universe import overlay, runtime, state
from digital_twin_universe.capabilities.universe import validate as validate_module
from digital_twin_universe.capabilities.universe.compose import compose_client, measure, translate_docker_error
from digital_twin_universe.capabilities.universe.profile import Profile
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.schemas import DigitalTwinUniverseError, ProfileReport, Universe

LOG_TAIL = 20
PORT_IN_USE_PATTERN = re.compile(
    r"(?:failed to bind host port [^:]*:(?P<bound>\d+)/\w+: address already in use)"
    r"|(?:Bind for [^:]*:(?P<allocated>\d+) failed: port is already allocated)"
)
UNHEALTHY_PATTERN = re.compile(r"container (?P<container>\S+) is unhealthy")
TIMEOUT_MARKER = "application not healthy after"
BUILD_FAILED_MARKER = "failed to solve"
BAKE_FILE = "bake.yaml"
BUILDX_VERSION_PATTERN = re.compile(r"\bv(?P<major>\d+)\.(?P<minor>\d+)")
BAKE_ALLOW_VERSION = (0, 17)


def launch(profile: str | Path, timeout_seconds: int = 600, operation_id: str | None = None) -> Universe:
    """Allocate one owned launch. A repeated operation observes its attempt and never repeats effects."""
    validation = validate_module.validate(profile) if operation_id is None else None
    if validation is not None:
        if validation.unreadable is not None:
            raise validation.unreadable
        if validation.profile is None:
            raise _profile_invalid(validation.report)
    name = validation.profile.name if validation is not None and validation.profile is not None else "launch"
    id = state.new_id(name, operation_id)
    with state.lifecycle_lock(id):
        existing = state.root() / id / state.RECORD_FILE
        if existing.is_file():
            record = state.read(id)
            if (
                operation_id is None
                or record.operation_id != operation_id
                or (record.request_profile is not None and record.request_profile != str(profile))
            ):
                raise DigitalTwinUniverseError(
                    "operation-conflict",
                    "Launch identity is already owned.",
                    "Inspect the existing receipt.",
                    id,
                    operation_id,
                )
            if record.phase == "ready":
                return measure(record)
            raise DigitalTwinUniverseError(
                "operation-incomplete",
                f"Launch {id} is {record.phase}; its effects are not replayed.",
                "Inspect status, stop, or remove this environment. Use a new operation id only for explicitly requested new work.",
                id,
                operation_id,
            )
        if state.cleanup_receipt(id) is not None:
            raise DigitalTwinUniverseError(
                "operation-removed",
                f"Environment {id} was already removed.",
                "Use a new operation id for a newly requested environment.",
                id,
                operation_id,
            )
        validation = validation or validate_module.validate(profile)
        if validation.unreadable is not None:
            raise validation.unreadable
        if validation.profile is None:
            raise _profile_invalid(validation.report)
        loaded = validation.profile
        host = runtime.docker_host()
        with runtime.configured(runtime.CURRENT.get().model_copy(update={"docker_host": host})):
            scoped = compose_client(id, [loaded.path]).compose.config(return_json=True)
        checked = validate_module._validated(loaded.path, scoped)
        if checked.profile is None:
            raise _profile_invalid(checked.report)
        loaded = checked.profile._replace(name=loaded.name)
        record = UniverseRecord(
            id=id,
            name=loaded.name,
            description=loaded.description,
            profile_path=loaded.path,
            twin_machine=loaded.twin_machine,
            created_at=state.now(),
            urls=loaded.x_dtu.urls,
            operation_id=operation_id,
            phase="allocated",
            docker_host=host,
            expected_services=list(loaded.services),
            profile_digest=hashlib.sha256(loaded.path.read_bytes()).hexdigest(),
            request_profile=str(profile),
        )
        try:
            state.allocate(record)
        except FileExistsError as error:
            raise DigitalTwinUniverseError(
                "operation-incomplete",
                f"Allocation {id} already exists without a readable receipt.",
                "Preserve and inspect it before cleanup; do not replay this operation.",
                id,
                operation_id,
            ) from error
        except OSError as error:
            raise DigitalTwinUniverseError(
                "allocation-failed",
                f"Could not persist the ownership record for {id}.",
                "Inspect the state directory before retrying; Docker has not been started.",
                id,
                operation_id,
            ) from error
        try:
            _lifecycle_definition(record, loaded)
            return _launch_owned(record, loaded, timeout_seconds)
        except (ClientNotFoundError, DockerException) as error:
            _mark_failed(record)
            failure = _launch_error(record, timeout_seconds, error)
            failure.universe_id, failure.operation_id = id, operation_id
            raise failure from error
        except Exception as error:
            _mark_failed(record)
            if isinstance(error, DigitalTwinUniverseError):
                error.universe_id, error.operation_id = id, operation_id
                raise
            raise DigitalTwinUniverseError(
                "launch-failed",
                f"Launch {id} failed while preparing its owned environment: {type(error).__name__}.",
                "Inspect the retained state; no automatic replay was attempted.",
                id,
                operation_id,
            ) from error


def _mark_failed(record: UniverseRecord) -> None:
    record.phase = "failed"
    # Keep the original failure and exact owner when storage itself has become unavailable.
    with suppress(OSError):
        state.write(record)


def _launch_owned(record: UniverseRecord, loaded: Profile, timeout_seconds: int) -> Universe:
    rendered = overlay.render(record, loaded)
    record.expected_services = list(dict.fromkeys([*loaded.services, *rendered.bootstrap]))
    _lifecycle_definition(record, loaded)
    record.phase = "launching"
    state.write(record)
    # The resolved config is consumed once, so mutable profile files cannot change this launch after validation.
    # It can contain credentials: private permissions and finally cleanup limit exposure, but a killed process
    # can leave it behind. Recovery never loads or replays this file.
    definition = record.state_path / "launch.private.yaml"
    state.atomic_text(definition, yaml.safe_dump(_literal_compose(loaded.config), sort_keys=False))
    try:
        client = compose_client(record.id, [definition, *rendered.files])
        if rendered.bootstrap:
            _up(client, record, timeout_seconds, services=rendered.bootstrap)
            overlay.export_ca(record, rendered)
        buildx = _buildx_version(client) if rendered.gateway_builds else None
        if buildx is not None:
            _bake(client, record, rendered.gateway_builds, grant=buildx >= BAKE_ALLOW_VERSION)
        _up(client, record, timeout_seconds, build=buildx is None)
        record.phase = "ready"
        state.write(record)
        return measure(record)
    finally:
        definition.unlink(missing_ok=True)


def _literal_compose(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [_literal_compose(item) for item in value]
    if isinstance(value, dict):
        return {key: _literal_compose(item) for key, item in value.items()}
    return value


def _lifecycle_definition(record: UniverseRecord, loaded: Profile) -> None:
    config: dict[str, Any] = {"services": {name: {"image": "scratch"} for name in record.expected_services}}
    for kind in ("networks", "volumes"):
        definitions = loaded.config.get(kind) or {}
        if definitions:
            config[kind] = {
                name: {key: value for key, value in (definition or {}).items() if key in ("name", "external")}
                for name, definition in definitions.items()
            }
    state.atomic_text(
        record.state_path / state.LIFECYCLE_FILE, yaml.safe_dump(_literal_compose(config), sort_keys=False)
    )


def _up(
    client: DockerClient,
    record: UniverseRecord,
    timeout_seconds: int,
    services: list[str] | None = None,
    build: bool = True,
) -> None:
    """One `compose up` pass. Compose reports progress on stderr, which is passed through so a person sees it."""
    for _, line in client.compose.up(
        services=services,
        build=build,
        no_build=not build,
        wait=True,
        wait_timeout=timeout_seconds,
        stream_logs=True,
    ):
        sys.stderr.write(line.decode(errors="replace"))


def _buildx_version(client: DockerClient) -> tuple[int, int] | None:
    """Buildx's major and minor version, or None when there is no Buildx or it does not say which it is."""
    try:
        reported = client.buildx.version()
    except DockerException:
        return None
    version = BUILDX_VERSION_PATTERN.search(reported)
    return (int(version["major"]), int(version["minor"])) if version is not None else None


def _bake(client: DockerClient, record: UniverseRecord, services: list[str], grant: bool) -> None:
    """Build the images that reach the gateway over the host network, before Compose starts them.

    Compose cannot build these itself: it hands Bake a definition without `--allow=network.host`, which Buildx
    0.37.2 and later refuse, and whose `network` Buildx before 0.17 ignores. Bake reads the stack here as Compose
    resolved it, so paths, `.env`, and interpolation mean what they mean to Compose, and tags the images the overlay
    names. `grant` passes the entitlements, which Buildx before 0.17 neither checks nor accepts.
    """
    config = _build_config(client.compose.config(return_json=True), services)
    definition = record.state_path / BAKE_FILE
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=record.state_path, delete=False) as temporary:
        pending = Path(temporary.name)
        try:
            temporary.write(yaml.safe_dump(_literal_compose(config), sort_keys=False))
            temporary.close()
            pending.replace(definition)
        finally:
            pending.unlink(missing_ok=True)
    entitlements = ["network.host", *(f"fs.read={path}" for path in _local_contexts(config, services))]
    command = [
        *client.docker_cmd,
        "buildx",
        "bake",
        "--progress=plain",
        "--load",
        *([f"--allow={entitlement}" for entitlement in entitlements] if grant else []),
        "--file",
        definition,
    ]
    for _, line in stream_stdout_and_stderr(command):
        sys.stderr.write(line.decode(errors="replace"))


def _build_config(config: dict[str, Any], services: list[str]) -> dict[str, Any]:
    """Retain build inputs, never resolved runtime environment or unrelated service settings."""
    selected: dict[str, Any] = {}
    secret_names: set[str] = set()
    for name in services:
        service = config["services"][name]
        build = {key: value for key, value in service["build"].items() if not key.startswith("x-")}
        selected[name] = {"build": build, **{key: service[key] for key in ("image", "platform") if key in service}}
        for secret in build.get("secrets") or []:
            secret_names.add(secret if isinstance(secret, str) else secret["source"])
    result = {"services": selected}
    if config.get("name"):
        result["name"] = config["name"]
    if secret_names:
        secrets = {}
        for name in sorted(secret_names):
            secret = config.get("secrets", {}).get(name, {})
            if "content" in secret:
                raise DigitalTwinUniverseError(
                    "build-secret-inline",
                    f"Build secret {name!r} contains inline content that cannot be saved in bake.yaml.",
                    "Provide the build secret through a file or environment variable instead of inline content.",
                )
            secrets[name] = {
                key: value for key, value in secret.items() if key in ("file", "environment", "external", "name")
            }
        result["secrets"] = secrets
    return result


def _local_contexts(config: dict[str, Any], services: list[str]) -> list[str]:
    """Every directory on the host the builds read, each of which Bake reads only when granted."""
    paths: set[str] = set()
    for name in services:
        build = config["services"][name]["build"]
        for context in [build.get("context"), *(build.get("additional_contexts") or {}).values()]:
            if context and Path(context).is_absolute():
                paths.add(context)
    return sorted(paths)


def _profile_invalid(report: ProfileReport) -> DigitalTwinUniverseError:
    """Nothing is recorded or started for a profile that is not a universe; the report's errors are the reason."""
    return DigitalTwinUniverseError(
        "profile-invalid",
        f"{report.path} cannot be launched:\n"
        + "\n".join(f"  - [{finding.code}] {finding.message}" for finding in report.errors),
        f"Fix each one; `digital-twin-universe validate-profile --profile {report.path}` lists them again, with a remedy for each.",
    )


def _launch_error(record: UniverseRecord, timeout_seconds: int, error: Exception) -> DigitalTwinUniverseError:
    translated = translate_docker_error(error)
    if translated is not None:
        return translated
    stderr = (getattr(error, "stderr", None) or "").strip()
    left_running = (
        f"Whatever started is left as is under `docker compose -p {record.id}` for inspection; "
        f"`digital-twin-universe destroy --id {record.id}` clears it."
    )
    port = PORT_IN_USE_PATTERN.search(stderr)
    if port is not None:
        number = port.group("bound") or port.group("allocated")
        return DigitalTwinUniverseError(
            "port-in-use",
            f"Universe {record.id} could not publish port {number}: something on the host already holds it.",
            f"Free port {number} or change the host side of the `ports` entry in {record.profile_path}. {left_running}",
        )
    if BUILD_FAILED_MARKER in stderr:
        return DigitalTwinUniverseError(
            "build-failed",
            f"An image build for universe {record.id} failed:\n{_tail(stderr)}",
            f"Fix the Dockerfile the profile at {record.profile_path} builds and launch again. {left_running}",
        )
    unhealthy = UNHEALTHY_PATTERN.search(stderr)
    if unhealthy is not None:
        container = unhealthy.group("container")
        return DigitalTwinUniverseError(
            "unhealthy",
            f"Container {container} of universe {record.id} failed its healthcheck. Its last log lines:\n"
            f"{_container_logs(container, record.id)}",
            f"Fix what the healthcheck in {record.profile_path} probes, or the healthcheck itself. {left_running}",
        )
    if TIMEOUT_MARKER in stderr:
        return DigitalTwinUniverseError(
            "timeout",
            f"Universe {record.id} was still starting after {timeout_seconds}s.",
            f"Raise `timeout_seconds`, or inspect it with `docker compose -p {record.id} ps` and `logs`. {left_running}",
        )
    command = "docker buildx bake" if "bake" in (getattr(error, "docker_command", None) or []) else "docker compose up"
    return DigitalTwinUniverseError(
        "launch-failed",
        f"`{command}` failed for universe {record.id}:\n{_tail(stderr)}",
        f"Read Docker's message above. {left_running}",
    )


def _container_logs(container: str, id: str | None = None) -> str:
    try:
        return str(compose_client(id).container.logs(container, tail=LOG_TAIL)).strip()
    except DockerException as error:
        return f"(logs unavailable: {(error.stderr or '').strip()})"


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-LOG_TAIL:])
