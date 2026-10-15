`export-repository-snapshot` exports an approved repository or linked worktree into a new private tar archive.
It is deterministic, needs Git on PATH, and uses no Docker or model provider.

```bash
digital-twin-universe export-repository-snapshot --source ./project --destination ./retained/source.tar
```

```python
from pathlib import Path
from digital_twin_universe.lib import export_repository_snapshot

snapshot = export_repository_snapshot(Path("./project"), Path("./retained/source.tar"))
```

## Arguments

- `--source PATH`: the approved repository or linked worktree root. Required.
- `--destination PATH`: a new tar archive in an existing directory outside the source checkout. Required.
- Library: `export_repository_snapshot(source, destination)` takes two `Path` values.

The destination directory must already exist; an existing destination is never replaced. The archive has mode
0600. It contains current tracked files and nonignored untracked files, with deletions and executable modes
preserved. Symlinks keep their link text; their targets are never read. Submodules remain empty directories;
they are never fetched or traversed. Ignored untracked files and source Git metadata are excluded. Tracked
credentials, if any, remain selected source: this is not a secret scrubber. Approve the source before export.

Git hooks, clean/smudge filters, and archive export-ignore/export-subst attributes are not executed or applied.
The source index and checkout are untouched. Detected file, index, HEAD, or ref changes fail the export, without
an automatic retry; this is a bounded observation, not a lock on other writers. Consumers must use safe archive
extraction and must not dereference symlinks outside their destination.

## Result

`RepositorySnapshot` JSON includes `path`, `source_head`, `source_branch`, `snapshot_commit`, `snapshot_tree`,
`files`, `shallow_source`, `archive_bytes`, and `archive_sha256`. The archive contains source bytes, not Git history.

Bounds: 100,000 selected files, 128 MiB per regular file, 1 GiB selected contents, 32 MiB Git listings, and 120
seconds per Git operation. Repository publishing additionally limits history export to 2 GiB. If exceeded,
prepare a smaller explicit checkout. Safe snapshots currently require Linux/macOS no-follow directory reads;
other DTU capabilities remain available on Windows.

## Failures

Failures raise `DigitalTwinUniverseError` (CLI exit 1): `git-missing`, `repository-snapshot-unsupported`,
`repository-not-root`, `repository-unmerged`, `repository-path-invalid`, `repository-file-unsupported`,
`repository-source-changed`, `repository-snapshot-limit`, `repository-snapshot-failed`,
`snapshot-destination-exists`, `snapshot-destination-inside-source`, or `snapshot-destination-missing`.
No partial archive is promoted.
