`file-pull` copies a file or directory out of the twin, the profile's twin machine, onto the
host.

Use it to bring back what the software under test produced: logs, reports, generated files, a
config it wrote. For a short text result, `exec --command 'cat <path>'` already returns it in
the JSON; pull when you want the file itself on the host.

```bash
# ./out exists, so the file lands at ./out/run.log
digital-twin-universe file-pull --id dtu-my-app-1a2b \
  --source /home/user/run.log \
  --destination ./out

# ./report does not exist, so the contents of the twin's directory land there
digital-twin-universe file-pull --id dtu-my-app-1a2b \
  --source /home/user/app/report \
  --destination ./report
```

```python
from pathlib import Path

from digital_twin_universe.lib import pull_files

pulled = pull_files("dtu-my-app-1a2b", "/home/user/run.log", Path("./out"))
pulled.destination, pulled.files
```

## Where the copy lands

The rules are `docker cp`'s:

- When `--destination` is an existing directory on the host, the source goes inside it under
  its own name.
- Otherwise the source lands at `--destination` itself: a file under that name, a directory's
  contents in a new directory of that name.
- The destination's parent directory must exist; nothing is created above it.
- A relative `--source` is resolved against `/` in the twin, not its working directory. Pass an
  absolute path.
- Do not end `--source` with `/.`. Docker copies the directory's contents, but the result's
  `destination` and `files` then describe `<destination>/<name>` instead.

The source is checked for as the twin's own user, so a path that user cannot reach, such as a
file in a directory only root can enter, is reported as `source-not-found`. Pulled files belong
to the host user running the command and keep their mode bits. Pulling into an existing
directory merges with what is there and overwrites files of the same name.

## Arguments

- `--id ID`: the universe id `launch` printed. Required.
- `--source PATH`: a file or directory in the twin, read with the rules above. Required.
- `--destination PATH`: a path on the host, relative to the working directory or absolute, read
  with the rules above. Required.
- Library: `pull_files(id, source, destination)`, with `source` a `str` and `destination` a
  `Path`.

## Result

`Transfer` JSON on stdout:

- `source`: the twin path, as given.
- `destination`: where the copy landed on the host, `<destination>/<source name>` when the
  destination was an existing directory, else the destination as given.
- `files`: how many files are at `destination` afterwards, counted on the host; a file counts
  one, a directory counts the files anywhere under it, including any that were there before.

## Failures

A failure prints `message` and `remedy` to stderr and exits 1. Exits 2 on a bad invocation,
such as a missing option.

The library raises `DigitalTwinUniverseError` with `code`:

- `universe-not-found`: no universe with that id was launched from this machine. The remedy
  lists the ids that were.
- `twin-not-running`: the twin's container is absent, exited, or otherwise not running.
- `source-not-found`: `--source` does not exist in the twin, or the twin's user cannot reach
  it. The remedy names the `exec` command that lists it.
- `transfer-failed`: `docker cp` refused, most often because the destination's parent does not
  exist or a directory would land on an existing file. The message carries Docker's reason.
- `docker-unavailable`: Docker is missing or its daemon is down; run `digital-twin-universe
  check`.
