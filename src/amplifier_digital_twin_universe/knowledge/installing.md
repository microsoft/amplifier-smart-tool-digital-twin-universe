# Installing the Digital Twin Universe prerequisites

## Prerequisites and what they are for

- **incus** (required). The container runtime that launches every Digital
  Twin Universe environment. Nothing runs without it.
- **docker** (optional). Runs Gitea sidecars and mock service sidecars
  alongside the Incus container. Only needed when a profile declares
  `mock_services` or uses the Gitea bundle. Basic Incus-only profiles do not
  need it.
- **git** (required). The model engine and provisioning steps clone the
  Amplifier foundation and any repos a profile references. Without it,
  provisioning fails partway through.
- **avahi** (optional, Linux and WSL2 only). Provides `avahi-publish-address`
  and `avahi-resolve-host-name` so a launched environment gets a `.local`
  mDNS hostname (e.g. `my-project.local:8410`) instead of falling back to
  `localhost`. Not supported on macOS or Windows; when absent, access URLs
  silently use `localhost`.

Install order matters: incus first, then git, then docker and avahi in
either order since neither depends on the other.

## Debian / Ubuntu (bare metal)

Install a current Incus release (7 LTS or newer) from the upstream Zabbly
packages rather than the distro package. The Incus shipped in the Ubuntu
24.04 archive is 6.0.0, which has an AppArmor bug that blocks Docker running
inside Incus and an image-unpack bug that breaks `incus publish`.

Add the Zabbly repo:
```bash
curl -fsSL https://pkgs.zabbly.com/key.asc | sudo gpg --dearmor -o /etc/apt/keyrings/zabbly.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/zabbly.gpg] \
  https://pkgs.zabbly.com/incus/stable $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/zabbly-incus.list
sudo apt update
```

If Ubuntu ESM is enabled it pins distro Incus 6.0.0 at apt priority 510, so a
plain `apt install incus` silently keeps the broken version. Pin the Zabbly
version explicitly across all three packages:
```bash
ZABBLY_VERSION=$(apt-cache madison incus | grep zabbly | head -1 | awk '{print $3}')
sudo apt install incus=$ZABBLY_VERSION incus-base=$ZABBLY_VERSION incus-client=$ZABBLY_VERSION
sudo systemctl restart incus
```
Verify:
```bash
incus version
```
Both a client and server version must print.

Add your user to the `incus-admin` group:
```bash
sudo adduser $USER incus-admin
```
Group membership does not propagate to processes already running, including
the current shell, tmux sessions, and any background service. `newgrp
incus-admin` fixes only the current shell; anything started before the group
change stays without the group until you log out and back in (or reboot).
Verify:
```bash
newgrp incus-admin
incus version
# expect: no "permissions" error
```

Initialize Incus with defaults. This step is required; `incus launch` fails
with `Error: not found` until it has run once:
```bash
sudo incus admin init --minimal
```
Verify:
```bash
incus launch images:ubuntu/24.04 test-incus
incus exec test-incus -- echo "hello from container"
incus delete test-incus --force
```
All three commands must succeed with no error output.

Install git:
```bash
sudo apt install -y git
```
Verify:
```bash
git --version
```

Install Docker (optional, only if the profile needs Gitea or mock
sidecars):
```bash
sudo apt update && sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")\nComponents: stable\nArchitectures: $(dpkg --print-architecture)\nSigned-By: /etc/apt/keyrings/docker.asc" | sudo tee /etc/apt/sources.list.d/docker.sources
sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```
Same group caveat as `incus-admin`: `newgrp docker` only affects the current
shell. Verify:
```bash
newgrp docker
docker run hello-world
```
Expect a pulled test image and a confirmation message.

If Docker is installed alongside Incus, it sets the kernel's iptables
`FORWARD` policy to `DROP`, which blocks Incus bridge traffic even though
both daemons report healthy. Apply the fix once:
```bash
echo '{"ip-forward-no-drop": true}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

Install Avahi (optional, for `.local` hostnames):
```bash
sudo apt install -y avahi-daemon avahi-utils
```
Verify:
```bash
which avahi-publish-address && echo "Avahi OK"
```

## WSL2 (Ubuntu 24.04+ on WSL2)

Everything above applies to WSL2 as well: Zabbly Incus install, `incus-admin`
group, `incus admin init --minimal`, git, and optional Docker and Avahi
follow the same steps and same verification commands.

WSL2 needs one thing plain Linux does not: after the Docker/Incus iptables
`FORWARD` fix, WSL2's network stack must be restarted at the Windows level,
not just the Linux service level. Run from PowerShell, not the WSL shell:
```powershell
wsl --shutdown
```
Then reopen the WSL terminal. Restarting only `docker` or `incus` inside
WSL2 is not enough; the NAT/masquerade rules WSL2 sets up for its own
virtual network are not refreshed until the WSL2 VM itself restarts.

WSL2 also loses Incus's nftables NAT rules more often than bare metal,
typically after `wsl --shutdown` or a host reboot. Containers can ping the
bridge gateway but cannot reach the internet. This is a runtime symptom, not
an install-step gap; see troubleshooting.md for the repair.

## macOS

macOS is not a supported platform for this tool. The Incus server is
Linux-only; on macOS, Incus runs inside a Colima VM and is reached over a
forwarded unix socket, with its own set of VM-provisioning, uid/gid, and
Docker-Desktop-bridge caveats. If you need Incus on macOS anyway, route
through Colima (`brew install incus colima`, then `colima start --runtime
incus`) and follow the upstream Incus and Colima install docs directly;
this tool does not diagnose or install against that path.

## Recommended Incus version

Install Incus 7 LTS or newer rather than a distro-pinned older release. Two
known bugs affect Digital Twin Universe use and are both fixed upstream: an
AppArmor bug that blocks Docker running inside Incus (fixed in 6.0.6 LTS /
6.19) and an image-unpack bug that breaks `incus publish` (fixed in 6.0.1).
An existing older install that already works for your profiles does not
need to be upgraded preemptively; upgrade only after hitting one of these
two failure modes.
