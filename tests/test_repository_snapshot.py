"""Repository export on temporary local Git repositories; no Docker or model provider."""

import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from digital_twin_universe import lib
from digital_twin_universe.capabilities.universe import repository_snapshot as snapshots
from digital_twin_universe.schemas import DigitalTwinUniverseError


@pytest.fixture(autouse=True)
def snapshot_platform(request: pytest.FixtureRequest) -> None:
    if request.node.name == "test_unsupported_snapshot_reports_specific_platform_remedy":
        return
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        pytest.skip("Safe repository snapshots need no-follow directory reads")


def git(path: Path, *args: str) -> bytes:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(path),
            *args,
        ],
        check=True,
        capture_output=True,
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-b", "main", "-q")
    (root / "changed").write_text("original")
    (root / "deleted").write_text("remove me")
    (root / ".gitignore").write_text("ignored\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    git(root, "tag", "v1")
    return root


@pytest.mark.parametrize("linked", [False, True])
def test_snapshot_preserves_current_files_refs_and_original_metadata(
    repository: Path, tmp_path: Path, linked: bool
) -> None:
    source = repository
    if linked:
        source = tmp_path / "linked"
        git(repository, "worktree", "add", "-qb", "feature", str(source))
    (source / "changed").write_text("staged")
    git(source, "add", "changed")
    (source / "changed").write_text("selected working bytes\r\n")
    (source / "deleted").unlink()
    (source / "new").write_text("new file")
    (source / "ignored").write_text("not selected")
    (source / "executable").write_text("#!/bin/sh\n")
    (source / "executable").chmod(0o755)
    (source / "outside").symlink_to(tmp_path / "private")
    (tmp_path / "private").write_text("HOST_SECRET_SENTINEL")
    before = git(source, "status", "--porcelain=v1", "-z")
    original_head = git(source, "rev-parse", "HEAD")
    original_index = git(source, "ls-files", "--stage", "-z")
    index_path = Path(os.fsdecode(git(source, "rev-parse", "--git-path", "index").strip()))
    if not index_path.is_absolute():
        index_path = source / index_path
    index_bytes, index_modified = index_path.read_bytes(), index_path.stat().st_mtime_ns
    destination = tmp_path / "snapshot"

    result = snapshots.export(source, destination)

    assert index_path.read_bytes() == index_bytes
    assert index_path.stat().st_mtime_ns == index_modified
    assert git(destination, "show", "HEAD:changed") == b"selected working bytes\r\n"
    assert git(destination, "show", "HEAD:new") == b"new file"
    assert git(destination, "show", "HEAD:outside") == os.fsencode(tmp_path / "private")
    names = git(destination, "ls-tree", "-rz", "--name-only", "HEAD").split(b"\0")
    assert b"deleted" not in names
    assert b"ignored" not in names
    assert git(destination, "ls-tree", "HEAD", "executable").startswith(b"100755")
    assert git(destination, "rev-parse", "v1") == original_head
    if linked:
        assert git(destination, "rev-parse", "main") == original_head
    assert git(source, "status", "--porcelain=v1", "-z") == before
    assert git(source, "rev-parse", "HEAD") == original_head
    assert git(source, "ls-files", "--stage", "-z") == original_index
    assert result["snapshot_tree"] == git(destination, "rev-parse", "HEAD^{tree}").decode().strip()
    assert json.loads(destination.with_name(f"{destination.name}.snapshot.json").read_text()) == result
    assert b"HOST_SECRET_SENTINEL" not in git(destination, "show", "HEAD:outside")
    git(destination, "fsck", "--full")


def test_snapshot_never_runs_source_hooks_or_filters(repository: Path, tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    executable = tmp_path / "hook"
    executable.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    executable.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "reference-transaction").symlink_to(executable)
    (hooks / "pre-commit").symlink_to(executable)
    git(repository, "config", "core.hooksPath", str(hooks))
    git(repository, "config", "filter.trap.clean", str(executable))
    git(repository, "config", "filter.trap.smudge", str(executable))
    git(repository, "config", "core.fsmonitor", str(executable))
    (repository / ".gitattributes").write_text("* filter=trap text eol=lf\n")
    (repository / "changed").write_bytes(b"exact\r\n")

    snapshots.export(repository, tmp_path / "snapshot")

    assert not marker.exists()
    assert git(tmp_path / "snapshot", "show", "HEAD:changed") == b"exact\r\n"
    assert "filter.trap" not in (tmp_path / "snapshot" / "config").read_text()


def test_snapshot_rejects_symlink_parent_without_reading_external_contents(repository: Path, tmp_path: Path) -> None:
    (repository / "folder").mkdir()
    (repository / "folder" / "tracked").write_text("original")
    git(repository, "add", "folder")
    (repository / "folder" / "tracked").unlink()
    (repository / "folder").rmdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "tracked").write_text("secret")
    (repository / "folder").symlink_to(private, target_is_directory=True)
    destination = tmp_path / "snapshot"

    with pytest.raises(DigitalTwinUniverseError, match="safely read"):
        snapshots.export(repository, destination)

    assert not destination.exists()
    assert (private / "tracked").read_text() == "secret"


