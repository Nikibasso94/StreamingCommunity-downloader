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

    # Best-effort: hand ownership to PUID/PGID before switching to it, so
    # setting these two variables is normally the whole fix, with nothing to
    # go and chown by hand on the host.
    #
    # Only the directories themselves, never recursively into what they
    # already hold: VIDEOS_DIR can be a library of years of downloads, and
    # creating a new file only ever needs write access to the directory it
    # lands in, not to everything already sitting in the tree. The database
    # files are the one exception, because sqlite opens panel.db itself for
    # writing, not just the folder holding it — and there are only ever a
    # handful of them, never a reason to worry about recursing.
    #
    # Failures are swallowed on purpose. An NFS export with root_squash
    # refuses this exactly like it refuses everything else root attempts,
    # and that is a server-side policy to fix there, not something to fail
    # the whole startup over — the app still runs, that one mount just stays
    # as unwritable as it already was.
    for dir in "${VIDEOS_DIR:-videos}" "${TMP_DIR:-tmp}" \
               "$(dirname "${DB_FILE:-panel.db}")" "$(dirname "${DATA_FILE:-data.json}")" \
               "$(dirname "${SCHEDULE_FILE:-schedule.json}")"; do
        { [ -d "$dir" ] && chown "$PUID:$PGID" "$dir" 2>/dev/null; } || true
    done
    for file in "${DB_FILE:-panel.db}" "${DB_FILE:-panel.db}-wal" "${DB_FILE:-panel.db}-shm" \
                "${DATA_FILE:-data.json}" "${SCHEDULE_FILE:-schedule.json}"; do
        { [ -e "$file" ] && chown "$PUID:$PGID" "$file" 2>/dev/null; } || true
    done

    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
