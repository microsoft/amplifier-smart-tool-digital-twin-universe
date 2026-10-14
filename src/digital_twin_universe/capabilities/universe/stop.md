Stop work in an owned universe without removing its containers, volumes, or management state.

```bash
digital-twin-universe stop --id <universe-id> --timeout-seconds 10
```

## Arguments

- `--id`: exact universe id returned by launch.
- `--timeout-seconds`: shutdown grace period, from 1 to 300 seconds; default 10.

## Result

`Stopped` JSON reports `stopped`, `incomplete`, or `unknown`, with containers still running and any errors.
Exit 0 confirms no owned container is running; exit 1 means cessation was not confirmed.
Stop does not resume or replay commands. Use `destroy` only when the retained environment and its data should be removed.

## Failures

An active launch or cleanup returns `operation-busy`; inspect it before retrying.
`universe-not-found` means the ownership record is missing. `docker-endpoint-mismatch` rejects retargeting an existing environment.
Daemon errors or failed post-stop inspection retain the record and report incomplete or unknown cessation.
