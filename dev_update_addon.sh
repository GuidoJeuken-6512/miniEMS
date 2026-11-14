#!/usr/bin/env bash
# Build and update the local miniEMS add-on inside the devcontainer.
#
# Since v3.6.0 miniems/config.yaml names a prebuilt image on ghcr.io, so the
# supervisor *pulls* instead of building – which fails (404) for a version
# that is not released yet. This script hides the `image:` line from the
# supervisor for the duration of the update – a bind mount inside the
# hassio_supervisor container only (mounts made in the devcontainer do not
# propagate into it); the file on the host is untouched – lets it build from
# the Dockerfile, then restores the original view.
#
# Usage (in the devcontainer):  bash dev_update_addon.sh
# See docs/technical/devcontainer-supervisor-patches.md, section 6.
set -euo pipefail

CFG="/data/apps/local/miniEMS/miniems/config.yaml"   # supervisor's view
DEV_CFG="/tmp/miniems-config.dev.yaml"                # inside hassio_supervisor
SLUG="local_miniems"

sup() { docker exec hassio_supervisor sh -c "$1"; }
# Supervisor API with a long timeout – a store reload pulls every add-on git
# repository and regularly exceeds the `ha` CLI's own timeout.
api() {
    docker exec hassio_cli sh -c "curl -sS -m 1800 -X $1 \
        -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" http://supervisor$2"
}

is_mounted() { sup "grep -q ' $CFG ' /proc/mounts"; }
cleanup() {
    if is_mounted; then sup "umount '$CFG'" || true; fi
    api POST /store/reload >/dev/null 2>&1 || true
}
trap cleanup EXIT

if is_mounted; then sup "umount '$CFG'"; fi
sup "grep -v '^image:' '$CFG' > '$DEV_CFG' && mount --bind '$DEV_CFG' '$CFG'"
echo "image: hidden – supervisor builds locally ($(sup "grep '^version' '$CFG'"))"

echo "Reloading the store (can take a minute)..."
api POST /store/reload; echo
if api GET "/addons/$SLUG/info" | grep -q '"update_available":true'; then
    echo "Updating $SLUG (local build, takes a few minutes)..."
    api POST "/store/addons/$SLUG/update"; echo
else
    echo "Same version – rebuilding $SLUG..."
    api POST "/addons/$SLUG/rebuild"; echo
fi
api GET "/addons/$SLUG/info" | grep -oE '"(version|state)":"[^"]*"'
