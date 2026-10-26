## Cleanup

The tool launched your draft itself, ran your checks, and they passed. Before the draft is kept under its final
name, clean up after the work, then call `submit` once with a Cleanup.

1. Destroy every universe you launched that is still up. The tool sees these launched from the draft, none of
   them its own:
{universes}
   Run `{digital_twin_universe} destroy --id <id>` for each and list the ids in `destroyed`. A universe left running is an
   orphan nobody will come back for.
2. Remove anything you created on this machine outside `{draft}`: scratch clones, captured logs, probes in `/tmp`.
   List the paths in `removed`.
3. Take out of the profile anything that served your iteration rather than the universe: a debugging package, a
   verification command that crept into `command` or the Dockerfile, a commented-out attempt, a stale comment.
   The cleaned profile must do exactly what the verified one did. Do not add a check, a note, a feature, or a
   comment explaining the cleanup. If you edit `compose.yaml`, the `Dockerfile`, or any file the build copies,
   set `profile_changed: true`; the tool then validates and launches it again before keeping it. If you change
   nothing, set it to `false` and touch no file.
4. `notes`: one line per thing you removed from the profile, or empty.
