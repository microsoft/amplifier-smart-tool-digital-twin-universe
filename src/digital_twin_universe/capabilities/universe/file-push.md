`file-push` copies a file or directory from the host into the twin, the profile's twin machine,
and makes the copy owned by the twin's own user, so the software under test can read and change
it the way a user's own files can be.

Use it to hand the twin input that has no URL to fetch from: a config file, a fixture, a script
to run with `exec`. Do not use it to stand in for the clone and install a real user would do; a
profile's `x-dtu.repositories` serves local repositories over `https://` for that.

```bash
# /home/user exists, so the directory lands at /home/user/src
digital-twin-universe file-push --id dtu-my-app-1a2b --source ./src --destination /home/user

# /home/user/app does not exist, so the contents of ./src land there
digital-twin-universe file-push --id dtu-my-app-1a2b \
  --source ./src \
  --destination /home/user/app

# a file under a new name
digital-twin-universe file-push --id dtu-my-app-1a2b \
  --source ./fixtures/config.toml \
  --destination /home/user/.config/my-app/config.toml
```

```python
from pathlib import Path

from digital_twin_universe.lib import push_files

pushed = push_files("dtu-my-app-1a2b", Path("./src"), "/home/user")
pushed.destination, pushed.files
```

## Where the copy lands

The rules are `docker cp`'s:

- When `--destination` is an existing directory in the twin, the source goes inside it under
  its own name.
- Otherwise the source lands at `--destination` itself: a file under that name, a directory's
  contents in a new directory of that name.
- The destination's parent directory must exist; nothing is created above it. Create it first
  with `exec --command 'mkdir -p ...'`.
- A relative `--destination` is resolved against `/`, not the twin's working directory. Pass an
  absolute path.
- A trailing `/.` on `--source` is dropped, so it does not mean "the contents". To push only a
  directory's contents, pass a `--destination` that does not exist yet.

The copy is owned by the numeric uid and gid the twin runs as by default. A private upload
archive supplies that ownership to Docker, including when the twin drops all capabilities.
No host permissions are changed and no `chown` runs inside the twin. File modes and symlink
text are preserved; symlink targets are never read on the host. Pushing into an existing
directory merges with what is there and overwrites files of the same name.

Uploads support Linux, WSL, and macOS no-follow file reads. Native Windows uploads fail with
`transfer-unsupported`; use WSL or a supported host. This restriction applies to uploads,
not every library capability.

Preparation and upload share a 120-second deadline. Inputs are limited to 100,000 entries,
128 directory levels, 128 MiB per file, 1 GiB total file content, and a 2 GiB archive. Docker
diagnostics are limited to 16 MiB per stream. Use smaller batches for larger inputs. A detected
source change fails before upload; pause the writer and retry. A Docker failure can leave a
partial copy in the twin, so inspect that destination before retrying.

## Arguments

- `--docker-host URI`: the original Docker endpoint. Required for legacy records without a pinned endpoint.
  Library callers pass `config=BackendConfig(docker_host=...)`. This selects the endpoint for this call only;
  the record is not adopted or changed. An explicit endpoint that differs from a pinned record is refused.

- `--id ID`: the universe id `launch` printed. Required.
- `--source PATH`: a file or directory on the host, relative to the working directory or
  absolute. Required.
- `--destination PATH`: a path in the twin, read with the rules above. Required.
- Library: `push_files(id, source, destination)`, with `source` a `Path` and `destination` a
  `str`.

## Result

`Transfer` JSON on stdout:

- `source`: the host path, as given.
- `destination`: the absolute path used in the twin, resolving a destination symlink and adding
  `<source name>` when the destination was an existing directory.
- `files`: how many regular files and symlinks were copied. Each counts one; directories do not.

## Failures

`docker-endpoint-required` means a legacy record has no saved origin: identify the original endpoint and pass
`--docker-host`. Ambient Docker context and environment variables are not enough. `docker-endpoint-mismatch`
means an explicit endpoint differs from the record's pinned origin.

A failure prints `message` and `remedy` to stderr and exits 1. Exits 2 on a bad invocation,
such as a missing option.

The library raises `DigitalTwinUniverseError` with `code`:

- `universe-not-found`: no universe with that id was launched from this machine. The remedy
  lists the ids that were.
- `twin-not-running`: the twin's container is absent, exited, or otherwise not running.
- `source-not-found`: `--source` does not exist on the host.
- `transfer-failed`: `docker cp` refused, most often because the destination's parent does not
  exist or a directory would land on an existing file.
- `transfer-unsupported`: the host lacks the no-follow reads required for secure uploads.
- `transfer-limit` or `transfer-timeout`: a preparation, archive, diagnostic, or time limit was reached.
- `transfer-source-changed`: a selected path changed or could not be read safely during preparation.
- `transfer-file-unsupported`: a selected entry is a device, socket, or other unsupported file type.
- `transfer-owner-unknown`: the twin's numeric user and group could not be read.
- `transfer-path-invalid`: the destination was empty, invalid, or an unresolvable symlink.
- `docker-unavailable`: Docker is missing or its daemon is down; run `digital-twin-universe
  check`.
