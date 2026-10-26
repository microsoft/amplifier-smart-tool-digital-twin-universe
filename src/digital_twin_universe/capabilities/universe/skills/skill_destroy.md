`destroy` removes the universe's observed owned containers, networks, and volumes, then its state directory
only when cleanup is confirmed. External and unrelated resources remain, as do built images. A declared
resource name alone does not authorize deletion.

Image-declared anonymous volumes require a retained exact container mount receipt, unchanged volume identity,
and no remaining container references. Explicit or inherited sources do not establish anonymous ownership.
The receipt is saved before containers are removed; failed ownership checks retain the universe for inspection
and retry. Externally owned data remains.

Use it when you are done with a universe you launched, and after a failed `launch`, which
leaves what started running for inspection; its remedy names this command. It works on a
universe in any state, running, degraded, or stopped. Use `stop` to keep the environment and its data.
Before destroy, use `export-files` to retain each required path, including from a stopped twin. One exported
path does not back up another service's data or every volume.

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

- `--docker-host URI`: the original Docker endpoint. Required for legacy records without a pinned endpoint.
  Library callers pass `config=BackendConfig(docker_host=...)`. This selects the endpoint for this call only;
  the record is not adopted or changed. An explicit endpoint that differs from a pinned record is refused.

- `--id ID`: the universe id `launch` printed. Required. A container or image name that starts
  with an id is refused with `universe-not-found`, and the remedy names the id.
- Library: `destroy(id, config=None)`.

## Result

`Destroyed` JSON on stdout:

- `id`: the owned universe.
- `removed`: previously observed owned resource names confirmed absent after cleanup.
- `outcome`: `removed`, `residue`, or `unknown`.
- `remaining`: observed containers, networks, and volumes; `null` for a failed inventory.
- `errors`: inspection, removal, or state-cleanup errors.
- `state_removed`: whether the management state directory is gone.

Exit 0 requires complete observed cleanup; residue or uncertainty exits 1 and retains recovery information.
The library's `complete` property checks all observations; `require_removed()` raises `cleanup-incomplete`
otherwise. A completed cleanup receipt is retained outside the removed directory, so repeating destroy can
return the same result without repeating Docker effects.
