`exec` runs a command in the twin, the profile's twin machine, as the twin's own user, the way
a person at that machine's terminal would. With `--command` it runs one command and prints what
it produced. Without `--command` it attaches an interactive shell to this terminal, for a
person.

Use it to install, run, and check the software under test inside a universe that `launch`
printed the id of. An agent wants `--command`: the interactive shell needs a terminal and fails
without one. To move files, use `file-push` and `file-pull` rather than `cat` through `exec`.

```bash
digital-twin-universe exec --id dtu-hello-1a2b --command 'echo dtu-ok'

# exits with the command's own code, so it composes with && and ||
digital-twin-universe exec --id dtu-my-app-1a2b --command 'curl -sf localhost:8000/health' \
  && echo healthy

# as root, somewhere else, with more time
digital-twin-universe exec --id dtu-hello-1a2b \
  --command 'apk add --no-cache curl' \
  --user root \
  --workdir /tmp \
  --timeout-seconds 900

# a person's interactive shell
digital-twin-universe exec --id dtu-hello-1a2b
```

```python
from digital_twin_universe.lib import execute, shell

result = execute("dtu-hello-1a2b", "echo dtu-ok", timeout_seconds=60)
result.exit_code, result.stdout, result.stderr

code = shell("dtu-hello-1a2b")  # needs a terminal on stdin and stdout
```

## How the command runs

`--command` is passed to `sh -lc` in the twin through `docker compose exec`, with no TTY and
stdin empty. `sh` is a login shell, so `PATH` changes an installer made in `~/.profile` apply,
but it is `sh`, not bash: wrap bash syntax as `bash -lc '...'` when the image has bash. Quote
the whole command once for the host shell.

The interactive shell is `bash -l` when the twin has bash and `sh -l` otherwise.

Without `--user` the command runs as the twin's own user: the service's `user:` in the profile,
else the image's `USER`. Without `--workdir` it runs in the service's `working_dir:`, else the
image's `WORKDIR`, else `/`.

## Arguments

- `--id ID`: the universe id `launch` printed, such as `dtu-hello-1a2b`. Required. A container
  or image name that starts with an id is refused with `universe-not-found`, and the remedy
  names the id.
- `--command TEXT`: the shell command to run. Omit it for the interactive shell.
- `--user USER`: run as this user, a name or uid in the twin, instead of the twin's own.
- `--workdir PATH`: run in this directory in the twin instead of the twin's own.
- `--timeout-seconds N`: how long `--command` may run before the tool gives up on it. Defaults
  to 300. Ignored for the interactive shell. 0 or less gives up at once. On timeout the command
  is abandoned, not killed: whatever it started keeps running in the twin. For anything long,
  start it in the background (`nohup ... &`) and poll it with further `exec` calls.
- Library: `execute(id, command, user=None, workdir=None, timeout_seconds=300)` and `shell(id,
  user=None, workdir=None)`, which returns the shell's exit code.

## Result

With `--command`, `ExecResult` JSON on stdout, whatever the exit code, and nothing streams
while the command runs:

- `exit_code`: the command's exit code.
- `stdout`: everything the command wrote to stdout.
- `stderr`: everything the command wrote to stderr.

The CLI exits with `exit_code`. A non-zero code is a result, not a failure: `execute` returns
it and raises nothing. When Docker cannot start the command, its own message is the result too:
an unknown `--user` gives `exit_code` 1 with the reason in `stderr`, an absent `--workdir`
gives 127 with the reason in `stdout`.

Without `--command`, nothing is printed; the shell is attached to this terminal and the CLI
exits with the shell's exit code.

## Failures

A failure prints `message` and `remedy` to stderr, prints no JSON, and exits 1. A non-zero exit
with no JSON on stdout is therefore the tool failing, not the command. Exits 2 on a bad
invocation, such as a missing `--id`.

The library raises `DigitalTwinUniverseError` with `code`:

- `universe-not-found`: no universe with that id was launched from this machine. The remedy
  lists the ids that were.
- `twin-not-running`: the twin's container is absent, exited, or otherwise not running. The
  message names its state; the remedy names `docker compose -p <id> ps`.
- `docker-unavailable`: Docker is missing or its daemon is down; run `digital-twin-universe
  check`.
- `timeout`: `--command` was still running after `--timeout-seconds`.
- `no-tty`: no `--command`, and stdin or stdout is not a terminal.
