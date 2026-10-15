"""Export a bounded archive from an owned container without extracting its contents on the host."""

from contextlib import suppress
import hashlib
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import subprocess
import tempfile
import time
from typing import BinaryIO

from python_on_whales import ClientNotFoundError
from python_on_whales.exceptions import DockerException

from digital_twin_universe.capabilities.universe import state
from digital_twin_universe.capabilities.universe.compose import PROJECT_LABEL, SERVICE_LABEL, compose_client
from digital_twin_universe.schemas import DigitalTwinUniverseError, FileArchive


def export_files(
    id: str, source: str, destination: Path, max_bytes: int = 268435456, timeout_seconds: int = 60
) -> FileArchive:
    """Save a private tar archive from the exact owned twin, including when it is stopped.

    Publication never overwrites an existing destination. Archive entries are not interpreted or extracted here.
    A failed export leaves the environment intact and removes only this invocation's temporary archive.
    """
    if os.name != "posix":
        raise _error(id, "export-unsupported", "Bounded archive export requires a Linux, WSL, or macOS host.")
    if not source.startswith("/") or "\0" in source or ".." in PurePosixPath(source).parts:
        raise _error(id, "export-path-invalid", "The export source must be an absolute container path.")
    if not 1 <= max_bytes <= 16 * 1024**3 or not 1 <= timeout_seconds <= 3600:
        raise _error(
            id, "export-limit-invalid", "Choose a byte limit up to 16 GiB and a timeout from 1 to 3600 seconds."
        )
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise _error(id, "export-destination-exists", "The archive destination already exists.")
    if not destination.parent.is_dir():
        raise _error(id, "export-destination-missing", "The archive destination directory does not exist.")
    with state.lifecycle_lock(id):
        record = state.read(id)
        client = compose_client(record.id)
        try:
            candidates = client.container.list(
                all=True,
                filters=[("label", f"{PROJECT_LABEL}={id}"), ("label", f"{SERVICE_LABEL}={record.twin_machine}")],
            )
            matches = [
                container
                for container in candidates
                if (container.config.labels or {}).get(PROJECT_LABEL) == id
                and (container.config.labels or {}).get(SERVICE_LABEL) == record.twin_machine
            ]
            if len(matches) != 1:
                raise _error(id, "export-twin-ambiguous", "Exactly one owned twin container is required for export.")
            command = [*client.docker_cmd, "cp", f"{matches[0].id}:{source}", "-"]
        except (ClientNotFoundError, DockerException) as error:
            raise _error(id, "export-inspection-failed", "The owned container could not be inspected.") from error
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as archive:
                size, digest = _copy_archive(id, command, archive, max_bytes, timeout_seconds)
            # A hard link publishes the completed file atomically without replacing an existing receipt.
            os.link(temporary_path, destination)
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return FileArchive(id=id, source=source, destination=destination, bytes=size, sha256=digest)
        except FileExistsError as error:
            raise _error(id, "export-destination-exists", "The archive destination already exists.") from error
        except OSError as error:
            raise _error(id, "export-io-failed", "The archive could not be saved.") from error
        finally:
            temporary_path.unlink(missing_ok=True)


def _copy_archive(
    id: str, command: list[str], archive: BinaryIO, max_bytes: int, timeout_seconds: int
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_seconds
    size = 0
    digest = hashlib.sha256()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _error(id, "export-timeout", "The archive export did not finish within its time limit.")
                    for selected, _ in selector.select(remaining):
                        maximum = min(65536, max_bytes - size + 1) if selected.data else 65536
                        chunk = os.read(selected.fd, maximum)
                        if not chunk:
                            selector.unregister(selected.fileobj)
                        elif selected.data:
                            size += len(chunk)
                            if size > max_bytes:
                                raise _error(id, "export-too-large", "The archive exceeds its byte limit.")
                            archive.write(chunk)
                            digest.update(chunk)
                        # Container-controlled diagnostics are drained without retaining them.
            try:
                returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as error:
                raise _error(
                    id, "export-timeout", "The archive export did not finish within its time limit."
                ) from error
            if returncode:
                raise _error(id, "export-failed", "Docker could not export the requested path.")
            if not size:
                raise _error(id, "export-empty", "Docker returned an empty archive.")
            archive.flush()
            os.fsync(archive.fileno())
            return size, digest.hexdigest()
        except BaseException:
            # Only this invocation's CLI and helper group are stopped, never in-container work or peer clients.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            raise
        finally:
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                process.kill()
            process.wait()


def _error(id: str, code: str, message: str) -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        code,
        message,
        "Keep the environment and inspect it; retry to a new archive path after correcting the cause.",
        universe_id=id,
    )
