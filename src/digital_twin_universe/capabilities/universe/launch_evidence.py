"""Observation at the originating foreground dispatch; never reconstructed from inventory."""

from contextlib import suppress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Literal

from python_on_whales import ClientNotFoundError, DockerClient

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.state import UniverseRecord
from digital_twin_universe.schemas import LaunchDispatchPhase, LaunchTerminalWitness


class LaunchEvidence:
    def __init__(self, record: UniverseRecord, client: DockerClient) -> None:
        self.record = record
        self.client = client
        self.completed: list[LaunchDispatchPhase] = []
        self.engine_id: str | None = None
        self.socket_identity: tuple[int, int] | None = None
        host = record.docker_host
        if record.operation_id is not None and os.name == "posix" and host is not None and host.startswith("unix:///"):
            self.socket_identity = _socket_identity(host)
            if self.socket_identity is not None:
                self.engine_id = _engine_id(client, host)

    def finish(self, error_category: Literal["post-dispatch-failed"] | None = None) -> None:
        operation_id, host = self.record.operation_id, self.record.docker_host
        if operation_id is None or host is None or self.engine_id is None or self.socket_identity is None:
            return
        if not self.completed or self.completed[-1] != "final-up":
            return
        if _engine_id(self.client, host) != self.engine_id or _socket_identity(host) != self.socket_identity:
            return
        witness = LaunchTerminalWitness(
            version=1,
            operation_id=operation_id,
            project=self.record.id,
            docker_endpoint_sha256=state.endpoint_digest(host),
            engine_id=self.engine_id,
            socket_device=self.socket_identity[0],
            socket_inode=self.socket_identity[1],
            phase="final-up",
            completed=tuple(self.completed),
            error_category=error_category,
            rollback="not-dispatched-by-dtu",
        )
        # The existing launch lock excludes writers for this project. Evidence failure must not replace
        # the launch result or create a retry; with no saved witness the caller has no terminal proof.
        with suppress(OSError):
            path = state.launch_witness_path(self.record.id)
            if not path.exists():
                state.atomic_text(path, witness.model_dump_json())


def _socket_identity(host: str) -> tuple[int, int] | None:
    try:
        info = Path(host.removeprefix("unix://")).stat()
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISSOCK(info.st_mode) else None


def _engine_id(client: DockerClient, host: str) -> str | None:
    try:
        command = list(client.docker_cmd)
        if command[-2:] != ["--host", host]:
            return None
        result = subprocess.run(
            [*command, "info", "--format", "{{json .ID}}"], capture_output=True, check=True, timeout=3
        )
        value = json.loads(result.stdout)
    except (ClientNotFoundError, OSError, subprocess.SubprocessError, ValueError):
        return None
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:-]{0,127}", value) else None
