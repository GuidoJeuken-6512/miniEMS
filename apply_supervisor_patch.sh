#!/bin/bash
# Applies supervisor devcontainer patches for docker_gateway_unprotected and unix socket issues.
# Called from postStartCommand in .devcontainer.json.
set -e

WS="${WORKSPACE_DIRECTORY:-$(pwd)}"
FIREWALL_PATCH="$WS/supervisor_firewall_patch.py"
API_PATCH="$WS/supervisor_api_patch.py"

echo "[patch] Waiting for hassio_supervisor container..."
for i in $(seq 1 30); do
    docker ps 2>/dev/null | grep -q hassio_supervisor && break
    sleep 2
done

if ! docker ps 2>/dev/null | grep -q hassio_supervisor; then
    echo "[patch] ERROR: hassio_supervisor not found after 60s"
    exit 1
fi

echo "[patch] Applying supervisor patches..."
docker cp "$FIREWALL_PATCH" hassio_supervisor:/usr/src/supervisor/supervisor/host/firewall.py
docker exec hassio_supervisor find /usr/src/supervisor/supervisor/host/__pycache__ -name "firewall*.pyc" -delete 2>/dev/null || true

docker cp "$API_PATCH" hassio_supervisor:/usr/src/supervisor/supervisor/homeassistant/api.py
docker exec hassio_supervisor find /usr/src/supervisor/supervisor/homeassistant/__pycache__ -name "api*.pyc" -delete 2>/dev/null || true

mkdir -p /run/os
ln -sf /run/supervisor/core.sock /run/os/core.sock 2>/dev/null || true

echo "[patch] Restarting supervisor with patched code..."
docker restart hassio_supervisor
echo "[patch] Done."
