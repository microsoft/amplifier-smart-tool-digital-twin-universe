#!/bin/sh
# Bring up the universe's Gitea and publish every repository mounted under /repos at /dtu/<name>.
#
# The host checkout is mounted read only and copied before anything is committed, so the working tree is served
# exactly as it is, uncommitted changes included, and the host repository is never touched.
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
    rm -rf "/work/$name"
    cp -a "$source" "/work/$name"
    cd "/work/$name"
    git config user.email dtu@digital-twin-universe.invalid
    git config user.name "Digital Twin Universe"
    git add -A
    # --no-verify because the checkout's own hooks are the developer's, and a hook that cannot run here, such as
    # one calling a tool this container does not have, would otherwise silently cost the working tree.
    # Nothing to commit is the ordinary case: a clean checkout is already what the profile means to serve.
    git commit -q --no-verify -m "the working tree as it was at launch" || true
    branch=$(git rev-parse --abbrev-ref HEAD)
    [ "$branch" = HEAD ] && branch=detached
    remote="http://dtu:$DTU_PASSWORD@127.0.0.1:3000/dtu/$name.git"
    if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
        # Gitea refuses history that ends in missing parents, as a CI checkout's does, so a shallow checkout is
        # served as its working tree alone, on one parentless commit.
        snapshot=$(git commit-tree "HEAD^{tree}" -m "the working tree as it was at launch")
        git push -q --force "$remote" "$snapshot:refs/heads/$branch"
    else
        # The checked-out branch goes first so it becomes the default, then every other local branch and tag,
        # since a consumer pinning `@main` while the developer sits on a fix branch still has to find `main`.
        git push -q --force "$remote" "HEAD:refs/heads/$branch"
        git push -q --force "$remote" "refs/heads/*:refs/heads/*" "refs/tags/*:refs/tags/*" || true
    fi
    echo "digital-twin-universe: serving /dtu/$name at branch $branch"
done

wait
