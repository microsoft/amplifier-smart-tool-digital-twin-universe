# Troubleshooting a Digital Twin Universe

Entries are grouped by category, each as symptom, cause, remedy.

## Incus daemon unreachable or permissions

**Symptom:** `incus version` prints `Server version: unreachable`, or a
command fails with "You don't have the needed permissions to talk to the
incus daemon."
**Cause:** the invoking user is not in the `incus-admin` group, or is in the
group but the current process predates the group change.
**Remedy:**
```bash
sudo usermod -aG incus-admin $USER
newgrp incus-admin
```
`newgrp` only fixes the current shell. Any process spawned before the group
change (tmux session, background service, already-running Amplifier
session) keeps lacking the group until the user logs out and back in, or
reboots.

## `incus: command not found`

**Cause:** Incus is not installed.
**Remedy:** install from the upstream Zabbly packages for the host's
platform. Do not rely on the distro `apt install incus` package on Ubuntu
24.04, it pins a version with known bugs.

## `Error: not found` on `incus launch`

**Cause:** `sudo incus admin init --minimal` was never run after install.
**Remedy:** run it once, then retry the launch.

## Container has no network / gateway never resolves

**Symptom:** provisioning hangs or fails with `apt-get update` timeouts,
connection refused, or DNS failures inside the container.

**Diagnosis order:**
1. `incus exec <name> -- ip route show default`. No route: Incus
   networking never initialized, restart Incus: `sudo systemctl restart
   incus`.
2. `incus exec <name> -- ping -c1 -W2 <gateway-ip>`. Unreachable: the
   Incus bridge itself is down, restart Incus as above.
3. Gateway reachable but no internet: NAT/masquerade rules are missing.
   Common on WSL2 after `wsl --shutdown` or a host restart, where Incus's
   nftables rules are silently dropped.

**Fix for missing NAT/masquerade rules:**
```bash
sudo systemctl restart incus

# Add masquerade rules (nftables often fails silently on WSL2)
SUBNET=$(incus network get incusbr0 ipv4.address | cut -d/ -f1)
NETWORK="${SUBNET%.*}.0/24"
sudo iptables -t nat -A POSTROUTING -s $NETWORK ! -d $NETWORK -j MASQUERADE
sudo iptables -A FORWARD -i incusbr0 -j ACCEPT
sudo iptables -A FORWARD -o incusbr0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

**If Docker is also installed:** Docker sets iptables `FORWARD` policy to
`DROP`, blocking Incus bridge traffic independent of the NAT fix above.
Docker also creates its own `DOCKER-USER` chain, evaluated ahead of the
general `FORWARD` chain, so allowing `FORWARD` alone is not sufficient once
Docker is present:
```bash
sudo iptables -I DOCKER-USER -i incusbr0 -j ACCEPT
sudo iptables -I DOCKER-USER -o incusbr0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

echo '{"ip-forward-no-drop": true}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```
On WSL2, also restart WSL2 itself from PowerShell, restarting the Linux
services alone is not sufficient:
```powershell
wsl --shutdown
```
Then reopen the WSL terminal. On bare-metal Ubuntu, persist the
`DOCKER-USER` rules across reboots via `iptables-persistent` or
`/etc/rc.local`; they do not survive a reboot on their own.

## Provisioning command failed

**Cause A:** `command not found` mid-provisioning means the tool a step
depends on was not installed yet. Check `setup_cmds` ordering: install the
runtime or package manager before commands that use it.
**Cause B:** `apt-get update` fails with `Failed to fetch`, `Mirror sync in
progress?`, or a timeout referencing `archive.ubuntu.com`. This is an
upstream Ubuntu mirror outage, not a proxy bug.
**Remedy for B:** switch the profile's base image to a Debian image (e.g.
`images:debian/12`) and retry; Debian's mirrors are independent of the
Ubuntu archive. A base image switch may have other downstream effects on a
profile that assumes Ubuntu.
**Cause C:** a real networking failure. Work through the network section
above first, then retry provisioning.

## Readiness check never passes

**Cause:** usually the same root cause as "container has no network"
above, since the readiness check depends on the provisioned service being
reachable. Fix Incus/Docker networking first.
**Remedy:** inspect container state directly instead of guessing:
```bash
incus list
incus exec <name> -- <the readiness command itself>
```
Run the readiness command by hand inside the container to see the actual
failure rather than a bare timeout.

## Access port not reachable from the host

