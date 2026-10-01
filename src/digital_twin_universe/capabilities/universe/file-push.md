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

The whole copy, file or tree, is then owned by the uid and gid the twin runs as by default.
When that user is root, the copy stays owned by root, as `docker cp` leaves it. Pushing into an
existing directory merges with what is there and overwrites files of the same name.

## Arguments

- `--id ID`: the universe id `launch` printed. Required.
- `--source PATH`: a file or directory on the host, relative to the working directory or
  absolute. Required.
- `--destination PATH`: a path in the twin, read with the rules above. Required.
- Library: `push_files(id, source, destination)`, with `source` a `Path` and `destination` a
  `str`.

## Result

`Transfer` JSON on stdout:

- `source`: the host path, as given.
- `destination`: where the copy landed in the twin, `<destination>/<source name>` when the
  destination was an existing directory, else the destination as given.
- `files`: how many files were copied, counted on the host; a file counts one, a directory
  counts the files anywhere under it.

## Failures

A failure prints `message` and `remedy` to stderr and exits 1. Exits 2 on a bad invocation,
such as a missing option.

The library raises `DigitalTwinUniverseError` with `code`:

- `universe-not-found`: no universe with that id was launched from this machine. The remedy
  lists the ids that were.
- `twin-not-running`: the twin's container is absent, exited, or otherwise not running.
- `source-not-found`: `--source` does not exist on the host.
- `transfer-failed`: `docker cp` refused, most often because the destination's parent does not
  exist or a directory would land on an existing file. The message carries Docker's reason.
- `docker-unavailable`: Docker is missing or its daemon is down; run `digital-twin-universe
  check`.
