# Installing Docker

Docker is optional. It runs mock service sidecars. Without it, everything works
except profiles that declare sidecars, which cannot launch.

Follow the upstream installation guide:
https://docs.docker.com/engine/install/

After installing, confirm your user can reach the daemon without `sudo`:

- `docker info`

If that prints daemon details, the optional prerequisite is satisfied.
