# Export files

Save files from the exact owned twin as a private tar archive, including after Stop. This is deterministic and needs no model provider.

```bash
digital-twin-universe export-files --id <universe-id> --source /workspace --destination ./workspace.tar
```

## Arguments

`--id` is the owned universe id, `--source` is an absolute container path, and `--destination` is a new archive path whose directory exists.
The default limit is 256 MiB and 60 seconds; use `--max-bytes` up to 16 GiB and `--timeout-seconds` up to 3600.

## Result

The result gives the archive path, byte count and SHA-256. Existing destinations are never overwritten. The archive is not extracted on the host; treat its contents as untrusted when opening it elsewhere.
Export source and artifacts before destroying an environment whose files must be retained. This operation does not remove containers or volumes.

## Failures

Failure leaves the environment intact and removes only this call's partial archive. An active lifecycle operation returns `operation-busy`. Inspect the named cause before exporting to a new destination.
