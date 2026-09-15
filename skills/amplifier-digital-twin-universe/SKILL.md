---
name: amplifier-digital-twin-universe
description: >-
  Stands up isolated, realistic environments from a declarative profile so software
  can be tested as though actually deployed, and manages them for their whole life:
  author a profile, launch it, run commands inside, copy files in and out, update it,
  and tear it down. Use when "tests pass locally" is not enough evidence and you need
  to exercise code the way a real deployment would.
license: MIT
metadata:
  author: DavidKoleczek
  version: "0.2.0"
  repository: https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
---

# amplifier-digital-twin-universe

Install as a CLI:

```bash
uv tool install git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe
```

Install as a library:

```bash
uv add "amplifier-digital-twin-universe @ git+https://github.com/microsoft/amplifier-smart-tool-digital-twin-universe"
```

Then run the tool's own skill and follow it:

```bash
amplifier-digital-twin-universe --help
```
