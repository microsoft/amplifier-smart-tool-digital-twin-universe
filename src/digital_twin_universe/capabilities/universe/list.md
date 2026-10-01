`list` reports every universe launched from this machine and not yet destroyed, oldest first,
each measured against Docker now. It changes nothing.

Use it to find an id you lost, to reuse a running universe instead of launching another, and
after any failure to see what is still up. It lists universes launched by anyone on this user
account, including other agents' work; destroy only the ones you launched.

```bash
digital-twin-universe list

# ids of the universes still running
digital-twin-universe list | jq -r '.[] | select(.state == "running") | .id'
```

```python
from digital_twin_universe.lib import list_universes

[(universe.id, universe.state, universe.urls) for universe in list_universes()]
```

A universe is listed for as long as its record exists under
`~/.digital-twin-universe/universes/<id>/`, which `destroy` removes. One whose containers were
removed some other way is still listed, as `stopped` with no services, until `destroy` clears
the record.

## Arguments

None.

## Result

A JSON array on stdout, one `Universe` per universe in the shape `launch` prints: `id`, `name`,
`description`, `profile_path`, `twin_machine`, `state`, `services`, `urls`, `state_path`, and
`created_at`. `state` is `running`, `starting`, `degraded`, or `stopped`;
`digital-twin-universe launch --help` describes every field. A machine with no universes prints
`[]` without asking Docker.

## Failures

When there is at least one universe to measure and Docker is not usable, it prints `message`
and `remedy` to stderr and exits 1 with `DigitalTwinUniverseError` code `docker-unavailable`;
run `digital-twin-universe check`.
