#!/bin/sh
# Bring up the universe's Gitea and publish every repository mounted under /repos at /dtu/<name>.
#
# Each mount is a self-contained bare snapshot exported by the host. Original checkout metadata and hooks
# never enter the container, including when the source is a linked worktree.
#
# The database and the owning user are prepared before the server starts, so nothing contends with it for SQLite,
# and the repositories are made by pushing to them, which Gitea's push-to-create turns into a repository.
set -e

git config --global --add safe.directory '*'
mkdir -p /data/gitea/conf /work

# The image's own entrypoint does this and then hands to s6; this file stands in for both, so the GITEA__ variables
# still become configuration and `gitea` still runs as the user that owns /data.
environment-to-ini --config /data/gitea/conf/app.ini
chown -R git:git /data

su-exec git gitea migrate
su-exec git gitea admin user create --username dtu --password "$DTU_PASSWORD" \
    --email dtu@digital-twin-universe.invalid --admin --must-change-password=false || true

su-exec git gitea web &

until wget -q -O /dev/null http://127.0.0.1:3000/api/healthz; do sleep 1; done

for source in /repos/*/; do
    name=$(basename "$source")
    branch=$(git --git-dir="$source" symbolic-ref --short HEAD)
    remote="http://dtu:$DTU_PASSWORD@127.0.0.1:3000/dtu/$name.git"
    git --git-dir="$source" -c core.hooksPath=/dev/null push --no-verify -q --force "$remote" "HEAD:refs/heads/$branch"
    git --git-dir="$source" -c core.hooksPath=/dev/null push --no-verify -q --force "$remote" "refs/heads/*:refs/heads/*" "refs/tags/*:refs/tags/*"
    echo "digital-twin-universe: serving /dtu/$name at branch $branch"
done

wait
