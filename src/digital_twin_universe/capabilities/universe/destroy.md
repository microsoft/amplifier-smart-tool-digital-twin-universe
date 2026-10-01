`destroy` takes a universe down and forgets it: every container, network, and volume of its
Compose project, then its state directory under `~/.digital-twin-universe/universes/<id>/`.
Built images stay, so the next `launch` of the same profile is fast.

Use it when you are done with a universe you launched, and after a failed `launch`, which
leaves what started running for inspection; its remedy names this command. It works on a
universe in any state, running, degraded, or stopped. Everything inside the twin is lost, so
`file-pull` what you need first.

Destroy only what you launched. `list` shows every universe launched from this machine,
including ones another agent or person is using.

```bash
digital-twin-universe destroy --id dtu-hello-1a2b
```

```python
from digital_twin_universe.lib import destroy

destroyed = destroy("dtu-hello-1a2b")
destroyed.id, destroyed.removed
```

Containers get one second after SIGTERM before they are killed, so a destroy takes seconds
whatever runs in the twin.

## Arguments

- `--id ID`: the universe id `launch` printed. Required. A container or image name that starts
  with an id is refused with `universe-not-found`, and the remedy names the id.
- Library: `destroy(id)`.

## Result

`Destroyed` JSON on stdout:

- `id`: the universe destroyed.
- `removed`: the names of the containers, networks, and volumes taken down, such as
  `dtu-hello-1a2b-twin-1` and `dtu-hello-1a2b_default`. Empty when Docker had nothing left of
  the universe; its state directory is removed all the same.

## Failures

A failure prints `message` and `remedy` to stderr and exits 1. Exits 2 on a bad invocation,
such as a missing `--id`.

The library raises `DigitalTwinUniverseError` with `code`:

- `universe-not-found`: no universe with that id was launched from this machine, including one
  already destroyed. The remedy lists the ids that were.
- `docker-unavailable`: Docker is missing or its daemon is down; run `digital-twin-universe
  check`. Nothing is removed, and the state directory stays so the destroy can be retried.