**Cause:** the profile's `access.ports` mapping was not applied, or the
service inside the container is not listening on the declared container
port.
**Remedy:**
1. `incus exec <name> -- ss -tlnp` to confirm the service is listening
   inside the container.
2. `incus config device list <name>` to confirm the Incus proxy device
   exists for the port. None means the launch-time port forwarding step
   did not run; relaunch or add it explicitly with `incus config device
   add`.
3. If the service listens only on `127.0.0.1` instead of `0.0.0.0`, the
   proxy device can forward the port but nothing answers from outside the
   container's own loopback. Fix the service's bind address.

## Mock sidecar cannot start

**Cause A:** Docker is not installed on the host. Mock service sidecars run
as Docker containers alongside the Incus environment; they are not
optional once a profile declares `mock_services`.
**Remedy:** install Docker.
**Cause B:** Docker is installed but the daemon is not running or not
reachable.
**Remedy:**
```bash
sudo systemctl start docker
docker version
```
Both a client and server version must print before retrying the launch.

## Docker inside Incus (nesting)

**Symptom:** `dockerd` fails to start inside the Incus container with
AppArmor permission-denied errors on `/proc/sys/` or `/sys/`.
**Cause:** Incus below 6.0.6 LTS / 6.19 combined with a newer runc has an
AppArmor bug that blocks Docker from starting inside an unprivileged Incus
container. Distro Ubuntu 24.04 still ships the broken 6.0.0. WSL2 hosts are
not affected.
**Remedy:** install Incus from the Zabbly repo instead of the distro
package; pin all three packages (`incus`, `incus-base`, `incus-client`)
explicitly, since Ubuntu ESM otherwise keeps the broken version at higher
apt priority.

**Symptom:** `dockerd` cannot create namespaces or cgroups inside the
container.
**Cause:** `security.nesting=true` is applied by default to every Digital
Twin Universe launch, so this should not normally trip. It only happens
when a profile explicitly sets `security.nesting: "false"` in
`base.config`.
**Remedy:** remove that override, or set nesting globally on the host's
default Incus profile: `incus profile set default security.nesting=true`.

## Profile validation errors

**Symptom:** `UnknownProfileFieldWarning` for a field in the profile YAML.
**Cause:** the field is not recognized by the current profile schema,
typically because the profile was written against an older or newer schema
version than the installed CLI.
**Remedy:** check the CLI version against the profile's origin, then drop
or rename the field per the current schema.

**Symptom:** `requires exactly one of ...` validation error.
**Cause:** a profile section that requires exactly one of a set of
mutually exclusive fields (e.g. specifying a base image) received zero or
more than one.
**Remedy:** supply exactly one of the required fields for that section.

**Symptom:** `Invalid match_mode` or `Invalid default_match_mode`.
**Cause:** a `url_rewrites` rule, or its block-level default, set
`match_mode` to something other than `prefix` or `boundary`.
**Remedy:** use `prefix` or `boundary` only. Prefer `boundary` when a
rule's path looks like a bare `org/repo` segment, since `prefix` mode on
that shape silently captures sibling repositories that share the prefix.

## Unresolved `${VAR}` references

**Symptom:** a `url_rewrites` rule's target still contains a literal
`${VAR}` string at runtime, or proxy setup for that rule is silently
skipped.
**Cause:** the variable was never supplied via `--var`. This is
intentional, not a bug: unresolved variables in a rewrite target cause
that rule's proxy setup to be skipped rather than fail launch.
**Remedy:** pass the missing variable with `--var`, or omit the rule from
the profile if it is not needed for this launch.

**Symptom:** a rewrite rule's target looks like a well-formed URL in the
YAML but the proxy returns a 502 at runtime.
**Cause:** the referenced variable substituted to an empty string,
collapsing the target into a bare path with no host, or the substituted
value has no `http` or `https` scheme.
**Remedy:** pass a real, non-empty value for the variable that includes a
scheme and host, or omit the `--var` flag entirely so the reference stays
unresolved and the proxy is skipped cleanly instead of misconfigured.

## Built-in profile names not resolving from an installed wheel

**Symptom:** launching by a built-in profile name (e.g.
`amplifier-user-sim`) fails to resolve when the package is installed as a
wheel, even though the same name works from a source checkout.
**Cause:** built-in name lookup searches `profiles/**/*.yaml` relative to
the package's own source tree. That directory structure is not guaranteed
to be present, or present at the expected path, once the package is
installed as a built wheel rather than run from a checkout.
**Remedy:** pass the profile as an explicit file path (relative or
absolute) instead of relying on built-in name resolution when running from
an installed wheel.
