"""Export repository inputs without mounting their Git metadata or changing the source checkout."""

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tarfile
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile
import time
from typing import BinaryIO
import zlib

from digital_twin_universe.schemas import DigitalTwinUniverseError, RepositorySnapshot

MAX_FILES = 100_000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
MAX_HISTORY_BYTES = 2 * 1024 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 120


def _error(code: str, message: str) -> DigitalTwinUniverseError:
    return DigitalTwinUniverseError(
        code,
        message,
        "Use a stable, smaller checkout with the intended files committed or present; retry the launch explicitly. "
        "No source files or Git metadata were changed.",
    )


@dataclass
class Git:
    scratch: Path

    def run(self, repository: Path, *arguments: str, data: bytes | BinaryIO | None = None) -> bytes:
        with TemporaryFile(dir=self.scratch) as output:
            self.write(repository, arguments, output, MAX_METADATA_BYTES, data)
            output.seek(0)
            return output.read()

    def write(
        self,
        repository: Path,
        arguments: tuple[str, ...],
        output: BinaryIO,
        limit: int,
        data: bytes | BinaryIO | None = None,
    ) -> None:
        # Do not inherit Git directory/index overrides, credentials, user hooks, or lazy-fetch behavior.
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR") if key in os.environ}
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_NO_LAZY_FETCH="1",
            GIT_OPTIONAL_LOCKS="0",
            GIT_AUTHOR_NAME="Digital Twin Universe",
            GIT_AUTHOR_EMAIL="dtu@digital-twin-universe.invalid",
            GIT_COMMITTER_NAME="Digital Twin Universe",
            GIT_COMMITTER_EMAIL="dtu@digital-twin-universe.invalid",
        )
        command = [
            "git",
            "--no-pager",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={self.scratch / 'no-hooks'}",
            "-c",
            "maintenance.auto=false",
            "-c",
            "gc.auto=0",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repository),
            *arguments,
        ]
        with TemporaryFile(dir=self.scratch) as incoming, TemporaryFile(dir=self.scratch) as errors:
            if isinstance(data, bytes):
                incoming.write(data)
            elif data is not None:
                shutil.copyfileobj(data, incoming)
            incoming.seek(0)
            try:
                with subprocess.Popen(
                    command, stdin=incoming, stdout=output, stderr=errors, env=environment
                ) as process:
                    try:
                        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
                        while process.poll() is None:
                            if (
                                os.fstat(output.fileno()).st_size > limit
                                or os.fstat(errors.fileno()).st_size > MAX_METADATA_BYTES
                                or time.monotonic() > deadline
                            ):
                                raise _error(
                                    "repository-snapshot-limit",
                                    "Repository export exceeded its size or Git time limit.",
                                )
                            time.sleep(0.01)
                        if process.returncode:
                            raise _error(
                                "repository-snapshot-failed",
                                f"Git could not export {repository.name!r} ({arguments[0]}).",
                            )
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
            except FileNotFoundError as error:
                raise _error("git-missing", "Publishing repositories needs Git on PATH.") from error
            if os.fstat(output.fileno()).st_size > limit or os.fstat(errors.fileno()).st_size > MAX_METADATA_BYTES:
                raise _error("repository-snapshot-limit", "Repository export exceeded its size limit.")


def _identity(git: Git, source: Path) -> tuple[bytes, bytes, bytes]:
    return (
        git.run(source, "rev-parse", "HEAD").strip(),
        git.run(source, "rev-parse", "--abbrev-ref", "HEAD").strip(),
        git.run(source, "ls-files", "--stage", "-z"),
    )