def test_source_changes_abort_without_retry_or_leaving_snapshot(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = snapshots._tree_entries
    calls = 0

    def changing(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            (repository / "changed").write_text("peer edit")
        return result

    monkeypatch.setattr(snapshots, "_tree_entries", changing)

    with pytest.raises(DigitalTwinUniverseError, match="changed during export"):
        snapshots.export(repository, tmp_path / "snapshot")

    assert calls == 2
    assert (repository / "changed").read_text() == "peer edit"
    assert not (tmp_path / "snapshot").exists()


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("change", ["sibling-commit", "new-tag", "deleted-tag"])
def test_unrelated_refs_only_invalidate_exports_that_include_history(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: bool,
    change: str,
) -> None:
    sibling = tmp_path / "sibling"
    git(repository, "worktree", "add", "-qb", "sibling", str(sibling))
    selected_head = git(repository, "rev-parse", "HEAD").decode().strip()
    selected_index = git(repository, "ls-files", "--stage", "-z")
    original = snapshots._tree_entries
    changed = False

    def concurrent_ref_change(
        command: snapshots.Git, source: Path, index: bytes, destination: Path | None, object_format: str
    ) -> dict[str, tuple[str, str]]:
        nonlocal changed
        entries = original(command, source, index, destination, object_format)
        if not changed:
            changed = True
            if change == "sibling-commit":
                (sibling / "changed").write_text("independent worktree edit")
                git(sibling, "commit", "-qam", "independent change")
            elif change == "new-tag":
                git(sibling, "tag", "unrelated-new-tag")
            else:
                git(sibling, "tag", "-d", "v1")
        return entries

    monkeypatch.setattr(snapshots, "_tree_entries", concurrent_ref_change)
    destination = tmp_path / ("snapshot" if history else "source.tar")
    if history:
        with pytest.raises(DigitalTwinUniverseError) as raised:
            snapshots.export(repository, destination)
        assert raised.value.code == "repository-source-changed"
        assert not destination.exists()
    else:
        result = lib.export_repository_snapshot(repository, destination)
        assert result.source_head == selected_head
        assert result.source_branch == "main"
        with tarfile.open(destination) as archive:
            content = archive.extractfile("changed")
            assert content is not None
            assert content.read() == b"original"
            assert ".git" not in archive.getnames()
    assert changed
    assert git(repository, "rev-parse", "HEAD").decode().strip() == selected_head
    assert git(repository, "ls-files", "--stage", "-z") == selected_index
    assert not list(tmp_path.glob("dtu-snapshot-*"))


@pytest.mark.parametrize("change", ["head", "branch", "index", "tracked", "untracked"])
def test_archive_still_rejects_selected_identity_or_content_changes(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    original = snapshots._tree_entries
    changed = False

    def concurrent_source_change(
        command: snapshots.Git, source: Path, index: bytes, destination: Path | None, object_format: str
    ) -> dict[str, tuple[str, str]]:
        nonlocal changed
        entries = original(command, source, index, destination, object_format)
        if not changed:
            changed = True
            if change == "head":
                git(source, "commit", "--allow-empty", "-qm", "selected head changed")
            elif change == "branch":
                git(source, "branch", "-m", "renamed")
            elif change == "index":
                git(source, "update-index", "--chmod=+x", "changed")
            else:
                (source / ("changed" if change == "tracked" else "new-file")).write_text("selected edit")
        return entries

    monkeypatch.setattr(snapshots, "_tree_entries", concurrent_source_change)
    destination = tmp_path / "source.tar"
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_repository_snapshot(repository, destination)
    assert raised.value.code == "repository-source-changed"
    assert changed
    assert not destination.exists()
    assert not list(tmp_path.glob("dtu-snapshot-*"))


def test_snapshot_file_bound_is_explicit(repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshots, "MAX_FILE_BYTES", 2)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        snapshots.export(repository, tmp_path / "snapshot")
    assert raised.value.code == "repository-snapshot-limit"
    assert not (tmp_path / "snapshot").exists()


def test_public_archive_has_exact_source_and_provenance_without_attribute_transforms(
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repository / ".gitattributes").write_text("changed export-ignore\nnew export-subst\n")
    (repository / "new").write_text("$Format:%H$\n")
    (repository / "outside").symlink_to("/outside/never-read")
    (repository / "executable").write_text("executable")
    (repository / "executable").chmod(0o755)
    head = git(repository, "rev-parse", "HEAD").decode().strip()
    git(repository, "update-index", "--add", "--cacheinfo", f"160000,{head},submodule")
    (repository / "submodule").mkdir()
    (repository / "submodule" / "private").write_text("not selected")
    monkeypatch.setattr(lib, "check", lambda: pytest.fail("snapshot must not access Docker"))
    destination = tmp_path / "source.tar"

    result = lib.export_repository_snapshot(repository, destination)

    assert result.archive_bytes == destination.stat().st_size
    assert result.source_head == head
    assert destination.stat().st_mode & 0o777 == 0o600
    with tarfile.open(destination) as archive:
        changed = archive.extractfile("changed")
        assert changed is not None
        assert changed.read() == b"original"
        new = archive.extractfile("new")
        assert new is not None
        assert new.read() == b"$Format:%H$\n"
        assert archive.getmember("outside").linkname == "/outside/never-read"
        assert archive.getmember("executable").mode == 0o755
        assert archive.getmember("submodule").isdir()
        assert "submodule/private" not in archive.getnames()
        assert ".git" not in archive.getnames()
    assert not list(tmp_path.glob("dtu-snapshot-*"))


def test_public_archive_never_overwrites_existing_destination(repository: Path, tmp_path: Path) -> None:
    destination = tmp_path / "source.tar"
    destination.write_text("prior retained output")
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_repository_snapshot(repository, destination)
    assert raised.value.code == "snapshot-destination-exists"
    assert destination.read_text() == "prior retained output"


def test_shallow_snapshot_is_self_contained(repository: Path, tmp_path: Path) -> None:
    source = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", repository.as_uri(), str(source))
    (source / "changed").write_text("shallow dirty")
    result = snapshots.export(source, tmp_path / "snapshot")
    assert result["shallow_source"] is True
    assert git(tmp_path / "snapshot", "show", "HEAD:changed") == b"shallow dirty"
    assert git(tmp_path / "snapshot", "rev-list", "--count", "HEAD").strip() == b"1"
    git(tmp_path / "snapshot", "fsck", "--full")


def test_detached_snapshot_retains_source_identity(repository: Path, tmp_path: Path) -> None:
    git(repository, "checkout", "--detach", "-q")
    result = snapshots.export(repository, tmp_path / "snapshot")
    assert result["source_branch"] == "HEAD"
    assert git(tmp_path / "snapshot", "symbolic-ref", "--short", "HEAD").strip() == b"detached"


def test_unsupported_snapshot_reports_specific_platform_remedy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(DigitalTwinUniverseError) as raised:
        snapshots.export(tmp_path / "source", tmp_path / "snapshot")
    assert raised.value.code == "repository-snapshot-unsupported"
    assert "WSL" in raised.value.remedy
    assert not (tmp_path / "snapshot").exists()


def test_snapshot_rejects_output_inside_source_without_creating_it(repository: Path) -> None:
    with pytest.raises(DigitalTwinUniverseError) as raised:
        lib.export_repository_snapshot(repository, repository / "snapshot.tar")
    assert raised.value.code == "snapshot-destination-inside-source"
    assert not list(repository.glob("dtu-snapshot-*"))
    assert not (repository / "snapshot.tar").exists()


def test_repository_service_mounts_owned_linked_snapshot(repository: Path, tmp_path: Path) -> None:
    from digital_twin_universe.capabilities.universe import overlay
    from digital_twin_universe.capabilities.universe.profile import Repository

    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-qb", "feature", str(linked))
    (linked / "changed").write_text("current linked files")
    assets = tmp_path / "owned"

    service = overlay._git_service(assets, tmp_path / "profile.yaml", [Repository(path=linked)])

    snapshot = assets / "repositories" / "linked"
    assert f"{snapshot}:/repos/linked:ro" in service["volumes"]
    assert not any(volume.startswith(f"{linked}:") for volume in service["volumes"])
    assert git(snapshot, "show", "HEAD:changed") == b"current linked files"
    assert (linked / ".git").is_file()


def test_repository_service_rejects_colliding_names(tmp_path: Path) -> None:
    from digital_twin_universe.capabilities.universe import overlay
    from digital_twin_universe.capabilities.universe.profile import Repository

    with pytest.raises(DigitalTwinUniverseError) as raised:
        overlay._served(
            tmp_path / "profile.yaml",
            [Repository(path=tmp_path / "one" / "repo"), Repository(path=tmp_path / "two" / "repo")],
        )
    assert raised.value.code == "repository-name-collision"
