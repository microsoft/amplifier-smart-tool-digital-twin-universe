# Installing Incus

Incus runs the isolated environments. It is a hard prerequisite: without it, no
environment can be launched.

Follow the upstream installation guide for your distribution:
https://linuxcontainers.org/incus/docs/main/installing/

After installing, initialize it once and confirm your user is in the `incus-admin`
group:

- `incus admin init --minimal`
- `incus list`

If `incus list` prints a table (empty is fine), the prerequisite is satisfied.