def _refs(git: Git, source: Path) -> bytes:
    return git.run(source, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags")


def _unchanged(git: Git, source: Path, identity: tuple[bytes, bytes, bytes], refs: bytes | None) -> bool:
    return _identity(git, source) == identity and (refs is None or _refs(git, source) == refs)


def _selection(git: Git, source: Path, index: bytes) -> dict[str, tuple[str, str]]:
    selected = {}
    for entry in index.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        if stage != "0":
            raise _error("repository-unmerged", "Resolve the source index's merge conflicts before publishing it.")
        selected[os.fsdecode(name)] = (mode, oid)
    for name in git.run(source, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if name:
            selected[os.fsdecode(name)] = ("", "")
    if len(selected) > MAX_FILES:
        raise _error("repository-snapshot-limit", f"Repository snapshot exceeds {MAX_FILES} selected files.")
    return selected


def _read(source: Path, name: str) -> tuple[str, bytes] | None:
    parts = PurePosixPath(name).parts
    if not parts or any(part in ("..", ".git") for part in parts) or PurePosixPath(name).is_absolute():
        raise _error("repository-path-invalid", "A selected repository path is outside the supported tree.")
    with ExitStack() as stack:
        directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        try:
            for part in parts[:-1]:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                stack.callback(os.close, directory)
            before = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                return "120000", os.fsencode(os.readlink(parts[-1], dir_fd=directory))
            if not stat.S_ISREG(before.st_mode):
                raise _error("repository-file-unsupported", f"Selected file {name!r} is not a regular file or symlink.")
            if before.st_size > MAX_FILE_BYTES:
                raise _error("repository-snapshot-limit", f"Selected file {name!r} exceeds {MAX_FILE_BYTES} bytes.")
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as file:
                data = file.read(MAX_FILE_BYTES + 1)
                after = os.fstat(file.fileno())
            if len(data) > MAX_FILE_BYTES or (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise _error("repository-source-changed", f"Selected file {name!r} changed while being read.")
            return ("100755" if before.st_mode & 0o111 else "100644"), data
        except FileNotFoundError:
            return None
        except OSError as error:
            raise _error(
                "repository-source-changed",
                f"Cannot safely read selected path {name!r}; a parent may be a symlink or changed.",
            ) from error


def _tree_entries(
    git: Git, source: Path, index: bytes, destination: Path | None, object_format: str
) -> dict[str, tuple[str, str]]:
    entries = {}
    total = 0
    for name, (mode, oid) in sorted(_selection(git, source, index).items()):
        if mode == "160000":
            entries[name] = (mode, oid)
            continue
        content = _read(source, name)
        if content is None:
            continue
        mode, data = content
        total += len(data)
        if total > MAX_CONTENT_BYTES:
            raise _error("repository-snapshot-limit", f"Selected contents exceed {MAX_CONTENT_BYTES} bytes.")
        raw = f"blob {len(data)}\0".encode() + data
        oid = hashlib.new(object_format, raw).hexdigest()
        entries[name] = (mode, oid)
        if destination is not None:
            path = destination / "objects" / oid[:2] / oid[2:]
            path.parents[0].mkdir(exist_ok=True)
            path.write_bytes(zlib.compress(raw))
    return entries


def export(source: Path, destination: Path, history: bool = True) -> dict[str, str | int | bool]:
    """A self-contained bare snapshot and provenance; failed exports are removed, never retried implicitly."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise DigitalTwinUniverseError(
            "repository-snapshot-unsupported",
            "This host lacks the no-follow directory reads required for safe repository snapshot export.",
            "Export from a Linux, WSL, or macOS host with Git, or supply an explicitly prepared source archive. "
            "This limit applies to repository export and publishing, not other DTU capabilities.",
        )
    source = source.resolve()
    if destination.resolve().is_relative_to(source):
        raise _error("snapshot-destination-inside-source", "Keep snapshot output outside the source checkout.")
    destination.mkdir(parents=True, mode=0o700)
    git = Git(destination.parents[0])
    try:
        if Path(os.fsdecode(git.run(source, "rev-parse", "--show-toplevel").strip())).resolve() != source:
            raise _error("repository-not-root", "Publish a repository or linked worktree root, not its subdirectory.")
        identity = _identity(git, source)
        head, branch, index = identity
        refs = _refs(git, source) if history else None
        object_format = git.run(source, "rev-parse", "--show-object-format").decode().strip()
        shallow = git.run(source, "rev-parse", "--is-shallow-repository").strip() == b"true"
        git.run(destination, "init", "--bare", f"--object-format={object_format}", "--template=", "-q")
        if refs is not None and not shallow:
            objects = b"\n".join(sorted({head, *(line.split()[0] for line in refs.splitlines())})) + b"\n"
            with TemporaryFile(dir=destination) as packed:
                git.write(source, ("pack-objects", "--revs", "--stdout"), packed, MAX_HISTORY_BYTES, objects)
                packed.seek(0)
                # index-pack reads bytes, without consulting source worktree files, attributes, or hooks.
                git.run(destination, "index-pack", "--stdin", data=packed)
            for line in refs.splitlines():
                oid, ref = line.decode().split()
                git.run(destination, "update-ref", ref, oid)
        entries = _tree_entries(git, source, index, destination, object_format)
        if (
            not _unchanged(git, source, identity, refs)
            or _tree_entries(git, source, index, None, object_format) != entries
            or not _unchanged(git, source, identity, refs)
        ):
            checked = "source files, index, HEAD, or local refs" if refs is not None else "source files, index, or HEAD"
            raise _error("repository-source-changed", f"The {checked} changed during export.")
        listing = b"".join(
            f"{mode} {oid}\t".encode() + os.fsencode(name) + b"\0" for name, (mode, oid) in entries.items()
        )
        git.run(destination, "read-tree", "--empty")
        git.run(destination, "update-index", "-z", "--index-info", data=listing)
        tree = git.run(destination, "write-tree").decode().strip()
        current = branch.decode() if branch != b"HEAD" else "detached"
        commit = (
            git.run(
                destination,
                "commit-tree",
                tree,
                *(("-p", head.decode()) if history and not shallow else ()),
                data=b"The selected working tree at universe launch\n",
            )
            .decode()
            .strip()
        )
        git.run(destination, "update-ref", f"refs/heads/{current}", commit)
        git.run(destination, "symbolic-ref", "HEAD", f"refs/heads/{current}")
        provenance = {
            "source_head": head.decode(),
            "source_branch": branch.decode(),
            "snapshot_commit": commit,
            "snapshot_tree": tree,
            "files": len(entries),
            "shallow_source": shallow,
        }
        receipt = destination.with_name(f"{destination.name}.snapshot.json")
        receipt.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        receipt.chmod(0o600)
        return provenance
    except BaseException:
        shutil.rmtree(destination)
        raise


def export_archive(source: Path, destination: Path) -> RepositorySnapshot:
    """Export selected raw tree contents as a private tar archive, without Git archive attribute transforms."""
    if destination.resolve().is_relative_to(source.resolve()):
        raise _error("snapshot-destination-inside-source", "Keep snapshot output outside the source checkout.")
    if destination.exists() or destination.is_symlink():
        raise _error("snapshot-destination-exists", f"Snapshot destination {destination} already exists.")
    if not destination.parents[0].is_dir():
        raise _error("snapshot-destination-missing", "Create the owner-managed archive destination directory first.")
    with TemporaryDirectory(prefix="dtu-snapshot-", dir=destination.parents[0]) as scratch:
        repository = Path(scratch) / "repository"
        provenance = export(source, repository, history=False)
        git = Git(Path(scratch))
        listing = git.run(repository, "ls-tree", "-rz", "HEAD")
        with NamedTemporaryFile(dir=scratch, mode="w+b") as temporary:
            with tarfile.open(fileobj=temporary, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in listing.split(b"\0"):
                    if not entry:
                        continue
                    metadata, name = entry.split(b"\t", 1)
                    mode, kind, oid = metadata.decode().split()
                    item = tarfile.TarInfo(os.fsdecode(name))
                    if kind == "commit":
                        item.type, item.mode = tarfile.DIRTYPE, 0o755
                        archive.addfile(item)
                        continue
                    # Every selected blob was written as a loose object by export, even if history has it packed.
                    raw = zlib.decompress((repository / "objects" / oid[:2] / oid[2:]).read_bytes())
                    header, data = raw.split(b"\0", 1)
                    if header != f"blob {len(data)}".encode():
                        raise _error("repository-snapshot-failed", "A snapshot blob has invalid content.")
                    if mode == "120000":
                        item.type, item.mode, item.linkname = tarfile.SYMTYPE, 0o777, os.fsdecode(data)
                        archive.addfile(item)
                    else:
                        item.mode, item.size = (0o755 if mode == "100755" else 0o644), len(data)
                        archive.addfile(item, io.BytesIO(data))
            temporary.flush()
            temporary.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: temporary.read(1024 * 1024), b""):
                digest.update(chunk)
            size = os.fstat(temporary.fileno()).st_size
            # A hard link publishes without replacing another writer's destination; the private temp owns mode 0600.
            try:
                os.link(temporary.name, destination)
            except FileExistsError as error:
                raise _error(
                    "snapshot-destination-exists", f"Snapshot destination {destination} appeared during export."
                ) from error
        return RepositorySnapshot.model_validate(
            {
                "path": destination,
                "archive_sha256": digest.hexdigest(),
                "archive_bytes": size,
                **provenance,
            }
        )
