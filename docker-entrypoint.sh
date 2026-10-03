#!/bin/sh
set -e

# PUID/PGID: run as this user/group instead of root, when the default
# (root) cannot write to a mounted volume — the common case being an NFS
# export with root_squash, which maps the container's root to an
# unprivileged "nobody" with no write access to it, even though the same
# share already works for every other container running as a normal user.
#
# Unset (the default) keeps today's behaviour exactly as it has always been:
# the process stays root, so an existing deployment on a plain bind mount
# changes nothing by not setting these.
if [ -n "$PUID" ] || [ -n "$PGID" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"

    getent group "$PGID" >/dev/null 2>&1 || groupadd -g "$PGID" appuser
    getent passwd "$PUID" >/dev/null 2>&1 || useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin appuser

    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
