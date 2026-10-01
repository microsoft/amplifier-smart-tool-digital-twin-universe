`manifest` prints the tool's manifest, the `SMART_TOOL.md` shipped inside the package, as
JSON. It needs no Docker, no agent provider, and no credentials, so it is the quickest way to
confirm an install works, see which version is installed, and read what the tool requires
before calling anything else.

```bash
digital-twin-universe manifest
```

```python
from digital_twin_universe.lib import load_manifest

manifest = load_manifest()
manifest.version, [(needed.name, needed.optional) for needed in manifest.requires]
```

## Arguments

None.

## Result

`Manifest` JSON on stdout:

- `smart_tool_format`: the version of the Smart Tool format the manifest follows.
- `name` and `version`: `digital-twin-universe` and the installed version.
- `description` and `use_cases`: what the tool is for, in its own words.
- `platforms`: the operating systems it runs on, from `linux`, `macos`, and `windows`.
- `requires`: each prerequisite with `name`, `purpose`, `install` (a documentation URL, never a
  command), and `optional`. `docker` is the one that is not optional; the others are what the
  model-backed commands' agent providers need.
- `body`: the Markdown below the frontmatter, which `digital-twin-universe --help` prints with
  the capability list after it.

## Failures

Exits 0. Any argument is a bad invocation and exits 2. It reads one file inside the package and
raises no `DigitalTwinUniverseError`; an install too damaged to read it fails with Python's own
error and exits 1. Reinstall the tool when that happens.
